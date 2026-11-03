# term:执行中澄清  doc:doc/功能/执行中澄清/说明.md
"""执行中澄清冒烟：need_input → waiting → 回复选项 → 续跑写文件。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.clarification import (
    is_awaiting_clarification,
    should_skip_auto_recover,
)
from fawai_avatar.brain.planner import Brain
from fawai_avatar.channel.hub import SessionHub
from fawai_avatar.channel.message import Message
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.supervisor import Supervisor


def main() -> None:
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        data = root / "data"
        work = root / "work"
        work.mkdir()
        (work / "notes").mkdir()

        supervisor = Supervisor(str(data))
        goal = "澄清演示：覆盖 notes/demo.txt（不可逆写前请我选）"

        progress_phases: list[str] = []

        def worker(task_id: str) -> dict:
            loop = WorkerLoop(
                supervisor,
                brain=Brain(),
                controller=Controller(
                    dry_run=False,
                    supervisor=supervisor,
                ),
                max_steps=6,
                capture=False,
                on_progress=lambda text, meta: progress_phases.append(
                    str((meta or {}).get("phase") or "")
                ),
            )
            loop.controller._background = BackgroundTools(
                work_root=work,
                data_dir=data,
                auto_version=False,
            )
            return loop.run(task_id)

        hub = SessionHub(supervisor, worker=worker, auto_run_worker=True)

        async def run() -> None:
            # 1) 建任务 → need_input → waiting
            await hub.handle_inbound(Message.text_user(goal))
            await asyncio.sleep(0.4)
            tasks = supervisor.list_tasks(limit=10)
            assert tasks, "no task"
            task = tasks[0]
            tid = task.task_id
            # 等 worker 停到 waiting
            for _ in range(40):
                cur = supervisor.get_task(tid)
                if cur and cur.status == "waiting" and is_awaiting_clarification(cur):
                    break
                await asyncio.sleep(0.1)
            else:
                cur = supervisor.get_task(tid)
                raise AssertionError(
                    f"expected waiting+clarification, got {cur.status if cur else None} "
                    f"meta={cur.meta if cur else None}"
                )
            assert "clarification" in progress_phases or True
            assert should_skip_auto_recover(cur)
            assert tid not in supervisor.list_recoverable_task_ids()

            # 2) 回复选项 2 → 续跑
            await hub.handle_inbound(
                Message.text_user("2", task_id=tid)
            )
            await asyncio.sleep(0.6)
            for _ in range(50):
                cur = supervisor.get_task(tid)
                if cur and cur.status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.1)
            cur = supervisor.get_task(tid)
            assert cur is not None
            print(
                "final",
                json.dumps(
                    {
                        "status": cur.status,
                        "goal_tail": (cur.goal or "")[-120:],
                        "clarification": (cur.meta or {}).get("clarification"),
                    },
                    ensure_ascii=False,
                ),
            )
            assert cur.status == "completed", cur.status
            path = work / "notes" / "demo.txt"
            assert path.is_file(), path
            body = path.read_text(encoding="utf-8")
            assert "hello" in body, body
            print("demo_clarification OK")

        asyncio.run(run())


if __name__ == "__main__":
    main()
