# term:观察选取  doc:doc/功能/观察/说明.md
"""观察选取 + 工具结果压缩冒烟（不调 LLM、不真截屏）。"""
from __future__ import annotations

from fawai_avatar.observe.methods import (
    candidate_observe_methods,
    choose_observe_methods,
    collect_observation,
    resolve_observe_methods,
)
from fawai_avatar.observe.tool_digest import digest_text, format_tool_observation
from fawai_avatar.types import Action, ExecResult


def main() -> None:
    paste_goal = "web对话窗口支持直接粘贴图片么？ 不支持需要修改支持。"
    assert _looks_structured(paste_goal), "产品能力目标应进结构化闸门"
    assert choose_observe_methods(paste_goal, capture=True) == ["obs_fs"]
    assert "obs_screen" not in candidate_observe_methods(paste_goal, capture=True)

    resolved = resolve_observe_methods(
        paste_goal, capture=True, requested=["obs_screen", "obs_fs"]
    )
    assert resolved == ["obs_fs"], resolved  # screen 不在候选，被丢掉

    gui_goal = "点击桌面计算器上的按钮"
    cands = candidate_observe_methods(gui_goal, capture=True)
    assert "obs_screen" in cands
    assert choose_observe_methods(gui_goal, capture=True) == ["none"]
    assert resolve_observe_methods(gui_goal, capture=True, requested=["obs_fs"]) == [
        "obs_fs"
    ]
    # LLM 点选截屏仍生效；步 0 无点选不默截
    assert resolve_observe_methods(
        gui_goal, capture=True, requested=["obs_screen"]
    ) == ["obs_screen"]

    cursor_goal = "cursor ide现在打开的任务有哪些，状态如何"
    assert not _looks_structured(cursor_goal)
    assert choose_observe_methods(cursor_goal, capture=True) == ["none"]
    assert "obs_screen" in candidate_observe_methods(cursor_goal, capture=True)

    mock_frontmost = lambda: {  # noqa: E731 — 冒烟注入
        "ok": True,
        "app": "Google Chrome",
        "window": "ASQ Creator",
        "reason": None,
        "platform": "Darwin",
    }
    obs = collect_observation(
        ["none"],
        goal=cursor_goal,
        capture=True,
        frontmost_probe=mock_frontmost,
    )
    assert obs.image_path is None
    assert "前台窗摘要" in obs.summary
    assert obs.meta.get("frontmost_ok") is True
    assert obs.meta.get("frontmost_app") == "Google Chrome"
    assert obs.meta.get("frontmost_window") == "ASQ Creator"

    huge = "INFO start\n" + ("noise line\n" * 5000) + "ERROR Traceback boom\n" + (
        "tail noise\n" * 100
    )
    digested, meta = digest_text(huge, goal=paste_goal, label="stdout", data_dir="data")
    assert meta.get("compressed") is True
    assert meta.get("dump_path")
    assert "已压缩" in digested
    assert "ERROR" in digested or "boom" in digested
    assert len(digested) < len(huge)

    text, tmeta = format_tool_observation(
        [Action(kind="shell", args={"cmd": "cat huge.log"})],
        [ExecResult(ok=True, message="ok", data={"stdout": huge})],
        goal=paste_goal,
        data_dir="data",
    )
    assert "工具结果观察" in text
    assert tmeta.get("compressed") is True

    # fs_grep 返回 hits 时必须回灌 path:line，不能只留「grep N」
    grep_text, _ = format_tool_observation(
        [
            Action(
                kind="fs_grep",
                args={"pattern": "paste", "path": "fawai_avatar/channel/static/index.html"},
            )
        ],
        [
            ExecResult(
                ok=True,
                message="grep 1",
                data={
                    "hits": [
                        {
                            "path": "fawai_avatar/channel/static/index.html",
                            "line": 120,
                            "text": "addEventListener('paste', onPaste)",
                            "before": [{"line": 119, "text": "// compose"}],
                            "after": [{"line": 121, "text": "  e.preventDefault();"}],
                        }
                    ]
                },
            )
        ],
        goal=paste_goal,
        data_dir="data",
    )
    assert "hits" in grep_text
    assert "fawai_avatar/channel/static/index.html:120:" in grep_text
    assert "addEventListener('paste'" in grep_text

    from fawai_avatar.brain.planner import _system_prompt
    from fawai_avatar.observe.methods import progress_tool_observe_text

    # f-string 提示词不得把 {html,js} 当表达式求值（历史 NameError 死循环）
    prompt = _system_prompt(coding=True, goal=paste_goal)
    assert "{html,js}" in prompt
    assert "NameError" not in prompt
    assert "前台窗摘要" in prompt

    shown = progress_tool_observe_text(
        pending=False, compressed=False, preview=grep_text
    )
    assert "观察工具结果完成" in shown
    assert "fawai_avatar/channel/static/index.html:120:" in shown
    print("demo_observe_select: ok")


def _looks_structured(goal: str) -> bool:
    from fawai_avatar.observe.methods import _looks_like_structured_observe_goal

    return _looks_like_structured_observe_goal(goal)


if __name__ == "__main__":
    main()
