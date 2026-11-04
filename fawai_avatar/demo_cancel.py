# term:任务与事件管理  doc:doc/测试/系统/取消停步/说明.md
"""回归：执行中取消后，工作任务须停止后续动作，状态保持 cancelled。"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from typing import Any

from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action, Observation, Plan


class _SlowBrain:
    """每步睡一会，便于并发 cancel；永远 continue，避免自然结束。"""

    def __init__(self, *, sleep_s: float = 0.35) -> None:
        self.sleep_s = sleep_s
        self.plan_calls = 0
        self.act_batches = 0

    def plan(
        self,
        goal: str,
        observation: Observation,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> Plan:
        self.plan_calls += 1
        time.sleep(self.sleep_s)
        self.act_batches += 1
        return Plan(
            thought=f"step-{self.plan_calls}",
            status="continue",
            done=False,
            actions=[
                Action(
                    kind="wait",
                    args={"seconds": 0.05},
                    needs_foreground=False,
                )
            ],
        )


def main(*, data_dir: str) -> None:
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    sup = Supervisor(data_dir)
    brain = _SlowBrain(sleep_s=0.4)
    phases: list[str] = []

    def on_progress(text: str, meta: dict[str, Any]) -> None:
        phase = str(meta.get("phase") or "")
        if phase:
            phases.append(phase)

    loop = WorkerLoop(
        sup,
        brain=brain,  # type: ignore[arg-type]
        controller=Controller(dry_run=True, supervisor=sup),
        max_steps=20,
        capture=False,
        on_progress=on_progress,
    )
    tid = sup.create_task("cancel-while-running")

    result_box: dict[str, Any] = {}

    def _run() -> None:
        result_box["out"] = loop.run(tid)

    worker = threading.Thread(target=_run, name="cancel-demo-worker", daemon=True)
    worker.start()

    # 等到进入执行后再取消
    deadline = time.time() + 5.0
    while time.time() < deadline:
        task = sup.get_task(tid)
        if task and task.status == "running" and brain.plan_calls >= 1:
            break
        time.sleep(0.05)
    assert brain.plan_calls >= 1, "worker never planned"
    assert sup.cancel(tid), "cancel failed"
    assert (sup.get_task(tid) or None) and sup.get_task(tid).status == "cancelled"

    worker.join(timeout=8.0)
    assert not worker.is_alive(), "worker did not stop after cancel"
    out = result_box.get("out") or {}
    assert out.get("error") == "cancelled", out
    assert out.get("ok") is False, out
    task = sup.get_task(tid)
    assert task is not None and task.status == "cancelled", task
    # 取消后不应继续烧满步数
    assert brain.plan_calls < 20, f"still planning after cancel: {brain.plan_calls}"
    assert "cancelled" in phases, phases
    print(
        {
            "ok": True,
            "plan_calls": brain.plan_calls,
            "status": task.status,
            "error": out.get("error"),
            "phases": phases[-8:],
        }
    )


if __name__ == "__main__":
    DATA_DIR = tempfile.mkdtemp(prefix="fawai_cancel_")
    print("DATA_DIR=", DATA_DIR)
    main(data_dir=DATA_DIR)
