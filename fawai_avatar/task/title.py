# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
"""任务短标题：启发式兜底 + LLM 首轮命名规范化。"""
from __future__ import annotations

import re
from typing import Any

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


def can_replace_task_title(
    *,
    existing: str,
    existing_source: str,
    proposed: str,
    proposed_source: str,
) -> bool:
    """是否应用新标题：空/启发式可被覆盖；已有 llm 正式名不覆盖。"""
    named = normalize_task_title(proposed)
    if not named:
        return False
    cur = normalize_task_title(existing)
    cur_src = (existing_source or "").strip() or ("heuristic" if cur else "")
    new_src = (proposed_source or "").strip() or "heuristic"
    if not cur:
        return True
    if cur == named and cur_src == new_src:
        return False
    if cur_src == "llm" and new_src != "llm":
        return False
    if cur_src == "llm" and new_src == "llm":
        # 会话已有正式短标题：不因后续子节点再命名而漂移
        return False
    return True


def commit_task_title(
    supervisor: Any,
    task_id: str,
    title: str,
    *,
    source: str,
) -> str:
    """
    把短标题写入任务 meta.title（会话列表真源）。
    返回实际落盘标题；未写入则空串。
    """
    tid = (task_id or "").strip()
    named = normalize_task_title(title)
    if not tid or not named or supervisor is None:
        return ""
    task = supervisor.get_task(tid)
    if task is None:
        return ""
    meta = dict(task.meta or {})
    existing = str(meta.get("title") or "")
    existing_source = str(meta.get("title_source") or "")
    if not can_replace_task_title(
        existing=existing,
        existing_source=existing_source,
        proposed=named,
        proposed_source=source,
    ):
        return ""
    supervisor.merge_task_meta(
        tid,
        {"title": named, "title_source": (source or "").strip() or "heuristic"},
    )
    return named


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


_STEP_LABEL_PLACEHOLDERS = frozenset(
    {
        "native tool_calls",
        "native tool call",
        "tool_calls",
        "tool call",
    }
)


def _is_step_label_placeholder(text: str) -> bool:
    raw = _WS_RE.sub(" ", (text or "").strip()).lower()
    if not raw:
        return True
    if raw in _STEP_LABEL_PLACEHOLDERS:
        return True
    # 截断展示常见形态：native tool_cal…
    if raw.startswith("native tool_cal"):
        return True
    return False


def resolve_step_label(
    *,
    step_label: str = "",
    thought: str = "",
    actions: list | None = None,
    max_len: int = 16,
) -> str:
    """显式 step_label → thought 首句 → 动作种类 兜底。

    跳过 native tool_calls 等占位 thought，避免簇头显示成工具通道内部名。
    """
    labeled = normalize_step_label(step_label, max_len=max_len)
    if labeled and not _is_step_label_placeholder(labeled):
        return labeled
    thought_text = _WS_RE.sub(" ", (thought or "").strip())
    if thought_text and not _is_step_label_placeholder(thought_text):
        for sep in ("。", "；", ";", "\n", "！", "!", "？", "?"):
            if sep in thought_text:
                head = thought_text.split(sep, 1)[0].strip()
                if head:
                    thought_text = head
                    break
        if not _is_step_label_placeholder(thought_text):
            from_thought = normalize_step_label(thought_text, max_len=max_len)
            if from_thought and not _is_step_label_placeholder(from_thought):
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
