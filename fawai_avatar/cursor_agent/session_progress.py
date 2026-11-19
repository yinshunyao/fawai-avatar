# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""把 cursor-sdk 流式事件映射为会话网关步骤流（思考 / 工具，对齐 Cursor 客户端）。"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fawai_avatar.runtime.llm_live import LlmLiveProgress

ProgressEmit = Callable[..., None]

_CURSOR_TOOL_LABELS: dict[str, str] = {
    "shell": "Shell",
    "Shell": "Shell",
    "bash": "Shell",
    "run_terminal_cmd": "Shell",
    "grep": "Search",
    "Grep": "Search",
    "rg": "Search",
    "glob_file_search": "Glob",
    "Glob": "Glob",
    "read_file": "Read",
    "Read": "Read",
    "read": "Read",
    "write": "Edit",
    "Write": "Edit",
    "search_replace": "Edit",
    "StrReplace": "Edit",
    "edit_notebook": "Notebook",
    "web_search": "Web",
    "WebSearch": "Web",
    "list_dir": "List",
    "delete_file": "Delete",
    "task": "Task",
    "TodoWrite": "Todo",
}

# 落盘 actions.kind：对齐 summarize_action_line / 时间线回放
_ACTION_KIND: dict[str, str] = {
    "Shell": "shell",
    "Search": "grep",
    "Glob": "glob_file_search",
    "Read": "read_file",
    "Edit": "write",
    "Web": "web_search",
    "List": "list_dir",
    "Delete": "delete_file",
    "Notebook": "edit_notebook",
    "Todo": "TodoWrite",
    "Task": "task",
}


def cursor_tool_label(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "Other"
    if raw in _CURSOR_TOOL_LABELS:
        return _CURSOR_TOOL_LABELS[raw]
    low = raw.lower()
    for key, label in _CURSOR_TOOL_LABELS.items():
        if key.lower() == low:
            return label
    return raw if len(raw) <= 24 else raw[:23] + "…"


def action_kind_for_store(display: str, name: str = "") -> str:
    if display in _ACTION_KIND:
        return _ACTION_KIND[display]
    raw = (name or display or "other").strip()
    return raw if raw else "other"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _clip(text: str, limit: int = 1200) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def _pick_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def summarize_tool_call(
    name: str,
    args: Any = None,
    *,
    result: Any = None,
) -> tuple[str, str, list[str]]:
    """返回 (展示名, 一行摘要, 命令行列表)。"""
    display = cursor_tool_label(name)
    argm = _as_dict(args) if args is not None else {}
    commands: list[str] = []
    detail = ""
    low = (name or "").lower()
    if low in {"shell", "bash", "run_terminal_cmd"} or display == "Shell":
        cmd = _pick_str(argm, "command", "cmd", "script")
        if cmd:
            commands.append(cmd)
            detail = _clip(cmd, 240)
    elif low in {"grep", "rg", "ripgrep"} or display == "Search":
        pattern = _pick_str(argm, "pattern", "query", "regex")
        path = _pick_str(argm, "path", "glob", "target_directory")
        detail = pattern or path
        if pattern and path:
            detail = f"{pattern} — {path}"
    elif low in {"read_file", "read"} or display == "Read":
        detail = _pick_str(argm, "path", "target_file", "file")
    elif low in {"glob_file_search", "glob"} or display == "Glob":
        detail = _pick_str(argm, "glob_pattern", "pattern", "path")
    elif low in {"write", "search_replace", "strreplace"} or display == "Edit":
        detail = _pick_str(argm, "path", "target_file", "file")
    if not detail and argm:
        try:
            detail = _clip(json.dumps(argm, ensure_ascii=False), 200)
        except Exception:  # noqa: BLE001
            detail = str(argm)[:200]
    if result is not None and not detail:
        try:
            detail = _clip(str(result), 200)
        except Exception:  # noqa: BLE001
            detail = ""
    return display, detail, commands


def _tool_args_for_store(display: str, args: Any, detail: str, commands: list[str]) -> dict[str, Any]:
    argm = _as_dict(args)
    out: dict[str, Any] = {}
    if display == "Shell":
        cmd = _pick_str(argm, "command", "cmd", "script") or (commands[0] if commands else detail)
        if cmd:
            out["cmd"] = _clip(cmd, 200)
        return out
    if display == "Search":
        pattern = _pick_str(argm, "pattern", "query", "regex")
        path = _pick_str(argm, "path", "glob", "target_directory")
        if pattern:
            out["pattern"] = _clip(pattern, 160)
        if path:
            out["path"] = _clip(path, 160)
        return out
    if display in {"Read", "Edit", "Delete"}:
        path = _pick_str(argm, "path", "target_file", "file") or detail
        if path:
            out["path"] = _clip(path, 200)
        return out
    if display == "Glob":
        pat = _pick_str(argm, "glob_pattern", "pattern", "path") or detail
        if pat:
            out["glob_pattern"] = _clip(pat, 160)
        return out
    if detail:
        out["detail"] = _clip(detail, 200)
    elif argm:
        try:
            out["detail"] = _clip(json.dumps(argm, ensure_ascii=False), 200)
        except Exception:  # noqa: BLE001
            out["detail"] = str(argm)[:200]
    return out


def _is_think_fragment(text: str) -> bool:
    """单 token / 极短碎片：不应单独成「规划完成」一步。"""
    t = (text or "").strip()
    if not t:
        return True
    if any(c in t for c in ".。!！?？\n"):
        return False
    words = t.split()
    if len(t) <= 24 and len(words) <= 3:
        return True
    return False


def clip_cursor_steps(steps: list[dict[str, Any]], *, max_steps: int = 24) -> list[dict[str, Any]]:
    """落盘用：限制步数与字段长度（对齐 Worker `_clip_steps_for_meta` 形态）。"""
    from fawai_avatar.channel.redact import redact_secrets

    clipped: list[dict[str, Any]] = []
    for step in steps[-max_steps:]:
        plan = dict((step or {}).get("plan") or {})
        thought = redact_secrets(str(plan.get("thought") or ""))[:800]
        actions: list[dict[str, Any]] = []
        for a in plan.get("actions") or []:
            if not isinstance(a, dict):
                continue
            raw_args = a.get("args") if isinstance(a.get("args"), dict) else {}
            args_out: dict[str, Any] = {}
            for k, v in list(raw_args.items())[:12]:
                key = str(k)
                if isinstance(v, str):
                    text = redact_secrets(v)
                    args_out[key] = text[:200] + ("…" if len(text) > 200 else "")
                else:
                    args_out[key] = v
            actions.append({"kind": a.get("kind"), "args": args_out})
        results: list[dict[str, Any]] = []
        for r in (step or {}).get("results") or []:
            if isinstance(r, dict):
                results.append(
                    {
                        "ok": r.get("ok"),
                        "message": redact_secrets(str(r.get("message") or ""))[:400],
                    }
                )
        entry: dict[str, Any] = {
            "plan": {
                "thought": thought,
                "status": plan.get("status") or "continue",
                "done": plan.get("done"),
                "actions": actions,
            },
            "results": results,
        }
        label = str(plan.get("step_label") or "").strip()
        if label:
            entry["plan"]["step_label"] = label[:64]
        clipped.append(entry)
    return clipped


class CursorSessionProgress:
    """消费 ``runtime.stream_agent_run`` 事件并 ``emit_progress``；累积可回放 ``steps``。"""

    def __init__(
        self,
        emit: ProgressEmit,
        *,
        task_id: str,
        base_meta: dict[str, Any] | None = None,
    ) -> None:
        self._emit = emit
        self._task_id = task_id
        self._base = dict(base_meta or {})
        self._step = 0
        self._think_live: LlmLiveProgress | None = None
        self._think_step: int | None = None
        self._tool_step: dict[str, int] = {}
        self._tool_pending: dict[str, dict[str, Any]] = {}
        self._tool_counts: dict[str, int] = {}
        self._steps: list[dict[str, Any]] = []
        self._run_id: str = ""
        self._agent_id: str = ""
        self._event_counts: dict[str, int] = {}
        self._assistant_acc: str = ""
        self._assistant_step: int | None = None

    def _bump(self, et: str) -> None:
        key = (et or "").strip() or "unknown"
        self._event_counts[key] = int(self._event_counts.get(key) or 0) + 1

    def _extra(self, **fields: Any) -> dict[str, Any]:
        out = {**self._base, "runtime": "cursor_agent", **fields}
        if self._run_id:
            out.setdefault("run_id", self._run_id)
        if self._agent_id:
            out.setdefault("agent_id", self._agent_id)
        if self._tool_counts:
            out["cursor_tools"] = dict(self._tool_counts)
        return out

    def _progress(self, text: str, *, phase: str, step: int, live: bool = False, **extra: Any) -> None:
        meta = self._extra(phase=phase, step=step, **extra)
        if live:
            meta["live"] = True
        try:
            self._emit(text, task_id=self._task_id, meta=meta)
        except Exception:  # noqa: BLE001
            pass

    def _live_emit(self, text: str, *, phase: str, step: int, **extra: Any) -> None:
        self._progress(text, phase=phase, step=step, live=True, **extra)

    def _finish_thinking(self) -> None:
        if self._think_live is None:
            return
        self._think_live.flush()
        preview = self._think_live.preview()
        step = int(self._think_step or self._step)
        thinking = str(preview.get("thinking_text") or "").strip()
        body = thinking or "思考完成"
        self._progress(
            f"规划完成（continue）；{body[:160]}",
            phase="planned",
            step=step,
            **preview,
        )
        self._append_think_step(thought=thinking, step_label=body[:32])
        self._think_live = None
        self._think_step = None

    def _append_think_step(self, *, thought: str, step_label: str = "") -> None:
        plan: dict[str, Any] = {
            "thought": thought[:800],
            "status": "continue",
            "actions": [],
        }
        if step_label:
            plan["step_label"] = step_label[:64]
        self._steps.append({"plan": plan, "results": []})

    def _append_tool_step(
        self,
        *,
        display: str,
        name: str,
        detail: str,
        commands: list[str],
        args: Any,
        ok: bool,
        preview: str,
    ) -> None:
        kind = action_kind_for_store(display, name)
        store_args = _tool_args_for_store(display, args, detail, commands)
        plan: dict[str, Any] = {
            "thought": "",
            "status": "continue",
            "step_label": (detail[:32] or display)[:64],
            "actions": [{"kind": kind, "args": store_args}],
        }
        msg = preview or detail or display
        self._steps.append(
            {
                "plan": plan,
                "results": [{"ok": bool(ok), "message": _clip(msg, 400)}],
            }
        )

    def _start_thinking(self) -> LlmLiveProgress:
        self._finish_thinking()
        self._step += 1
        step = self._step
        self._think_step = step
        live = LlmLiveProgress(
            self._live_emit,
            phase="plan",
            step=step,
            min_interval_ms=100,
            min_chars=10,
            runtime="cursor_agent",
        )
        self._think_live = live
        self._progress("深度思考中…", phase="plan", step=step, live=True, api_wait=True)
        return live

    def handle(self, ev: dict[str, Any]) -> None:
        et = str(ev.get("type") or "")
        if et:
            self._bump(et)
        if et == "run_start":
            self._run_id = str(ev.get("run_id") or self._run_id or "")
            self._agent_id = str(ev.get("agent_id") or self._agent_id or "")
            self._progress(
                "Cursor Agent 运行中…",
                phase="cursor_status",
                step=0,
                api_wait=True,
                run_id=self._run_id or None,
                agent_id=self._agent_id or None,
            )
            return
        if et == "agent":
            aid = str(ev.get("agent_id") or "")
            if aid:
                self._agent_id = aid
            return
        if et == "thinking_delta":
            acc = str(ev.get("accumulated") or ev.get("text") or "")
            if not acc.strip():
                return
            live = self._think_live or self._start_thinking()
            live.on_delta({"thinking": acc})
            return
        if et == "thinking_done":
            text = str(ev.get("text") or "").strip()
            dur = ev.get("thinking_duration_ms")
            has_dur = False
            try:
                has_dur = dur is not None and int(dur) > 0
            except (TypeError, ValueError):
                has_dur = False
            if self._think_live is not None:
                if text:
                    prev = str(self._think_live.preview().get("thinking_text") or "")
                    if not prev:
                        merged = text
                    elif text == prev or prev.endswith(text):
                        merged = prev
                    elif text.startswith(prev) and len(text) >= len(prev):
                        # 累计全文快照
                        merged = text
                    else:
                        sep = (
                            ""
                            if prev[-1].isspace() or text[:1].isspace()
                            else " "
                        )
                        merged = prev + sep + text
                    self._think_live.on_delta({"thinking": merged})
                # 碎片继续直播；整段 / 带时长才收成一步（一词多 thinking_done 时靠 tool_start 收束）
                if has_dur or (text and not _is_think_fragment(text)):
                    self._finish_thinking()
                return
            if not text:
                return
            # 兜底：一词一条 thinking_done 时先开直播，等 tool/flush 再收成一步
            if _is_think_fragment(text) and not has_dur:
                live = self._start_thinking()
                live.on_delta({"thinking": text})
                return
            self._step += 1
            step = self._step
            extra: dict[str, Any] = {"thinking_text": text}
            if has_dur:
                extra["elapsed_ms"] = int(dur)
            self._progress(
                f"规划完成（continue）；{text[:160]}",
                phase="planned",
                step=step,
                **extra,
            )
            self._append_think_step(thought=text, step_label=text[:32])
            return
        if et == "tool_start":
            self._finish_thinking()
            call_id = str(ev.get("call_id") or "")
            name = str(ev.get("name") or "")
            display = str(ev.get("display_name") or cursor_tool_label(name))
            detail = str(ev.get("detail") or "").strip()
            commands = ev.get("commands") if isinstance(ev.get("commands"), list) else []
            args = ev.get("args")
            self._step += 1
            step = self._step
            if call_id:
                self._tool_step[call_id] = step
                self._tool_pending[call_id] = {
                    "name": name,
                    "display": display,
                    "detail": detail,
                    "commands": [str(c) for c in commands][:8],
                    "args": args,
                }
            self._tool_counts[display] = self._tool_counts.get(display, 0) + 1
            title = display
            if detail:
                title = f"{display}: {detail[:120]}"
            self._progress(
                f"执行中：{title}",
                phase="act",
                step=step,
                tool=display,
                call_id=call_id or None,
                commands=[str(c) for c in commands][:8],
                step_label=detail[:32] or display,
            )
            return
        if et == "tool_done":
            call_id = str(ev.get("call_id") or "")
            pending = self._tool_pending.pop(call_id, None) if call_id else None
            step = self._tool_step.get(call_id)
            name = str(ev.get("name") or (pending or {}).get("name") or "")
            display = str(
                ev.get("display_name")
                or (pending or {}).get("display")
                or cursor_tool_label(name)
            )
            detail = str(ev.get("detail") or (pending or {}).get("detail") or "").strip()
            commands = ev.get("commands") if isinstance(ev.get("commands"), list) else []
            if not commands and pending:
                commands = list(pending.get("commands") or [])
            args = ev.get("args") if "args" in ev else (pending or {}).get("args")
            # 仅 completed、无 start：补一张 act 再 acted，并计入工具次数
            if step is None:
                self._finish_thinking()
                self._step += 1
                step = self._step
                if call_id:
                    self._tool_step[call_id] = step
                self._tool_counts[display] = self._tool_counts.get(display, 0) + 1
                title = f"{display}: {detail[:120]}" if detail else display
                self._progress(
                    f"执行中：{title}",
                    phase="act",
                    step=step,
                    tool=display,
                    call_id=call_id or None,
                    commands=[str(c) for c in commands][:8],
                    step_label=detail[:32] or display,
                )
            preview = _clip(str(ev.get("result_preview") or ""), 800)
            ok = ev.get("ok")
            if ok is None:
                ok = str(ev.get("status") or "").lower() not in {"error", "failed"}
            body = preview or detail or display
            prefix = "执行: " if ok else "失败: "
            self._progress(
                f"{prefix}{body}",
                phase="acted",
                step=step,
                ok=bool(ok),
                tool=display,
                call_id=call_id or None,
                commands=[str(c) for c in commands][:8] if commands else None,
            )
            self._append_tool_step(
                display=display,
                name=name,
                detail=detail,
                commands=[str(c) for c in commands],
                args=args,
                ok=bool(ok),
                preview=preview,
            )
            return
        if et == "assistant_segment":
            piece = str(ev.get("text") or "")
            if not piece.strip():
                return
            self._assistant_acc = (self._assistant_acc + piece)[-8000:]
            if self._assistant_step is None:
                self._finish_thinking()
                self._step += 1
                self._assistant_step = self._step
            step = int(self._assistant_step)
            preview = _clip(self._assistant_acc, 400)
            self._progress(
                "模型输出中…",
                phase="plan",
                step=step,
                live=True,
                content_text=self._assistant_acc,
                step_label="输出",
            )
            # 不落 steps；终稿以 text_assistant 为准。仅用短预览占位便于调试。
            del preview
            return
        if et == "stream_error":
            self._finish_thinking()
            self._progress(
                str(ev.get("error") or "Cursor 流中断"),
                phase="failed",
                step=self._step,
            )
            return
        if et == "stream_stats":
            kinds = ev.get("event_kinds") if isinstance(ev.get("event_kinds"), dict) else {}
            for k, v in kinds.items():
                key = f"sdk:{k}"
                try:
                    self._event_counts[key] = int(self._event_counts.get(key) or 0) + int(v or 0)
                except (TypeError, ValueError):
                    continue
            return
        if et == "run_done":
            # 身份字段补齐；正文由 hub 终稿气泡负责
            rid = str(ev.get("run_id") or "")
            aid = str(ev.get("agent_id") or "")
            if rid:
                self._run_id = rid
            if aid:
                self._agent_id = aid

    def finalize(self) -> dict[str, Any]:
        self._finish_thinking()
        steps = clip_cursor_steps(self._steps)
        return {
            "cursor_tools": dict(self._tool_counts),
            "cursor_steps": self._step,
            "cursor_events": dict(self._event_counts),
            "steps": steps,
            "run_id": self._run_id,
            "agent_id": self._agent_id,
        }
