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


def normalize_step_label(raw: str, *, max_len: int = 16) -> str:
    """本步业务意图短名（簇头用；默认 ≤16 字）。"""
    return normalize_task_title(raw, max_len=max_len)


def step_label_from_plan_payload(data: dict | None, *, max_len: int = 16) -> str:
    """从规划 JSON 取 step_label / step_name / intent。"""
    if not isinstance(data, dict):
        return ""
    for key in ("step_label", "step_name", "intent"):
        raw = data.get(key)
        if raw is None:
            continue
        labeled = normalize_step_label(str(raw), max_len=max_len)
        if labeled:
            return labeled
    return ""


def resolve_step_label(
    *,
    step_label: str = "",
    thought: str = "",
    actions: list | None = None,
    max_len: int = 16,
) -> str:
    """显式 step_label → thought 首句 → 动作种类 兜底。"""
    labeled = normalize_step_label(step_label, max_len=max_len)
    if labeled:
        return labeled
    thought_text = _WS_RE.sub(" ", (thought or "").strip())
    if thought_text:
        for sep in ("。", "；", ";", "\n", "！", "!", "？", "?"):
            if sep in thought_text:
                head = thought_text.split(sep, 1)[0].strip()
                if head:
                    thought_text = head
                    break
        from_thought = normalize_step_label(thought_text, max_len=max_len)
        if from_thought:
            return from_thought
    kinds: list[str] = []
    for item in actions or []:
        kind = ""
        if hasattr(item, "kind"):
            kind = str(getattr(item, "kind") or "").strip()
        elif isinstance(item, dict):
            kind = str(item.get("kind") or item.get("type") or "").strip()
        if kind and kind not in kinds:
            kinds.append(kind)
        if len(kinds) >= 3:
            break
    if kinds:
        return normalize_step_label("、".join(kinds), max_len=max_len)
    return ""
