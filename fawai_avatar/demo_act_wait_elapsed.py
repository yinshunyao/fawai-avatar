# term:会话通道  doc:doc/功能/会话通道/说明.md
"""工具执行心跳与耗时口径冒烟（不调 LLM）。"""
from __future__ import annotations

import time
from typing import Any

from fawai_avatar.runtime.api_wait import ApiWaitProgress


def main() -> None:
    events: list[dict[str, Any]] = []

    def emit(text: str, *, phase: str, **extra: Any) -> None:
        events.append({"text": text, "phase": phase, **extra})

    waiter = ApiWaitProgress(
        emit,
        phase="act",
        step=1,
        waiting_text="执行中：fs_grep pattern=max_epochs",
        interval_sec=0.2,
        mark_api_wait=False,
        tool="fs_grep",
    )
    waiter.start()
    time.sleep(1.15)
    waiter.stop()

    assert events, "应有心跳事件"
    assert events[0]["phase"] == "act"
    assert events[0].get("api_wait") is not True, "本机长工具不得标 api_wait"
    assert "wait_elapsed_sec" in events[0]
    assert any(
        int(e.get("wait_elapsed_sec") or 0) >= 1 for e in events
    ), events
    assert any("已等待" in str(e.get("text") or "") for e in events), events

    # 收口耗时口径：动作墙钟 + 观察格式化，禁止只用格式化毫秒
    act_ms = 240_000
    obs_ms = 3
    tool_card_ms = max(0, act_ms) + max(0, obs_ms)
    assert tool_card_ms == 240_003
    assert tool_card_ms > obs_ms

    print("demo_act_wait_elapsed: ok", {"events": len(events), "tool_card_ms": tool_card_ms})


if __name__ == "__main__":
    main()
