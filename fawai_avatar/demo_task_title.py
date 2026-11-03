# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
"""任务短标题：启发式 + 规划字段解析冒烟。"""
from __future__ import annotations

from fawai_avatar.brain.planner import _plan_from_payload
from fawai_avatar.task.title import (
    normalize_task_title,
    suggest_task_title,
    title_from_plan_payload,
)


def main() -> None:
    goal = "web对话窗口支持直接粘贴图片么？ 不支持需要修改支持。"
    title = suggest_task_title(goal)
    assert "粘贴" in title or "图片" in title or "对话" in title, title
    assert len(title) <= 24, title
    assert "54684c6e" not in title

    assert normalize_task_title("  粘贴图片支持  ") == "粘贴图片支持"
    assert title_from_plan_payload({"title": "Web粘贴图片"}) == "Web粘贴图片"
    assert title_from_plan_payload({"task_title": "侧栏命名"}) == "侧栏命名"
    assert title_from_plan_payload({}) == ""

    plan = _plan_from_payload(
        {
            "thought": "先找前端文件",
            "title": "Web粘贴图片",
            "status": "continue",
            "done": False,
            "observe_kinds": ["obs_fs"],
            "actions": [{"kind": "fs_glob", "args": {"pattern": "**/*.html"}}],
        }
    )
    assert plan.title == "Web粘贴图片", plan.title
    print("demo_task_title: ok", title)


if __name__ == "__main__":
    main()
