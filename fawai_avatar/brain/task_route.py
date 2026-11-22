# term:任务线程  doc:doc/功能/01-接入与呈现/02-会话通道/说明.md
# term:追问  doc:doc/功能/01-接入与呈现/02-会话通道/说明.md
"""线程内跟进：一对话一任务——有焦点线程则续跑/并入，不拆 sibling。"""
from __future__ import annotations

from typing import Any, Literal

from fawai_avatar.task.models import TaskRecord

RouteAction = Literal["continue", "new_task"]
AppendAction = Literal["apply", "new_task"]


def route_followup(
    task: TaskRecord,
    user_text: str,
    *,
    recent_notes: str = "",
) -> dict[str, Any]:
    """有焦点线程时一律 continue（一对话一任务，不做自动拆分）。"""
    _ = (task, recent_notes)
    text = (user_text or "").strip()
    if not text:
        return {"action": "continue", "reason": "空跟进，续跑", "source": "policy"}
    return {
        "action": "continue",
        "reason": "一对话一任务，同线程续跑",
        "source": "policy",
    }


def analyze_running_append(
    task: TaskRecord,
    user_text: str,
    *,
    recent_steps_summary: str = "",
) -> dict[str, Any]:
    """执行中追加一律 apply 并入当前任务（不拆 sibling）。"""
    _ = (task, recent_steps_summary)
    text = (user_text or "").strip()
    if not text:
        return {
            "action": "apply",
            "instruction": "",
            "reason": "空追加，忽略",
            "source": "policy",
        }
    return {
        "action": "apply",
        "instruction": text[:80],
        "reason": "一对话一任务，执行中追加并入",
        "source": "policy",
    }


def summarize_recent_steps(task: TaskRecord, *, limit: int = 8) -> str:
    """从 meta.steps 抽近期阶段文案，供追加分析。"""
    steps = list((task.meta or {}).get("steps") or [])
    if not steps:
        return ""
    lines: list[str] = []
    for item in steps[-limit:]:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase") or item.get("kind") or "").strip()
        text = str(item.get("text") or item.get("message") or "").strip()
        bit = " · ".join(p for p in (phase, text) if p)
        if bit:
            lines.append(bit[:120])
    return "\n".join(lines)
