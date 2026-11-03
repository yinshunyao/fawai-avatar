# term:操作分身  doc:doc/功能/【总表】功能一览.md
"""本地冒烟：总管 + mock 大脑 + dry_run 控制（默认不截屏）。"""

from __future__ import annotations

import json
import os
import tempfile

from fawai_avatar.brain.planner import Brain
from fawai_avatar.control.executor import Controller
from fawai_avatar.llm.client import LlmClient
from fawai_avatar.observe.screen import observe
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.models import ResourceItem
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action, Observation


def main(
    *,
    data_dir: str,
    dry_run: bool,
    mock_brain: bool,
    do_capture: bool,
) -> None:
    if mock_brain:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"

    print("== LLM 接入 ==")
    llm = LlmClient()
    print({"has_key": bool(llm.config.api_key), "model": llm.config.model})

    print("== 任务与事件 / 总管租约 ==")
    sup = Supervisor(data_dir)
    t1 = sup.create_task("smoke-foreground")
    t2 = sup.create_task("smoke-resource")
    assert sup.set_running(t1) and sup.set_running(t2)
    assert sup.claim_foreground(t1)
    assert not sup.claim_foreground(t2)
    assert sup.get_task(t2).status == "blocked"
    sup.release_foreground(t1)
    shared = os.path.join(data_dir, "shared.txt")
    with open(shared, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert sup.set_running(t2)
    assert sup.claim_resources(t1, [ResourceItem("file", shared, "write")])
    assert not sup.claim_resources(t2, [ResourceItem("file", shared, "write")])
    print("lease checks ok")

    print("== TaskStore 连接不泄漏 FD ==")
    import subprocess
    from fawai_avatar.task.store import TaskStore

    store = TaskStore(data_dir)
    db_name = os.path.basename(store.db_path)

    def _sqlite_fd_count() -> int:
        try:
            out = subprocess.check_output(
                ["lsof", "-n", "-p", str(os.getpid())],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return -1
        return sum(1 for line in out.splitlines() if db_name in line)

    before = _sqlite_fd_count()
    for _ in range(80):
        store.list_tasks(limit=5)
    after = _sqlite_fd_count()
    if before >= 0 and after >= 0:
        assert after - before <= 2, f"sqlite FD leaked: before={before} after={after}"
        print(f"sqlite fd stable ({before} → {after})")
    else:
        print("lsof unavailable; skip fd check")

    print("== 观察 ==")
    if do_capture:
        obs = observe(os.path.join(data_dir, "observe"))
        print(obs.summary)
    else:
        obs = Observation(summary="smoke: capture skipped")
        print(obs.summary)

    print("== 大脑 ==")
    plan = Brain().plan("冒烟目标", obs)
    print(plan.to_dict())

    print("== 控制 ==")
    ctrl = Controller(dry_run=dry_run, supervisor=sup)
    assert sup.set_running(t1)
    assert sup.claim_foreground(t1)
    r = ctrl.execute(
        Action(kind="click", args={"x": 1, "y": 2}, needs_foreground=True),
        task_id=t1,
    )
    print(r.to_dict())
    r2 = ctrl.execute(
        Action(kind="click", args={"x": 1, "y": 2}, needs_foreground=True),
        task_id=t2,
    )
    assert not r2.ok
    print("gate reject ok", r2.message)

    print("== 工作任务闭环 ==")
    loop = WorkerLoop(
        sup,
        brain=Brain(),
        controller=Controller(dry_run=dry_run, supervisor=sup),
        max_steps=2,
        capture=False,
    )
    tid = sup.create_task("闭环冒烟")
    out = loop.run(tid)
    # mock brain never sets done → max_steps → fail expected unless we force done
    print(json.dumps({k: out[k] for k in out if k != "steps"}, ensure_ascii=False))
    print("smoke done")


if __name__ == "__main__":
    DATA_DIR = tempfile.mkdtemp(prefix="fawai_smoke_")
    DRY_RUN = True
    MOCK_BRAIN = True
    DO_CAPTURE = False  # True 时会截屏
    print("DATA_DIR=", DATA_DIR)
    main(data_dir=DATA_DIR, dry_run=DRY_RUN, mock_brain=MOCK_BRAIN, do_capture=DO_CAPTURE)
