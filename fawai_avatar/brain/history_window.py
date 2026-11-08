# term:规划历史窗  doc:doc/功能/编码闭环/说明.md
"""规划前压缩 history：近窗全文 + 远窗摘要，抑制重复只读回灌膨胀。"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Callable

from fawai_avatar.brain.durable_memory import durable_memory_text

Summarize = Callable[[str], str | None]

_COMPACT_NOTE = (
    "（这是更早步骤的摘要，只保留做过什么和得到的结论。"
    "摘要里没有的本任务事实：先 task_context_search / task_context_get；"
    "文件事实再按 path 定向 fs_read。"
    "不要把「曾经读过」当成不能再取证。）"
)

_PATH_RE = re.compile(
    r"(?:path=|paths?:\s*|path':\s*|path\":\s*)['\"]?([^\s'\",\]]+)",
    re.IGNORECASE,
)
_KIND_LINE_RE = re.compile(
    r"^-\s*(\w+)\b.*?→\s*(ok|FAIL)\b",
    re.IGNORECASE | re.MULTILINE,
)
_CONTENT_BLOCK_RE = re.compile(
    r"(?ms)^ {2}(?:stdout|stderr|content|lines|hits)[^:]*:\n.*?(?=^- |\Z)"
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def history_keep_recent() -> int:
    """近窗保留的最近消息条数（默认 8）。"""
    return _env_int("FAWAI_HISTORY_KEEP_RECENT", 8)


def history_max_chars() -> int:
    """压缩后 history 字符软上限（默认 18000）。"""
    return _env_int("FAWAI_HISTORY_MAX_CHARS", 18000)


def _msg_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def _msg_chars(msg: dict[str, Any]) -> int:
    return len(_msg_text(msg))


def _extract_paths(text: str) -> list[str]:
    out: list[str] = []
    for m in _PATH_RE.finditer(text or ""):
        p = (m.group(1) or "").strip().rstrip("',\"")
        if p and p not in out:
            out.append(p)
    return out


def _summarize_tool_feedback(text: str) -> str:
    """把工具结果观察压成「kind path → ok + 短提示」。"""
    kinds = _KIND_LINE_RE.findall(text or "")
    paths = _extract_paths(text or "")
    head = (text or "").splitlines()[:1]
    label = head[0] if head else "执行结果"
    if not kinds and not paths:
        body = (text or "").strip()
        if len(body) > 240:
            body = body[:239] + "…"
        return body or label
    bits: list[str] = [label + "（远窗摘要）"]
    for kind, status in kinds[:8]:
        bits.append(f"- {kind} → {status}")
    if paths:
        shown = paths[:6]
        bits.append(
            "path="
            + ", path=".join(shown)
            + ("…" if len(paths) > 6 else "")
        )
    if "已压缩" in (text or "") or "全文落盘" in (text or ""):
        bits.append("（原文已落盘/压缩，勿重复盲读同一大文件）")
    return "\n".join(bits)


def _summarize_assistant(text: str) -> str:
    body = (text or "").strip()
    if len(body) <= 120:
        return body
    return body[:119] + "…"


def _compact_recent_tool_msg(text: str) -> str:
    """近窗内仍去掉超长 content 块，保留命中头与 path 行。"""
    if len(text) <= 2500:
        return text
    stripped = _CONTENT_BLOCK_RE.sub(
        "  content: （已从近窗省略长正文；按 path/行号定向 fs_read）\n",
        text,
    )
    if len(stripped) <= 3500:
        return stripped
    return stripped[:3499] + "…"


def _dedupe_older_reads(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """远窗里同一 path 的只读摘要只留最后一次。"""
    last_idx_by_path: dict[str, int] = {}
    for i, msg in enumerate(summaries):
        text = _msg_text(msg)
        if "执行结果" not in text and "工具结果" not in text:
            continue
        if not any(k in text for k in ("fs_read", "fs_grep", "fs_glob", "explore_search")):
            continue
        for p in _extract_paths(text):
            last_idx_by_path[p] = i
    drop: set[int] = set()
    seen_path: set[str] = set()
    for i in range(len(summaries) - 1, -1, -1):
        text = _msg_text(summaries[i])
        if "执行结果" not in text and "工具结果" not in text:
            continue
        paths = _extract_paths(text)
        if not paths:
            continue
        if all(p in seen_path for p in paths):
            drop.add(i)
            continue
        for p in paths:
            seen_path.add(p)
    return [m for i, m in enumerate(summaries) if i not in drop]


def compress_history_for_plan(
    history: list[dict[str, Any]] | None,
    *,
    keep_recent: int | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """返回供规划使用的 history 副本（不修改原列表）。"""
    msgs = list(history or [])
    if not msgs:
        return []
    keep = history_keep_recent() if keep_recent is None else max(0, keep_recent)
    limit = history_max_chars() if max_chars is None else max(0, max_chars)
    total = sum(_msg_chars(m) for m in msgs)
    if keep <= 0:
        keep = len(msgs)
    if total <= limit and len(msgs) <= keep + 2:
        return msgs

    if len(msgs) <= keep:
        older: list[dict[str, Any]] = []
        recent = msgs
    else:
        older = msgs[:-keep]
        recent = msgs[-keep:]

    # 先按原文 path 去重，再摘要（摘要后 path= 可能被改成 paths:）
    older = _dedupe_older_reads(older)

    summarized: list[dict[str, Any]] = []
    for msg in older:
        role = str(msg.get("role") or "user")
        text = _msg_text(msg)
        if role == "assistant":
            summarized.append({"role": "assistant", "content": _summarize_assistant(text)})
        elif (
            "执行结果" in text
            or "工具结果" in text
            or "【探索进度闸门】" in text
            or "【探索改写强制】" in text
            or "【铲子优先】" in text
            or "【铲子提炼】" in text
        ):
            summarized.append({"role": role, "content": _summarize_tool_feedback(text)})
        else:
            body = text if len(text) <= 400 else text[:399] + "…"
            summarized.append({"role": role, "content": body})

    recent_out: list[dict[str, Any]] = []
    for msg in recent:
        role = str(msg.get("role") or "user")
        text = _msg_text(msg)
        if role == "user" and ("执行结果" in text or "工具结果" in text):
            recent_out.append({"role": role, "content": _compact_recent_tool_msg(text)})
        else:
            recent_out.append(dict(msg))

    out = summarized + recent_out
    # 仍超限：从最旧摘要继续砍
    while out and sum(_msg_chars(m) for m in out) > limit:
        if len(out) <= max(2, keep // 2 or 1):
            # 保近窗，截断最旧一条
            oldest = out[0]
            text = _msg_text(oldest)
            if len(text) > 160:
                out[0] = {
                    "role": oldest.get("role") or "user",
                    "content": text[:159] + "…",
                }
            break
        out.pop(0)
    return out


def _prefix_hash(msgs: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for msg in msgs:
        digest.update(str(msg.get("role") or "").encode())
        digest.update(b"\0")
        digest.update(_msg_text(msg).encode())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def _compact_excerpt(older: list[dict[str, Any]], *, limit: int = 12000) -> str:
    parts = [f"[{msg.get('role') or 'user'}]\n{_msg_text(msg)}" for msg in older]
    text = "\n\n".join(parts)
    if len(text) <= limit:
        return text
    return "…（更早内容已截断）\n" + text[-limit:]


def _fit_plan_history(
    out: list[dict[str, Any]], *, limit: int, keep: int
) -> list[dict[str, Any]]:
    while out and sum(_msg_chars(m) for m in out) > limit:
        if len(out) <= max(2, keep // 2 or 1):
            oldest = out[0]
            text = _msg_text(oldest)
            if len(text) > 160:
                out[0] = {
                    "role": oldest.get("role") or "user",
                    "content": text[:159] + "…",
                }
            break
        out.pop(0)
    return out


def plan_history(
    history: list[dict[str, Any]] | None,
    *,
    cache: dict[str, Any] | None = None,
    summarize: Summarize | None = None,
    keep_recent: int | None = None,
    max_chars: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """规划用历史。超窗时优先用 compact 摘要；失败则退回机械远窗。

    cache 为 ``{"prefix_hash", "text"}``。同一段旧历史不重复摘要。
    """
    msgs = list(history or [])
    kept = dict(cache or {})
    if not msgs:
        return [], kept
    keep = history_keep_recent() if keep_recent is None else max(0, keep_recent)
    limit = history_max_chars() if max_chars is None else max(0, max_chars)
    total = sum(_msg_chars(m) for m in msgs)
    if keep <= 0:
        keep = len(msgs)
    if total <= limit and len(msgs) <= keep + 2:
        return msgs, kept
    if len(msgs) <= keep:
        older: list[dict[str, Any]] = []
        recent = msgs
    else:
        older = msgs[:-keep]
        recent = msgs[-keep:]
    older = _dedupe_older_reads(older)
    prefix = _prefix_hash(older) if older else ""
    compact_text = ""
    if (
        older
        and prefix
        and str(kept.get("prefix_hash") or "") == prefix
        and str(kept.get("text") or "").strip()
    ):
        compact_text = str(kept.get("text") or "").strip()
    elif older and summarize is not None:
        try:
            got = summarize(_compact_excerpt(older))
        except Exception:
            got = None
        if isinstance(got, str) and got.strip():
            compact_text = durable_memory_text(got.strip())[:1500]
            if compact_text:
                kept = {"prefix_hash": prefix, "text": compact_text}
            else:
                compact_text = ""
    if not compact_text:
        return (
            compress_history_for_plan(msgs, keep_recent=keep, max_chars=limit),
            kept if kept.get("text") else dict(cache or {}),
        )
    head = {
        "role": "user",
        "content": "【已完成步骤摘要】\n" + compact_text + "\n" + _COMPACT_NOTE,
    }
    out = _fit_plan_history([head] + _recent_for_plan(recent), limit=limit, keep=keep)
    return out, kept


def _recent_for_plan(recent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recent_out: list[dict[str, Any]] = []
    for msg in recent:
        role = str(msg.get("role") or "user")
        text = _msg_text(msg)
        if role == "user" and ("执行结果" in text or "工具结果" in text):
            recent_out.append({"role": role, "content": _compact_recent_tool_msg(text)})
        else:
            recent_out.append(dict(msg))
    return recent_out
