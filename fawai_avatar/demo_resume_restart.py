# term:进程中断续跑  doc:doc/测试/进程中断续跑/说明.md
"""回归：检查点续跑 — 模拟进程死后从断点继续，不整任务重开。"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from typing import Any

from fawai_avatar.channel.hub import SessionHub
from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.checkpoint import (
    CHECKPOINT_KEY,
    dump_checkpoint,
    load_checkpoint,
)
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action, Observation, Plan


class _CountingBrain:
    """前两步 continue，之后 success；记录每次 history 长度。"""

    def __init__(self, *, success_after: int = 3) -> None:
        self.plan_calls = 0
        self.history_lens: list[int] = []
        self.success_after = success_after

    def plan(
        self,
        goal: str,
        observation: Observation,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> Plan:
        self.plan_calls += 1
        self.history_lens.append(len(history or []))
        if self.plan_calls >= self.success_after:
            return Plan(
                thought="done after resume",
                status="success",
                done=True,
                actions=[],
            )
        return Plan(
            thought=f"step-{self.plan_calls}",
            status="continue",
            done=False,
            actions=[
                Action(
                    kind="wait",
                    args={"seconds": 0.02},
                    needs_foreground=False,
                )
            ],
        )


def test_checkpoint_continue(data_dir: str) -> None:
    """种入检查点后 run：须走 resumed，且首轮 history 非空（非从零重跑）。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    sup = Supervisor(data_dir)
    tid = sup.create_task("resume-checkpoint-goal")
    assert sup.set_running(tid)

    prior_history = [
        {"role": "assistant", "content": "already-did-step-0"},
        {"role": "user", "content": "工具结果：ok"},
    ]
    ckpt = dump_checkpoint(
        {
            "run_id": "run-demo-1",
            "next_step": 1,
            "history": prior_history,
            "traces": [],
            "replan_count": 0,
            "reflect_count": 0,
            "stagnation_count": 0,
            "fingerprint_fails": {},
            "prior_alternate_fps": [],
            "step_fp_counts": {},
            "prior_judged_step_fps": [],
            "verify_results": [],
            "write_set": [],
            "saw_code_check_ok": False,
            "awaiting_user_answer": False,
            "pending_observe_kinds": None,
            "last_reflection": None,
        }
    )
    task = sup.get_task(tid)
    assert task is not None
    meta = dict(task.meta)
    meta[CHECKPOINT_KEY] = ckpt
    assert sup.replace_task_meta(tid, meta)

    brain = _CountingBrain(success_after=2)
    phases: list[str] = []

    def on_progress(text: str, meta: dict[str, Any]) -> None:
        phase = str(meta.get("phase") or "")
        if phase:
            phases.append(phase)

    loop = WorkerLoop(
        sup,
        brain=brain,  # type: ignore[arg-type]
        controller=Controller(dry_run=True, supervisor=sup),
        max_steps=10,
        capture=False,
        on_progress=on_progress,
    )
    out = loop.run(tid)
    assert out.get("ok") is True, out
    assert "resumed" in phases, phases
    assert "started" not in phases, phases
    assert brain.history_lens, "never planned"
    assert brain.history_lens[0] >= len(prior_history), (
        brain.history_lens,
        len(prior_history),
    )
    task = sup.get_task(tid)
    assert task is not None and task.status == "completed"
    assert CHECKPOINT_KEY not in (task.meta or {})
    print(
        {
            "ok": True,
            "case": "checkpoint_continue",
            "hist0": brain.history_lens[0],
            "calls": brain.plan_calls,
        }
    )


def test_checkpoint_saved_during_run(data_dir: str) -> None:
    """真实跑一步后应落检查点；放行第二步后完成并清除。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    import threading

    sup = Supervisor(data_dir)
    gate = threading.Event()

    class _GatedBrain(_CountingBrain):
        def plan(self, goal, observation, *, history=None):  # type: ignore[no-untyped-def]
            # 第一步自由跑；第二步起卡住，方便主线程看到检查点
            if self.plan_calls >= 1:
                gate.wait(timeout=5.0)
            return super().plan(goal, observation, history=history)

    brain = _GatedBrain(success_after=2)
    tid = sup.create_task("save-ckpt")
    loop = WorkerLoop(
        sup,
        brain=brain,  # type: ignore[arg-type]
        controller=Controller(dry_run=True, supervisor=sup),
        max_steps=10,
        capture=False,
    )

    box: dict[str, Any] = {}

    def _run() -> None:
        box["out"] = loop.run(tid)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    seen_ckpt = False
    deadline = time.time() + 5.0
    while time.time() < deadline:
        task = sup.get_task(tid)
        if task and load_checkpoint(task.meta) is not None and brain.plan_calls >= 1:
            seen_ckpt = True
            break
        time.sleep(0.05)
    gate.set()
    th.join(timeout=8.0)
    assert seen_ckpt, "checkpoint never appeared mid-run"
    assert box.get("out", {}).get("ok") is True, box
    task = sup.get_task(tid)
    assert task is not None and task.status == "completed"
    assert load_checkpoint(task.meta) is None
    print({"ok": True, "case": "checkpoint_saved_during_run"})


def test_hub_recover_and_no_double(data_dir: str) -> None:
    """启动 recover 拉起孤儿 running；同进程 pause/resume 不双开。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    import threading

    sup = Supervisor(data_dir)
    runs: list[str] = []
    hold = threading.Event()

    class _HoldBrain(_CountingBrain):
        def plan(self, goal, observation, *, history=None):  # type: ignore[no-untyped-def]
            # 首步后阻塞，保证 pause/resume 时线程仍活
            if self.plan_calls >= 1:
                hold.wait(timeout=8.0)
            return super().plan(goal, observation, history=history)

    def worker(task_id: str) -> dict[str, Any]:
        runs.append(task_id)
        goal_task = sup.get_task(task_id)
        goal = (goal_task.goal if goal_task else "") or ""
        if "live-pause" in goal:
            b: Any = _HoldBrain(success_after=3)
        else:
            b = _CountingBrain(success_after=2)
        loop = WorkerLoop(
            sup,
            brain=b,
            controller=Controller(dry_run=True, supervisor=sup),
            max_steps=8,
            capture=False,
        )
        return loop.run(task_id)

    hub = SessionHub(sup, worker=worker, auto_run_worker=True)
    tid = sup.create_task("orphan-running")
    assert sup.set_running(tid)
    started = hub.recover_interrupted_tasks()
    assert tid in started, started
    deadline = time.time() + 5.0
    while time.time() < deadline:
        task = sup.get_task(tid)
        if task and task.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    task = sup.get_task(tid)
    assert task is not None and task.status == "completed", task

    tid2 = sup.create_task("live-pause-resume")

    async def _flow() -> None:
        await hub._maybe_start_worker(tid2)
        deadline2 = time.time() + 5.0
        while time.time() < deadline2:
            t = sup.get_task(tid2)
            if t and t.status == "running" and hub.has_live_worker(tid2):
                break
            await asyncio.sleep(0.05)
        assert hub.has_live_worker(tid2), "worker died before pause window"
        assert not await hub._maybe_start_worker(tid2)
        assert sup.pause(tid2)
        await asyncio.sleep(0.3)
        assert hub.has_live_worker(tid2)
        assert sup.resume(tid2)
        assert not await hub._maybe_start_worker(tid2)
        hold.set()
        deadline3 = time.time() + 8.0
        while time.time() < deadline3:
            t = sup.get_task(tid2)
            if t and t.status in {"completed", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.05)

    asyncio.run(_flow())
    t2 = sup.get_task(tid2)
    assert t2 is not None and t2.status == "completed", t2
    # 等 Worker 线程退出，避免 TemporaryDirectory 删库后 _finish 再写
    deadline_join = time.time() + 3.0
    while time.time() < deadline_join:
        with hub._lock:
            alive = any(t.is_alive() for t in hub._worker_threads.values())
        if not alive:
            break
        time.sleep(0.05)
    print({"ok": True, "case": "hub_recover_and_no_double", "runs": len(runs)})


def test_worker_crash_marks_failed(data_dir: str) -> None:
    """Worker 抛异常须 fail 任务并发终态卡，禁止卡在 running。"""
    import sqlite3
    import threading

    from fawai_avatar.channel.message import Message

    sup = Supervisor(data_dir)
    outbox: list[Message] = []

    def worker(task_id: str) -> dict[str, Any]:
        assert sup.set_running(task_id)
        raise sqlite3.OperationalError("unable to open database file")

    hub = SessionHub(sup, worker=worker, auto_run_worker=False)

    def _broadcast(msg: Message) -> None:
        outbox.append(msg)

    hub.add_broadcaster(_broadcast)
    tid = sup.create_task("crash-db-open")
    thread = threading.Thread(
        target=hub._run_worker_safe,
        args=(tid,),
        name=f"fawai-worker-{tid[:8]}",
        daemon=True,
    )
    with hub._lock:
        hub._worker_threads[tid] = thread
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    task = sup.get_task(tid)
    assert task is not None and task.status == "failed", task
    kinds = {m.kind for m in outbox}
    assert "error" in kinds
    assert "card" in kinds
    assert any(
        m.kind == "text" and "任务库无法打开" in (m.text or "") for m in outbox
    ), [(m.kind, (m.text or "")[:80]) for m in outbox]
    print({"ok": True, "case": "worker_crash_marks_failed"})


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fawai-resume-") as tmp:
        test_checkpoint_continue(tmp)
    with tempfile.TemporaryDirectory(prefix="fawai-resume-save-") as tmp:
        test_checkpoint_saved_during_run(tmp)
    with tempfile.TemporaryDirectory(prefix="fawai-resume-hub-") as tmp:
        test_hub_recover_and_no_double(tmp)
    with tempfile.TemporaryDirectory(prefix="fawai-resume-crash-") as tmp:
        test_worker_crash_marks_failed(tmp)
    print("demo_resume_restart ok")


if __name__ == "__main__":
    main()
