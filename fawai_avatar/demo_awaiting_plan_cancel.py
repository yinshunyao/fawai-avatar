# term:复杂任务计划模式  doc:doc/功能/复杂任务计划模式/说明.md
# term:会话通道  doc:doc/功能/会话通道/说明.md
"""取消编排中任务后须清 awaiting_plan，续跑不得再卡「仍在编排」。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fawai_avatar.brain.plan_scheduler import (
    META_AWAITING_PLAN,
    clear_awaiting_plan,
    is_awaiting_plan,
    release_awaiting_plan_if_terminal,
)
from fawai_avatar.channel.hub import SessionHub
from fawai_avatar.channel.message import Message
from fawai_avatar.task.supervisor import Supervisor


def main() -> None:
    import os

    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data = Path(tmp) / "data"
        supervisor = Supervisor(str(data))
        hub = SessionHub(supervisor, auto_run_worker=False)

        tid = supervisor.create_task("梳理一个演示 PPT")
        supervisor.merge_task_meta(
            tid,
            {META_AWAITING_PLAN: True, "title": "演示 PPT"},
        )
        assert is_awaiting_plan(supervisor.get_task(tid).meta)

        # 取消只改 status 时 latch 仍在；release 应对称清掉
        assert supervisor.cancel(tid)
        assert supervisor.get_task(tid).status == "cancelled"
        assert is_awaiting_plan(supervisor.get_task(tid).meta)
        assert release_awaiting_plan_if_terminal(supervisor, tid)
        assert not is_awaiting_plan(supervisor.get_task(tid).meta)

        # 模拟旧坏数据：终态 + latch；dispatch 须清闸并走出追问续跑，而非「仍在编排」
        supervisor.merge_task_meta(tid, {META_AWAITING_PLAN: True})
        assert is_awaiting_plan(supervisor.get_task(tid).meta)

        async def run() -> None:
            out = await hub.handle_inbound(
                Message.text_user("继续任务", task_id=tid)
            )
            assert not (
                out.kind == "progress"
                and (out.meta or {}).get("phase") == "orchestrate"
                and "仍在编排" in (out.text or "")
            ), f"终态不应再发编排闸门: {out.kind} {out.text!r} {(out.meta or {})}"
            assert out.kind == "card" and (out.meta or {}).get("mode") == "continue", (
                f"期望追问续跑卡，得到 {out.kind} {(out.meta or {})}"
            )
            cur = supervisor.get_task(tid)
            assert cur is not None
            assert not is_awaiting_plan(cur.meta), "dispatch 后 latch 应已清"
            # 续跑 reopen → pending
            assert cur.status in {"pending", "running"}, f"期望续跑，得到 {cur.status}"

        asyncio.run(run())

        # clear 幂等
        assert clear_awaiting_plan(supervisor, tid) is False
        print("demo_awaiting_plan_cancel: ok")


if __name__ == "__main__":
    main()
