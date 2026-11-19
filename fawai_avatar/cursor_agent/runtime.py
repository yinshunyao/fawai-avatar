# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""cursor-sdk 同步封装；未安装时优雅降级。"""
from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any

OnEvent = Callable[[dict[str, Any]], None]

# httpx trust_env 会吃 macOS 系统代理；本机 Clash 等对 127.0.0.1:bridge 常回空 502。
_LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")


def ensure_loopback_no_proxy() -> str:
    """保证 NO_PROXY/no_proxy 含回环地址，避免 SDK→Bridge 被系统 HTTP 代理劫持。"""
    merged: list[str] = []
    seen: set[str] = set()
    for key in ("NO_PROXY", "no_proxy"):
        raw = str(os.environ.get(key) or "").strip()
        if not raw:
            continue
        for part in raw.split(","):
            item = part.strip()
            if not item:
                continue
            low = item.lower()
            if low in seen:
                continue
            seen.add(low)
            merged.append(item)
    for host in _LOOPBACK_NO_PROXY:
        if host.lower() not in seen:
            merged.append(host)
            seen.add(host.lower())
    value = ",".join(merged)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    return value


def sdk_available() -> bool:
    try:
        importlib.import_module("cursor_sdk")
        return True
    except ImportError:
        return False


def sdk_import_error() -> str:
    try:
        importlib.import_module("cursor_sdk")
        return ""
    except ImportError as exc:
        return str(exc) or "cursor-sdk not installed"


def normalize_model_entry(raw: Any) -> dict[str, Any] | None:
    """把 SDK 模型对象 / dict 压成前端可用条目。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        mid = str(raw.get("id") or raw.get("model") or "").strip()
        if not mid:
            return None
        label = str(raw.get("display_name") or raw.get("name") or raw.get("label") or mid)
        return {
            "id": mid,
            "label": label,
            "display_name": label,
        }
    mid = str(getattr(raw, "id", None) or getattr(raw, "model", None) or "").strip()
    if not mid:
        return None
    label = str(
        getattr(raw, "display_name", None)
        or getattr(raw, "name", None)
        or getattr(raw, "label", None)
        or mid
    )
    return {"id": mid, "label": label, "display_name": label}


def normalize_mode(raw: Any) -> str:
    """SDK AgentModeOption：仅 agent / plan。"""
    val = str(raw or "").strip().lower()
    if val in {"plan", "agent"}:
        return val
    return "agent"


def fallback_models() -> list[dict[str, Any]]:
    """Bridge 失败时仍可选手动常用项。"""
    return [
        {"id": "auto", "label": "Auto (default)", "display_name": "Auto (default)"},
        {"id": "composer-2.5", "label": "Composer 2.5", "display_name": "Composer 2.5"},
        {"id": "composer-2", "label": "Composer 2", "display_name": "Composer 2"},
        {"id": "gpt-5.4", "label": "GPT-5.4", "display_name": "GPT-5.4"},
        {"id": "claude-4.6-sonnet", "label": "Claude 4.6 Sonnet", "display_name": "Claude 4.6 Sonnet"},
        {"id": "claude-4.6-opus", "label": "Claude 4.6 Opus", "display_name": "Claude 4.6 Opus"},
    ]


def _ensure_common_models(out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {str(m.get("id") or "") for m in out}
    if "auto" not in seen:
        out.insert(0, {"id": "auto", "label": "Auto (default)", "display_name": "Auto (default)"})
        seen.add("auto")
    if "composer-2.5" not in seen:
        out.append(
            {
                "id": "composer-2.5",
                "label": "Composer 2.5",
                "display_name": "Composer 2.5",
            }
        )
    return out


def list_models(*, api_key: str = "") -> list[dict[str, Any]]:
    if not sdk_available():
        raise RuntimeError(f"cursor-sdk 未安装：{sdk_import_error()}")
    ensure_loopback_no_proxy()
    key = (api_key or "").strip()
    if not key:
        from fawai_avatar.cursor_agent import auth as ca_auth

        key, _ = ca_auth.resolve_api_key()
    if not key:
        raise RuntimeError("未登录 Cursor（请先登录）")

    from cursor_sdk import Cursor

    prev = os.environ.get("CURSOR_API_KEY")
    os.environ["CURSOR_API_KEY"] = key
    try:
        try:
            raw_list = Cursor.models.list(api_key=key)
        except TypeError:
            raw_list = Cursor.models.list()
    finally:
        if prev is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = prev
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_list or []:
        entry = normalize_model_entry(item)
        if not entry or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        out.append(entry)
    return _ensure_common_models(out)


def _extract_assistant_text(message: Any) -> str:
    """从 SDK 消息取可见助手正文。"""
    if message is None:
        return ""
    # 已归一的 dict
    if isinstance(message, dict):
        if message.get("type") == "assistant" or message.get("role") == "assistant":
            content = message.get("message", {}).get("content") if isinstance(message.get("message"), dict) else message.get("content")
            return _blocks_to_text(content)
        return str(message.get("text") or "")
    mtype = str(getattr(message, "type", None) or "").lower()
    # SDKAssistantMessage
    if "assistant" in type(message).__name__.lower() or mtype == "assistant":
        inner = getattr(message, "message", None)
        content = getattr(inner, "content", None) if inner is not None else getattr(message, "content", None)
        return _blocks_to_text(content)
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _blocks_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if str(block.get("type") or "") == "text":
                    parts.append(str(block.get("text") or ""))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
            else:
                btype = str(getattr(block, "type", None) or "")
                if btype == "text" or hasattr(block, "text"):
                    parts.append(str(getattr(block, "text", "") or ""))
    return "".join(parts)


def _sdk_msg_type(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("type") or "").strip().lower()
    return str(getattr(msg, "type", None) or "").strip().lower()


def _thinking_text(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("text") or "").strip()
    return str(getattr(msg, "text", None) or "").strip()


def _tool_call_fields(msg: Any) -> tuple[str, str, Any, Any, str]:
    if isinstance(msg, dict):
        return (
            str(msg.get("call_id") or msg.get("callId") or ""),
            str(msg.get("name") or ""),
            msg.get("args"),
            msg.get("result"),
            str(msg.get("status") or ""),
        )
    return (
        str(getattr(msg, "call_id", None) or getattr(msg, "callId", None) or ""),
        str(getattr(msg, "name", None) or ""),
        getattr(msg, "args", None),
        getattr(msg, "result", None),
        str(getattr(msg, "status", None) or ""),
    )


def _result_preview(result: Any, *, limit: int = 1200) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result.strip()
    else:
        try:
            import json

            text = json.dumps(result, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(result)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _emit_tool(emit: OnEvent, *, kind: str, aid: str, **fields: Any) -> None:
    if emit is None:
        return
    payload: dict[str, Any] = {"type": kind, "agent_id": aid, **fields}
    try:
        emit(payload)
    except Exception:  # noqa: BLE001
        pass


def _push_thinking_delta(
    thinking_acc: list[str],
    piece: str,
    emit: OnEvent,
    aid: str,
) -> None:
    """把流式思考片段并入缓冲；支持增量拼接与整段快照，避免一词一步。"""
    raw = str(piece or "")
    if not raw:
        return
    acc = "".join(thinking_acc)
    if not acc:
        thinking_acc.append(raw)
    elif raw == acc or acc.endswith(raw):
        pass
    elif raw.startswith(acc) and len(raw) >= len(acc):
        # SDK 推累计全文
        thinking_acc.clear()
        thinking_acc.append(raw)
    elif acc.startswith(raw) and len(acc) > len(raw):
        # 旧片段是前缀，忽略
        pass
    else:
        # 纯 token 增量（含前导空格）
        thinking_acc.append(raw)
    joined = "".join(thinking_acc)
    try:
        emit(
            {
                "type": "thinking_delta",
                "text": raw,
                "accumulated": joined,
                "agent_id": aid,
            }
        )
    except Exception:  # noqa: BLE001
        pass


def _flush_thinking_done(
    thinking_acc: list[str],
    emit: OnEvent,
    aid: str,
    *,
    duration_ms: Any = None,
) -> None:
    """收束当前思考为**一步** planned（仅在 completed / 工具前 / 流结束时调用）。"""
    text = "".join(thinking_acc).strip()
    thinking_acc.clear()
    if not text:
        return
    payload: dict[str, Any] = {
        "type": "thinking_done",
        "text": text,
        "agent_id": aid,
    }
    if duration_ms is not None:
        try:
            dur = int(duration_ms)
            if dur > 0:
                payload["thinking_duration_ms"] = dur
        except (TypeError, ValueError):
            pass
    try:
        emit(payload)
    except Exception:  # noqa: BLE001
        pass


def _consume_interaction_update(update: Any, emit: OnEvent, aid: str, *, thinking_acc: list[str]) -> None:
    from fawai_avatar.cursor_agent.session_progress import summarize_tool_call

    utype = ""
    if isinstance(update, dict):
        utype = str(update.get("type") or "").strip().lower()
    else:
        utype = str(getattr(update, "type", None) or "").strip().lower()
    if utype == "thinking-delta":
        piece = ""
        if isinstance(update, dict):
            piece = str(update.get("text") or "")
        else:
            piece = str(getattr(update, "text", None) or "")
        if piece:
            _push_thinking_delta(thinking_acc, piece, emit, aid)
        return
    if utype == "thinking-completed":
        duration = 0
        if not isinstance(update, dict):
            duration = int(getattr(update, "thinking_duration_ms", None) or 0)
        else:
            duration = int(update.get("thinkingDurationMs") or update.get("thinking_duration_ms") or 0)
        _flush_thinking_done(thinking_acc, emit, aid, duration_ms=duration)
        return
    if utype in {"tool-call-started", "partial-tool-call"}:
        _flush_thinking_done(thinking_acc, emit, aid)
        tool_call: dict[str, Any] = {}
        call_id = ""
        if isinstance(update, dict):
            tool_call = dict(update.get("toolCall") or {})
            call_id = str(update.get("callId") or update.get("call_id") or "")
        else:
            tool_call = dict(getattr(update, "tool_call", None) or {})
            call_id = str(getattr(update, "call_id", None) or "")
        name = str(tool_call.get("name") or tool_call.get("toolName") or "")
        args = tool_call.get("args")
        display, detail, commands = summarize_tool_call(name, args)
        _emit_tool(
            emit,
            kind="tool_start",
            aid=aid,
            call_id=call_id,
            name=name,
            display_name=display,
            detail=detail,
            commands=commands,
            args=args,
        )
        return
    if utype == "tool-call-completed":
        _flush_thinking_done(thinking_acc, emit, aid)
        tool_call = {}
        call_id = ""
        if isinstance(update, dict):
            tool_call = dict(update.get("toolCall") or {})
            call_id = str(update.get("callId") or update.get("call_id") or "")
        else:
            tool_call = dict(getattr(update, "tool_call", None) or {})
            call_id = str(getattr(update, "call_id", None) or "")
        name = str(tool_call.get("name") or tool_call.get("toolName") or "")
        args = tool_call.get("args")
        result = tool_call.get("result")
        display, detail, commands = summarize_tool_call(name, args, result=result)
        status = str(tool_call.get("status") or "completed")
        _emit_tool(
            emit,
            kind="tool_done",
            aid=aid,
            call_id=call_id,
            name=name,
            display_name=display,
            detail=detail,
            commands=commands,
            result_preview=_result_preview(result),
            status=status,
            ok=status.lower() not in {"error", "failed"},
        )


def _consume_sdk_message(msg: Any, emit: OnEvent, aid: str, *, thinking_acc: list[str]) -> str:
    """处理单条 SDKMessage；返回可拼进终稿的助手正文（不含思考）。"""
    from fawai_avatar.cursor_agent.session_progress import summarize_tool_call

    mtype = _sdk_msg_type(msg)
    if mtype == "thinking":
        text = _thinking_text(msg)
        if text:
            # token/片段只进 delta；禁止每个词 emit thinking_done
            _push_thinking_delta(thinking_acc, text, emit, aid)
        return ""
    if mtype == "tool_call":
        _flush_thinking_done(thinking_acc, emit, aid)
        call_id, name, args, result, status = _tool_call_fields(msg)
        display, detail, commands = summarize_tool_call(name, args, result=result)
        if status == "running":
            _emit_tool(
                emit,
                kind="tool_start",
                aid=aid,
                call_id=call_id,
                name=name,
                display_name=display,
                detail=detail,
                commands=commands,
                args=args,
            )
        else:
            _emit_tool(
                emit,
                kind="tool_done",
                aid=aid,
                call_id=call_id,
                name=name,
                display_name=display,
                detail=detail,
                commands=commands,
                result_preview=_result_preview(result),
                status=status or "completed",
                ok=str(status or "").lower() not in {"error", "failed"},
            )
        return ""
    if mtype == "assistant":
        _flush_thinking_done(thinking_acc, emit, aid)
        text = _extract_assistant_text(msg)
        if text:
            # 直播片段进进度卡；同文仍返回供终稿拼接
            emit({"type": "assistant_segment", "text": text, "agent_id": aid})
        return text
    return ""


def _consume_conversation_step(
    step: Any, emit: OnEvent, aid: str, *, thinking_acc: list[str]
) -> None:
    from cursor_sdk.types import (
        AssistantConversationStep,
        ThinkingConversationStep,
        ToolCallConversationStep,
    )
    from fawai_avatar.cursor_agent.session_progress import summarize_tool_call

    if isinstance(step, ThinkingConversationStep):
        text = str(getattr(step.message, "text", "") or "").strip()
        if text:
            # ConversationStep 常按 token 推送；只作 delta，收束交给 flush
            _push_thinking_delta(thinking_acc, text, emit, aid)
        return
    if isinstance(step, ToolCallConversationStep):
        _flush_thinking_done(thinking_acc, emit, aid)
        msg = step.message if isinstance(step.message, dict) else {}
        name = str(msg.get("name") or msg.get("toolName") or "")
        args = msg.get("args")
        result = msg.get("result")
        call_id = str(msg.get("call_id") or msg.get("callId") or "")
        status = str(msg.get("status") or "completed")
        display, detail, commands = summarize_tool_call(name, args, result=result)
        if status == "running":
            _emit_tool(
                emit,
                kind="tool_start",
                aid=aid,
                call_id=call_id,
                name=name,
                display_name=display,
                detail=detail,
                commands=commands,
            )
        else:
            _emit_tool(
                emit,
                kind="tool_done",
                aid=aid,
                call_id=call_id,
                name=name,
                display_name=display,
                detail=detail,
                commands=commands,
                result_preview=_result_preview(result),
                status=status,
                ok=status.lower() not in {"error", "failed"},
            )
        return
    if isinstance(step, AssistantConversationStep):
        _flush_thinking_done(thinking_acc, emit, aid)
        text = str(getattr(step.message, "text", "") or "").strip()
        if text:
            emit({"type": "assistant_segment", "text": text, "agent_id": aid})


def stream_agent_run(run: Any, emit: OnEvent | None, *, agent_id: str) -> list[str]:
    """遍历 run.events()，向 emit 推送结构化事件；返回助手正文片段（供终稿）。"""
    if emit is None:
        emit = lambda _ev: None  # noqa: E731
    assistant_chunks: list[str] = []
    thinking_acc: list[str] = []
    kind_counts: dict[str, int] = {}
    try:
        events_fn = getattr(run, "events", None)
        if not callable(events_fn):
            for msg in run.messages():
                kind_counts["sdk_message"] = int(kind_counts.get("sdk_message") or 0) + 1
                piece = _consume_sdk_message(msg, emit, agent_id, thinking_acc=thinking_acc)
                if piece:
                    assistant_chunks.append(piece)
            _flush_thinking_done(thinking_acc, emit, agent_id)
            emit(
                {
                    "type": "stream_stats",
                    "event_kinds": dict(kind_counts),
                    "agent_id": agent_id,
                }
            )
            return assistant_chunks
        for event in events_fn():
            kind = str(getattr(event, "kind", None) or "") or "unknown"
            kind_counts[kind] = int(kind_counts.get(kind) or 0) + 1
            if kind == "interaction_update":
                upd = getattr(event, "interaction_update", None)
                if upd is not None:
                    _consume_interaction_update(upd, emit, agent_id, thinking_acc=thinking_acc)
                continue
            if kind == "sdk_message":
                msg = getattr(event, "sdk_message", None)
                if msg is not None:
                    piece = _consume_sdk_message(msg, emit, agent_id, thinking_acc=thinking_acc)
                    if piece:
                        assistant_chunks.append(piece)
                continue
            if kind == "step":
                step = getattr(event, "step", None)
                if step is not None:
                    _consume_conversation_step(
                        step, emit, agent_id, thinking_acc=thinking_acc
                    )
                continue
            # 未知 kind：计入 stats，避免 silently 丢事件却难排查
        _flush_thinking_done(thinking_acc, emit, agent_id)
        emit(
            {
                "type": "stream_stats",
                "event_kinds": dict(kind_counts),
                "agent_id": agent_id,
            }
        )
    except Exception as stream_exc:  # noqa: BLE001
        _flush_thinking_done(thinking_acc, emit, agent_id)
        emit({"type": "stream_error", "error": str(stream_exc), "agent_id": agent_id})
        if kind_counts:
            emit(
                {
                    "type": "stream_stats",
                    "event_kinds": dict(kind_counts),
                    "agent_id": agent_id,
                }
            )
    return assistant_chunks


def run_agent_prompt(
    prompt: str,
    *,
    api_key: str = "",
    model: str,
    cwd: str,
    agent_id: str | None = None,
    mode: str | None = None,
    auto_accept: bool | None = None,
    on_event: OnEvent | None = None,
) -> dict[str, Any]:
    """创建或 resume local agent，发送一轮 prompt，返回终态摘要。

    on_event 可选：收到助手增量或状态时回调 ``{type, text, ...}``。
    ``auto_accept`` 映射 SDK ``LocalAgentOptions.auto_review``（工具自动审阅，对齐 IDE Auto Accept 语义）。
    """
    if not sdk_available():
        raise RuntimeError(f"cursor-sdk 未安装：{sdk_import_error()}")
    ensure_loopback_no_proxy()
    key = (api_key or "").strip()
    if not key:
        from fawai_avatar.cursor_agent import auth as ca_auth

        key, _ = ca_auth.resolve_api_key()
    if not key:
        raise RuntimeError("未登录 Cursor（请先登录）")
    model_id = (model or "").strip() or "composer-2.5"
    work = (cwd or "").strip() or "."
    text = (prompt or "").strip()
    if not text:
        raise RuntimeError("prompt 为空")
    mode_id = normalize_mode(mode)
    use_auto_review = bool(auto_accept) if auto_accept is not None else False

    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    local_opts = LocalAgentOptions(cwd=work, auto_review=use_auto_review)
    agent_opts = AgentOptions(
        api_key=key,
        model=model_id,
        mode=mode_id,  # type: ignore[arg-type]
        local=local_opts,
    )

    def _emit(ev: dict[str, Any]) -> None:
        if on_event is not None:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    created = False
    resume_id = (agent_id or "").strip()
    if resume_id:
        ctx = Agent.resume(resume_id, agent_opts)
    else:
        created = True
        ctx = Agent.create(agent_opts)
    with ctx as agent:
        aid = str(getattr(agent, "agent_id", None) or getattr(agent, "agentId", None) or resume_id or "")
        _emit(
            {
                "type": "agent",
                "agent_id": aid,
                "created": created,
                "model": model_id,
                "mode": mode_id,
                "auto_accept": use_auto_review,
            }
        )
        run = agent.send(text)
        run_id = str(getattr(run, "id", None) or "")
        _emit({"type": "run_start", "run_id": run_id, "agent_id": aid})
        chunks = stream_agent_run(run, _emit, agent_id=aid)
        result = run.wait()
        status = str(getattr(result, "status", None) or "")
        final = str(getattr(result, "result", None) or "").strip()
        if not final and chunks:
            final = "".join(chunks).strip()
        out = {
            "ok": status in {"finished", "success", "completed", ""} or status == "finished",
            "status": status or "finished",
            "agent_id": aid,
            "run_id": run_id,
            "model": model_id,
            "mode": mode_id,
            "auto_accept": use_auto_review,
            "cwd": work,
            "result": final,
            "created": created,
        }
        if status == "error":
            out["ok"] = False
            out["error"] = final or "run status=error"
        _emit({"type": "run_done", **out})
        return out
