# term:规划提示档  doc:doc/功能/规划提示档/说明.md
"""规划提示档：推断与体积冒烟（无真 LLM）。"""

from __future__ import annotations

import os

from fawai_avatar.brain.planner import _system_prompt
from fawai_avatar.brain.prompt_profile import (
    infer_prompt_profile,
    profile_full,
)


def main() -> None:
    goal_img = (
        "如图会话页面不支持markdown格式文档展示吧， 优化一下支持。\n"
        "[image:data/session_media/demo.png]"
    )
    p0 = infer_prompt_profile(goal_img, history=None)
    assert p0.id == "vision", p0.id
    assert p0.attach_user_images is True
    assert p0.compact_capabilities is True

    hist = [
        {"role": "assistant", "content": "先 glob"},
        {
            "role": "user",
            "content": "执行结果（工具结果观察）\n- fs_glob → ok: glob 1\n  paths: index.html",
        },
    ]
    p1 = infer_prompt_profile(goal_img, history=hist)
    assert p1.id == "fs", p1.id
    assert p1.attach_user_images is False

    gui = infer_prompt_profile("用鼠标点击微信窗口发送你好", history=None)
    assert gui.id == "gui", gui.id

    full_prompt = _system_prompt(coding=False, goal="随便问问", profile=profile_full(coding=False))
    vision_prompt = _system_prompt(coding=False, goal=goal_img, profile=p0)
    fs_prompt = _system_prompt(coding=False, goal=goal_img, profile=p1)
    assert len(vision_prompt) < len(full_prompt) * 0.55, (
        len(vision_prompt),
        len(full_prompt),
    )
    assert len(fs_prompt) < len(full_prompt) * 0.65, (len(fs_prompt), len(full_prompt))
    assert "thought ≤40" in vision_prompt
    assert "mermaid" in vision_prompt
    assert "截本机屏幕与任务无关" in vision_prompt
    from fawai_avatar.brain.prompt_profile import (
        report_delivery_rule,
        user_specifies_report_form,
    )

    assert user_specifies_report_form("一句话告诉我结论，不要图表")
    assert "不要另加图表" in report_delivery_rule("一句话告诉我结论，不要图表")
    assert "mermaid" in report_delivery_rule("总结这份文档的流程")
    assert "本轮提示档：vision" in vision_prompt
    assert "本轮提示档：fs" in fs_prompt
    assert "{html,js}" in fs_prompt
    for prompt in (full_prompt, vision_prompt, fs_prompt):
        assert "工作区根（可写）" in prompt
        assert "只读根：" in prompt
        assert prompt.index("工作区根（可写）") < prompt.index("当前操作系统")

    prev = os.environ.get("FAWAI_READ_ROOTS")
    os.environ["FAWAI_READ_ROOTS"] = "work"
    try:
        from fawai_avatar.workspace.paths import workspace_paths_prompt_block

        only_work = workspace_paths_prompt_block()
    finally:
        if prev is None:
            os.environ.pop("FAWAI_READ_ROOTS", None)
        else:
            os.environ["FAWAI_READ_ROOTS"] = prev
    assert "只读根：无（仅工作区）" in only_work, only_work

    print(
        "demo_prompt_profile: ok",
        {
            "full": len(full_prompt),
            "vision": len(vision_prompt),
            "fs": len(fs_prompt),
        },
    )


if __name__ == "__main__":
    main()
