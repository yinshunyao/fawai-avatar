# term:成功价值台账|失败价值台账  doc:doc/功能/成功价值台账/说明.md
"""Ledger → lore 笔记标题：用人话短句，拒绝 True/False 等无意义值。"""

from __future__ import annotations

from typing import Any

# 单独出现时不当标题（大小写不敏感；中英混用）
_BAD_TITLE_TOKENS = frozenset(
    {
        "true",
        "false",
        "none",
        "null",
        "undefined",
        "nan",
        "ok",
        "yes",
        "no",
        "pass",
        "passed",
        "fail",
        "failed",
        "success",
        "失败",
        "成功",
        "通过",
        "完成",
    }
)


def as_text_field(val: Any) -> str:
    """布尔 / 空不当正文；其余转短字符串。"""
    if val is None or isinstance(val, bool):
        return ""
    if isinstance(val, (dict, list, tuple)):
        return ""
    text = str(val).strip()
    if not text:
        return ""
    if text.lower() in _BAD_TITLE_TOKENS:
        return ""
    return text


def usable_title_fragment(raw: Any, *, min_len: int = 4, max_len: int = 80) -> str:
    """可供展示的标题片段；过短 / 布尔字面量 / 纯符号 → 空。"""
    text = as_text_field(raw)
    if not text:
        return ""
    compact = "".join(text.split())
    if len(compact) < min_len:
        return ""
    # 纯数字/纯标点无叙事价值
    if compact.isdigit():
        return ""
    if all(not ch.isalnum() for ch in compact):
        return ""
    one_line = " ".join(text.split())
    if len(one_line) > max_len:
        one_line = one_line[: max_len - 1].rstrip() + "…"
    return one_line


def _command_snippet(commands: Any) -> str:
    if not isinstance(commands, list) or not commands:
        return ""
    first = commands[0]
    if isinstance(first, dict):
        kind = str(first.get("kind") or "").strip()
        cmd = str(
            first.get("cmd")
            or first.get("invoke")
            or first.get("path")
            or first.get("tool")
            or ""
        ).strip()
        blob = " ".join(p for p in (kind, cmd) if p)
        return usable_title_fragment(blob, min_len=3, max_len=60)
    return usable_title_fragment(first, min_len=3, max_len=60)


def _tool_snip(entry: dict[str, Any]) -> str:
    return usable_title_fragment(entry.get("tool_id"), min_len=2, max_len=40)


def summarize_success_lore_title(entry: dict[str, Any], *, eid: str = "") -> str:
    """成功台账入库标题：优先验收句 / 目标摘 / 证据，再命令族。"""
    for key in ("acceptance", "goal_slice", "evidence", "avoid"):
        frag = usable_title_fragment(entry.get(key))
        if frag:
            return frag
    cmd = _command_snippet(entry.get("commands"))
    if cmd:
        tool = _tool_snip(entry)
        return f"{tool} · {cmd}" if tool and tool not in cmd else cmd
    tool = _tool_snip(entry)
    if tool:
        return f"成功复用 · {tool}"
    return f"成功台账 {eid or entry.get('id') or ''}".strip()


def summarize_failure_lore_title(entry: dict[str, Any], *, eid: str = "") -> str:
    """失败台账入库标题（调用方再加 [avoid] 前缀亦可）。"""
    for key in ("symptom", "cause", "goal_slice", "do_not_retry", "evidence", "recovery"):
        frag = usable_title_fragment(entry.get(key))
        if frag:
            return frag
    cmd = _command_snippet(entry.get("commands_tried") or entry.get("commands"))
    if cmd:
        return cmd
    tool = _tool_snip(entry)
    if tool:
        return f"失败避坑 · {tool}"
    return f"失败台账 {eid or entry.get('id') or ''}".strip()
