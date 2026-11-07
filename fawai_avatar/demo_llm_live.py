# term:会话通道  doc:doc/功能/会话通道/说明.md
"""无网自检：LLM 直播节流 + 流式字段归一。"""
from __future__ import annotations

from fawai_avatar.llm.client import _delta_reasoning, _message_reasoning, _tool_calls_from_acc
from fawai_avatar.runtime.llm_live import LlmLiveProgress


def _ok(name: str, cond: bool) -> None:
    if not cond:
        raise SystemExit(f"FAIL: {name}")
    print(f"ok {name}")


class _Obj:
    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def main() -> None:
    emitted: list[dict] = []

    def emit(text: str, *, phase: str, step: int, **extra: object) -> None:
        emitted.append({"text": text, "phase": phase, "step": step, **extra})

    live = LlmLiveProgress(emit, phase="plan", step=1, min_interval_ms=1, min_chars=1)
    live.on_delta({"thinking": "先看配置", "content": "", "model": "qwen3.7-plus"})
    live.on_delta(
        {
            "thinking": "先看配置再读文件",
            "content": '{"status":',
            "model": "qwen3.7-plus",
            "thinking_meta": {"enabled": True, "budget": 512},
        }
    )
    live.flush()
    _ok("emitted", len(emitted) >= 1)
    last = emitted[-1]
    _ok("live_flag", last.get("live") is True)
    _ok("thinking_text", "先看配置再读文件" in str(last.get("thinking_text") or ""))
    _ok("content_text", '{"status":' in str(last.get("content_text") or ""))
    _ok("phase", last.get("phase") == "plan")

    delta = _Obj(reasoning_content="深度…", content=None)
    _ok("delta_reasoning", _delta_reasoning(delta) == "深度…")
    msg = _Obj(reasoning_content="整段思考", content="hi")
    _ok("message_reasoning", _message_reasoning(msg) == "整段思考")

    acc = {
        0: {"id": "c1", "name": "fs_read", "arguments": '{"path":"/a"}'},
    }
    tools = _tool_calls_from_acc(acc)
    _ok("tool_name", tools[0]["name"] == "fs_read")
    print("demo_llm_live: all passed")


if __name__ == "__main__":
    main()
