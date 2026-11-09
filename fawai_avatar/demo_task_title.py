# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
# term:步骤业务名  doc:doc/功能/会话通道/说明.md
"""任务短标题 + 步骤业务名：启发式与规划字段解析冒烟。"""
from __future__ import annotations

from fawai_avatar.brain.planner import _plan_from_payload
from fawai_avatar.brain.plan_scheduler import (
    best_child_llm_title,
    promote_title_to_plan_parent,
    resolve_session_title,
)
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.task.title import (
    can_replace_task_title,
    commit_task_title,
    normalize_step_label,
    normalize_task_title,
    resolve_step_label,
    step_label_from_plan_payload,
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

    assert can_replace_task_title(
        existing="1、压缩目录/Users…",
        existing_source="heuristic",
        proposed="压缩GlueGap数据",
        proposed_source="llm",
    )
    assert not can_replace_task_title(
        existing="压缩GlueGap数据",
        existing_source="llm",
        proposed="切换r34训练配置",
        proposed_source="llm",
    )

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        supervisor = Supervisor(tmp)
        parent_id = supervisor.create_task(
            "1、压缩目录/tmp/GlueGap\n2、上传训练",
            meta={
                "plan_role": "orchestrator",
                "title": "1、压缩目录/tmp/GlueGap",
                "title_source": "heuristic",
            },
        )
        child_id = supervisor.create_task(
            "压缩节点",
            meta={
                "plan_role": "supervisor",
                "plan_parent_task_id": parent_id,
                "title": "压缩GlueGap数据",
                "title_source": "llm",
            },
        )
        assert best_child_llm_title(supervisor, parent_id) == "压缩GlueGap数据"
        written = promote_title_to_plan_parent(
            supervisor, child_id, "压缩GlueGap数据", source="llm"
        )
        assert written == "压缩GlueGap数据", written
        parent = supervisor.get_task(parent_id)
        assert parent is not None
        assert (parent.meta or {}).get("title") == "压缩GlueGap数据"
        assert (parent.meta or {}).get("title_source") == "llm"
        # 再次提升不得覆盖
        assert (
            promote_title_to_plan_parent(
                supervisor, child_id, "切换r34训练配置", source="llm"
            )
            == ""
        )
        named, src = resolve_session_title(supervisor, parent)
        assert named == "压缩GlueGap数据" and src == "llm"
        assert commit_task_title(
            supervisor, parent_id, "其它名", source="heuristic"
        ) == ""

    assert step_label_from_plan_payload({"step_label": "查找转换脚本"}) == "查找转换脚本"
    assert step_label_from_plan_payload({"intent": "转 VOC XML"}) == "转 VOC XML"
    assert normalize_step_label("  查找脚本  ") == "查找脚本"
    from_thought = resolve_step_label(thought="先在目录里找 coco 转 VOC 脚本。再跑。")
    assert "找" in from_thought, from_thought
    assert len(from_thought) <= 16, from_thought
    assert (
        resolve_step_label(thought="查找转换脚本。再执行。") == "查找转换脚本"
    )
    assert resolve_step_label(
        actions=[{"kind": "fs_glob"}, {"kind": "fs_read"}]
    ) == "fs_glob、fs_read"
    assert (
        resolve_step_label(
            thought="native tool_calls",
            actions=[{"kind": "fs_grep"}, {"kind": "fs_glob"}],
        )
        == "fs_grep、fs_glob"
    )
    from fawai_avatar.brain.native_tools import plan_from_tool_calls

    native_plan = plan_from_tool_calls(
        [{"name": "fs_glob", "arguments_obj": {"pattern": "**/*.py"}}]
    )
    assert native_plan is not None
    assert "native" not in (native_plan.step_label or "").lower(), native_plan.step_label
    assert "fs_glob" in (native_plan.step_label or ""), native_plan.step_label

    plan = _plan_from_payload(
        {
            "thought": "先找前端文件",
            "title": "Web粘贴图片",
            "step_label": "定位粘贴入口",
            "status": "continue",
            "done": False,
            "observe_kinds": ["obs_fs"],
            "actions": [{"kind": "fs_glob", "args": {"pattern": "**/*.html"}}],
        }
    )
    assert plan.title == "Web粘贴图片", plan.title
    assert plan.step_label == "定位粘贴入口", plan.step_label

    derived = _plan_from_payload(
        {
            "thought": "在目录里找 VOC 转换脚本",
            "status": "continue",
            "done": False,
            "actions": [{"kind": "fs_grep", "args": {"pattern": "voc"}}],
        }
    )
    assert derived.step_label, derived.step_label
    assert "找" in derived.step_label or "VOC" in derived.step_label, derived.step_label
    print("demo_task_title: ok", title, plan.step_label, derived.step_label)


if __name__ == "__main__":
    main()
