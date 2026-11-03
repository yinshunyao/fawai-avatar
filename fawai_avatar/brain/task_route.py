# term:任务线程  doc:doc/功能/会话通道/说明.md
# term:追问  doc:doc/功能/会话通道/说明.md
"""线程内跟进路由：同一任务续跑 vs 新建任务；执行中追加分析（LLM；mock 可离线）。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from fawai_avatar.task.models import TaskRecord

RouteAction = Literal["continue", "new_task"]
AppendAction = Literal["apply", "new_task"]

_NEW_TASK_MARKERS = ("新任务", "另外一件", "另一件事", "无关的", "先别管这个", "下一个任务", "别的事")


def route_followup(
    task: TaskRecord,
    user_text: str,
    *,
    recent_notes: str = "",
) -> dict[str, Any]:
    """
    在用户已打开某任务线程的前提下，判断本条是续跑还是新开任务。

    返回 {"action": "continue"|"new_task", "reason": str, "source": "llm"|"mock"|"fallback"}
    """
    text = (user_text or "").strip()
    if not text:
        return {"action": "continue", "reason": "空跟进，默认续跑", "source": "fallback"}

    if _mock_enabled():
        return _mock_route(task, text)

    try:
        from fawai_avatar.llm.client import chat
    except ImportError:
        return _mock_route(task, text) | {"source": "mock"}

    final = str((task.meta or {}).get("final_thought") or "")[:400]
    err = str((task.meta or {}).get("error") or "")[:200]
    system = (
        "你是操作分身的任务路由。用户已打开某一任务线程并发送新消息。"
        "根据原任务目标、状态与新消息，判断应续跑同一任务还是另开新任务。"
        "只输出一个 JSON 对象，不要其它文字。字段："
        '{"action":"continue"|"new_task","reason":"不超过40字"}。'
        "规则："
        "1) 同一目标的换法、补救、澄清、追加约束 → continue；"
        "2) 明显另一件事/新目标/用户明示新开 → new_task；"
        "3) 不确定时优先 continue（用户已主动留在该线程）。"
    )
    user = (
        f"任务状态: {task.status}\n"
        f"原目标:\n{task.goal[:800]}\n"
        f"终态结论: {final or '（无）'}\n"
        f"错误摘要: {err or '（无）'}\n"
        f"近期备注: {(recent_notes or '（无）')[:400]}\n"
        f"用户本轮:\n{text[:800]}\n"
    )
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=None,
        tool_choice=None,
    )
    if not resp.get("ok"):
        out = _mock_route(task, text)
        out["reason"] = f"LLM 不可用，规则兜底：{out['reason']}"
        out["source"] = "fallback"
        return out

    parsed = _parse_json_object(str(resp.get("content") or ""))
    action = str((parsed or {}).get("action") or "").strip().lower()
    reason = str((parsed or {}).get("reason") or "").strip() or "模型未给理由"
    if action not in {"continue", "new_task"}:
        out = _mock_route(task, text)
        out["reason"] = f"模型输出无效，规则兜底：{out['reason']}"
        out["source"] = "fallback"
        return out
    return {"action": action, "reason": reason[:80], "source": "llm"}


def analyze_running_append(
    task: TaskRecord,
    user_text: str,
    *,
    recent_steps_summary: str = "",
) -> dict[str, Any]:
    """
    任务执行中用户追加：结合状态、原目标、步骤摘要与增量，判断 apply 或 new_task。

    返回 {"action":"apply"|"new_task","instruction":str,"reason":str,"source":...}
    """
    text = (user_text or "").strip()
    if not text:
        return {
            "action": "apply",
            "instruction": "",
            "reason": "空追加，忽略",
            "source": "fallback",
        }

    if _mock_enabled():
        return _mock_append(task, text)

    try:
        from fawai_avatar.llm.client import chat
    except ImportError:
        return _mock_append(task, text) | {"source": "mock"}

    final = str((task.meta or {}).get("final_thought") or "")[:300]
    err = str((task.meta or {}).get("error") or "")[:200]
    system = (
        "你是操作分身的执行中追加分析器。任务正在执行，用户在同一线程追加了信息。"
        "结合原目标、当前状态、近期步骤与追加内容，判断应并入当前任务还是另开新任务。"
        "只输出一个 JSON 对象，不要其它文字。字段："
        '{"action":"apply"|"new_task","instruction":"给执行器的短指令（apply时必填，≤80字）",'
        '"reason":"不超过40字"}。'
        "规则："
        "1) 同一目标的澄清、改约束、补充材料、纠偏 → apply，并写清 instruction；"
        "2) 明显另一件事/新目标/用户明示新开 → new_task，instruction 可空；"
        "3) 不确定时优先 apply。"
    )
    user = (
        f"任务状态: {task.status}\n"
        f"原目标:\n{task.goal[:800]}\n"
        f"近期步骤:\n{(recent_steps_summary or '（无）')[:600]}\n"
        f"终态/备注: {final or '（无）'}\n"
        f"错误摘要: {err or '（无）'}\n"
        f"用户追加:\n{text[:800]}\n"
    )
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=None,
        tool_choice=None,
    )
    if not resp.get("ok"):
        out = _mock_append(task, text)
        out["reason"] = f"LLM 不可用，规则兜底：{out['reason']}"
        out["source"] = "fallback"
        return out

    parsed = _parse_json_object(str(resp.get("content") or ""))
    action = str((parsed or {}).get("action") or "").strip().lower()
    reason = str((parsed or {}).get("reason") or "").strip() or "模型未给理由"
    instruction = str((parsed or {}).get("instruction") or "").strip()
    if action not in {"apply", "new_task"}:
        out = _mock_append(task, text)
        out["reason"] = f"模型输出无效，规则兜底：{out['reason']}"
        out["source"] = "fallback"
        return out
    if action == "apply" and not instruction:
        instruction = text[:80]
    return {
        "action": action,
        "instruction": instruction[:120],
        "reason": reason[:80],
        "source": "llm",
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


def _mock_enabled() -> bool:
    return os.environ.get("FAWAI_BRAIN_MOCK", "").strip().lower() in {"1", "true", "yes"}


def _mock_route(task: TaskRecord, text: str) -> dict[str, Any]:
    if any(m in text for m in _NEW_TASK_MARKERS):
        return {"action": "new_task", "reason": "话术明示另开任务", "source": "mock"}

    # 已在该线程发言：默认续跑（含失败后换法）
    if task.status in {"completed", "failed", "cancelled", "pending", "paused", "waiting", "blocked"}:
        return {
            "action": "continue",
            "reason": "同线程跟进，默认续跑原任务",
            "source": "mock",
        }
    return {"action": "continue", "reason": "默认续跑", "source": "mock"}


def _mock_append(task: TaskRecord, text: str) -> dict[str, Any]:
    if any(m in text for m in _NEW_TASK_MARKERS):
        return {
            "action": "new_task",
            "instruction": "",
            "reason": "话术明示另开任务",
            "source": "mock",
        }
    return {
        "action": "apply",
        "instruction": text[:80],
        "reason": "执行中追加，默认并入当前任务",
        "source": "mock",
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
