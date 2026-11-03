# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
"""任务短标题：启发式兜底 + LLM 首轮命名规范化。"""
from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")
_PREFIX_RE = re.compile(
    r"^(请|帮我|麻烦|想|需要|帮忙)?"
    r"(一下|下)?"
)
_PUNCT_EDGE = re.compile(r"^[\s，,。．\.！!？\?：:;；、\-—]+|[\s，,。．\.！!？\?：:;；、\-—]+$")


def normalize_task_title(raw: str, *, max_len: int = 24) -> str:
    """压缩空白、去首尾标点，截到短标题长度。"""
    text = _WS_RE.sub(" ", (raw or "").strip())
    text = _PUNCT_EDGE.sub("", text)
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def suggest_task_title(goal: str, *, max_len: int = 24) -> str:
    """从 goal 抽主题短名（创建时兜底；未等 LLM）。"""
    text = _WS_RE.sub(" ", (goal or "").strip())
    if not text:
        return "未命名任务"
    # 常见口语前缀略收
    text = _PREFIX_RE.sub("", text).strip() or text
    # 问句取问号前；否则取首句
    for sep in ("？", "?", "。", "！", "!", "\n"):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if len(head) >= 4:
                text = head
                break
    titled = normalize_task_title(text, max_len=max_len)
    return titled or "未命名任务"


def title_from_plan_payload(data: dict | None, *, max_len: int = 24) -> str:
    """从规划 JSON 取 title / task_title。"""
    if not isinstance(data, dict):
        return ""
    raw = data.get("title")
    if raw is None:
        raw = data.get("task_title")
    return normalize_task_title(str(raw or ""), max_len=max_len)
