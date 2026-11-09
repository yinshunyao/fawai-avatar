# term:观察选取  doc:doc/功能/观察/说明.md
"""观察选取 + 工具结果压缩冒烟（不调 LLM、不真截屏）。"""
from __future__ import annotations

from fawai_avatar.observe.methods import (
    candidate_observe_methods,
    choose_observe_methods,
    collect_observation,
    goal_screen_unrelated,
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

    # 会话名含「文件」不得当结构化：否则点选截屏会被静默降成 obs_fs
    chat_goal = "发消息给微信的文件传输助手，发送：你好"
    assert not _looks_structured(chat_goal)
    assert "obs_screen" in candidate_observe_methods(chat_goal, capture=True)
    assert "obs_ax" in candidate_observe_methods(chat_goal, capture=True)
    assert resolve_observe_methods(
        chat_goal, capture=True, requested=["obs_screen"]
    ) == ["obs_screen"]
    assert resolve_observe_methods(
        chat_goal, capture=True, requested=["obs_ax"]
    ) == ["obs_ax"]
    assert choose_observe_methods(chat_goal, capture=True) == ["none"]

    doc_goal = "总结这张说明文档里的流程，图片内容如下\n[image:data/session_media/doc.png]"
    assert goal_screen_unrelated(doc_goal)
    assert "obs_screen" not in candidate_observe_methods(doc_goal, capture=True)
    assert "obs_ax" not in candidate_observe_methods(doc_goal, capture=True)
    assert choose_observe_methods(doc_goal, capture=True) == ["none"]
    assert resolve_observe_methods(
        doc_goal, capture=True, requested=["obs_screen"]
    ) == ["none"]

    pdf_goal = "根据说明.pdf 整理章节结构"
    assert "obs_screen" not in candidate_observe_methods(pdf_goal, capture=True)
    assert choose_observe_methods(pdf_goal, capture=True) == ["none"]

    click_doc = "点击文档窗口里的保存按钮"
    assert not goal_screen_unrelated(click_doc)
    assert "obs_screen" in candidate_observe_methods(click_doc, capture=True)

    cursor_goal = "cursor ide现在打开的任务有哪些，状态如何"
    assert not _looks_structured(cursor_goal)
    assert choose_observe_methods(cursor_goal, capture=True) == ["none"]
    assert "obs_screen" in candidate_observe_methods(cursor_goal, capture=True)

    # none 真跳过：不探前台、无 PNG
    obs = collect_observation(
        ["none"],
        goal=cursor_goal,
        capture=True,
    )
    assert obs.image_path is None
    assert obs.meta.get("skipped_env") is True
    assert obs.meta.get("frontmost_ok") is None
    assert "前台窗摘要" not in (obs.summary or "")
    assert isinstance(obs.meta.get("observe_candidates"), list)

    mock_frontmost = lambda: {  # noqa: E731 — 冒烟注入
        "ok": True,
        "app": "Google Chrome",
        "window": "ASQ Creator",
        "reason": None,
        "platform": "Darwin",
    }
    # 真实取证仍附前台窗
    fs_obs = collect_observation(
        ["obs_fs"],
        goal="打开 README.md 看目录结构",
        capture=True,
        frontmost_probe=mock_frontmost,
    )
    assert "前台窗摘要" in (fs_obs.summary or "")
    assert fs_obs.meta.get("frontmost_ok") is True
    assert fs_obs.meta.get("frontmost_app") == "Google Chrome"
    assert fs_obs.meta.get("frontmost_window") == "ASQ Creator"

    import os

    from fawai_avatar.observe.tool_digest import clip_head_tail, max_chars

    huge = "INFO start\n" + ("noise line\n" * 5000) + "ERROR Traceback boom\n" + (
        "tail noise\n" * 100
    )
    # soft=0 时中等体量仍全文；超过绝对上限才强制压缩
    mid = "head-marker\n" + ("mid\n" * 20) + "tail-marker\n"
    plain, plain_meta = digest_text(
        mid, goal=paste_goal, label="stdout", data_dir="data"
    )
    assert plain_meta.get("compressed") is not True
    assert plain == mid

    os.environ["FAWAI_TOOL_OBS_MAX_CHARS"] = "2000"
    os.environ["FAWAI_TOOL_OBS_SOFT_CHARS"] = "0"
    try:
        capped, cap_meta = digest_text(
            huge, goal=paste_goal, label="stdout", data_dir="data"
        )
    finally:
        os.environ.pop("FAWAI_TOOL_OBS_MAX_CHARS", None)
        os.environ.pop("FAWAI_TOOL_OBS_SOFT_CHARS", None)
    assert cap_meta.get("compressed") is True
    assert cap_meta.get("max_capped") is True
    assert "ERROR" in capped or "boom" in capped
    assert "INFO start" in capped
    assert "tail noise" in capped
    assert len(capped) < len(huge)

    digested, meta = digest_text(
        huge,
        goal=paste_goal,
        label="stdout",
        data_dir="data",
        soft_override=2000,
    )
    assert meta.get("compressed") is True
    assert meta.get("dump_path")
    assert "已压缩" in digested
    assert "ERROR" in digested or "boom" in digested
    assert len(digested) < len(huge)

    # 头+尾裁切：尾标记必须保留
    clipped, was = clip_head_tail(
        "HEAD_ONLY_ZONE\n" + ("x\n" * 4000) + "TAIL_ONLY_ZONE\n",
        max_chars=200,
    )
    assert was is True
    assert "HEAD_ONLY_ZONE" in clipped
    assert "TAIL_ONLY_ZONE" in clipped

    os.environ["FAWAI_TOOL_OBS_SOFT_CHARS"] = "2000"
    os.environ["FAWAI_TOOL_OBS_MAX_CHARS"] = "0"
    try:
        text, tmeta = format_tool_observation(
            [Action(kind="shell", args={"cmd": "cat huge.log"})],
            [ExecResult(ok=True, message="ok", data={"stdout": huge})],
            goal=paste_goal,
            data_dir="data",
        )
    finally:
        os.environ.pop("FAWAI_TOOL_OBS_SOFT_CHARS", None)
        os.environ.pop("FAWAI_TOOL_OBS_MAX_CHARS", None)
    assert "工具结果观察" in text
    assert tmeta.get("compressed") is True
    assert max_chars() >= 0

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

    # hits 默认全文回灌（不 soft 压缩、不截断条数）
    mid_hits = []
    for i in range(35):
        mid_hits.append(
            {
                "path": "fawai_avatar/channel/hub.py",
                "line": 100 + i,
                "text": f"cancelled marker line {i} " + ("x" * 40),
                "before": [{"line": 99 + i, "text": "ctx-before"}],
                "after": [{"line": 101 + i, "text": "ctx-after"}],
            }
        )
    mid_text, mid_meta = format_tool_observation(
        [
            Action(
                kind="fs_grep",
                args={
                    "pattern": "cancelled",
                    "path": "fawai_avatar",
                    "head_limit": 35,
                },
            )
        ],
        [ExecResult(ok=True, message="grep 35", data={"hits": mid_hits})],
        goal=paste_goal,
        data_dir="data",
    )
    assert mid_meta.get("compressed") is not True, mid_meta
    assert "已压缩" not in mid_text
    assert "【宽检索提示】" not in mid_text
    assert "hub.py:100:" in mid_text
    assert "hub.py:134:" in mid_text
    assert "已达本次 head_limit=35" in mid_text

    # fs_read：读多少回灌多少（默认不二次截断、不 soft 压缩）
    read_rows = [{"n": i, "text": f"body line {i} " + ("y" * 30)} for i in range(1, 81)]
    read_text, read_meta = format_tool_observation(
        [Action(kind="fs_read", args={"path": "notes/big.py", "offset": 1, "limit": 80})],
        [
            ExecResult(
                ok=True,
                message="read notes/big.py",
                data={"path": "notes/big.py", "lines": read_rows, "truncated": True},
            )
        ],
        goal=paste_goal,
        data_dir="data",
    )
    assert "80: body line 80" in read_text
    assert "仅展示前" not in read_text
    assert "已压缩" not in read_text
    assert read_meta.get("compressed") is not True, read_meta
    assert "文件后续未读完" in read_text

    from fawai_avatar.brain.planner import _system_prompt
    from fawai_avatar.observe.methods import (
        progress_observe_text,
        progress_tool_observe_text,
    )

    # f-string 提示词不得把花括号 glob 当表达式求值（历史 NameError 死循环）
    prompt = _system_prompt(coding=True, goal=paste_goal)
    assert "{a,b}" in prompt or "{yaml,json}" in prompt or "{html,js}" in prompt
    assert "NameError" not in prompt
    assert "前台窗摘要" in prompt or "obs_screen" in prompt

    shown = progress_tool_observe_text(
        pending=False, compressed=False, preview=grep_text
    )
    assert "观察工具结果完成" in shown
    assert "fawai_avatar/channel/static/index.html:120:" in shown

    # 文案工具仍可拼「观察完成（跳过）」；闭环对 none 不推卡
    env_shown = progress_observe_text(
        ["none"],
        pending=False,
        preview="（不应出现在时间线；仅测文案拼装）",
    )
    assert "观察完成（跳过）" in env_shown
    print("demo_observe_select: ok")


def _looks_structured(goal: str) -> bool:
    from fawai_avatar.observe.methods import _looks_like_structured_observe_goal

    return _looks_like_structured_observe_goal(goal)


if __name__ == "__main__":
    main()
