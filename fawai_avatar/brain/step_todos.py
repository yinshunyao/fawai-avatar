# term:规划历史窗  doc:doc/功能/编码闭环/说明.md
"""简单目标的步骤待办：待办 / 进行中 / 已完成。不进入可被压缩的历史。"""

from __future__ import annotations

from typing import Any

from fawai_avatar.brain.durable_memory import durable_memory_text

_STATUSES = frozenset({"待办", "进行中", "已完成"})
_ALIAS = {
    "pending": "待办",
    "todo": "待办",
    "in_progress": "进行中",
    "doing": "进行中",
    "completed": "已完成",
    "done": "已完成",
}
_MAX_ITEMS = 8
_MAX_TEXT = 40


def task_keeps_step_todos(meta: dict[str, Any] | None) -> bool:
    """有任务图的监管脑不再维护这份清单，节点状态即完成清单。"""
    data = meta or {}
    if str(data.get("plan_role") or "") == "supervisor":
        return False
    if str(data.get("plan_node_id") or "").strip():
        return False
    return True


def normalize_todos(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            text, status = item, "待办"
        elif isinstance(item, dict):
            text = str(
                item.get("text") or item.get("content") or item.get("title") or ""
            )
            status = str(item.get("status") or "待办").strip()
            status = _ALIAS.get(status, status)
        else:
            continue
        if status not in _STATUSES:
            status = "待办"
        text = durable_memory_text(text, with_note=False).replace("\n", " ").strip()[
            :_MAX_TEXT
        ]
        if not text:
            continue
        out.append({"text": text, "status": status})
        if len(out) >= _MAX_ITEMS:
            break
    return out


def format_todo_prompt(todos: list[dict[str, str]] | None) -> str:
    lines = [
        "【步骤待办】这份清单单独注入，不在历史里，不会被压缩丢掉。",
        "状态只有待办、进行中、已完成。",
    ]
    items = list(todos or [])
    if not items:
        lines.append("（当前为空。需要分步时在本轮 JSON 写出 todos。）")
    else:
        for item in items:
            lines.append(f"- [{item.get('status')}] {item.get('text')}")
    lines.append(
        '本轮可更新："todos":[{"text":"≤40字","status":"待办|进行中|已完成"}]，最多 8 条。'
        "已完成的留下。没有变化就照抄上一份。不要写口令。"
        "清单只说明哪一步做过；摘要里没有的事实仍须定向再读，不要因此 failed。"
    )
    return "\n".join(lines)
