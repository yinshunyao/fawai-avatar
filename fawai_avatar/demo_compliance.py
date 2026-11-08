# term:守规约束  doc:doc/功能/守规约束/说明.md
"""守规约束验收：规则命中、架构 doc_refs/skip、校验拒绝。"""

from __future__ import annotations

import os

from fawai_avatar.brain.capabilities import validate_plan
from fawai_avatar.brain.compliance import (
    annotate_plan_with_docs,
    ensure_architecture_decls,
    match_rules,
    plan_satisfies_architecture,
    rules_prompt_block,
    validate_architecture,
)
from fawai_avatar.brain.explore_progress import (
    explore_gate_should_abort,
    explore_gate_should_inject,
    is_explore_only_step,
    update_explore_only_streak,
)
from fawai_avatar.brain.native_tools import plan_from_tool_calls
from fawai_avatar.types import Action, ExecResult, Plan


def _write_control_plan(*, with_docs: bool = False, skip: str = "") -> Plan:
    plan = Plan(
        thought="patch control",
        actions=[
            Action(
                kind="fs_patch",
                args={
                    "path": "fawai_avatar/control/executor.py",
                    "old": "a",
                    "new": "b",
                },
                write_resources=["fawai_avatar/control/executor.py"],
            )
        ],
        done=False,
        status="continue",
        raw={},
    )
    if skip:
        raw = dict(plan.raw)
        raw["architecture_skip_reason"] = skip
        return Plan(
            thought=plan.thought,
            actions=list(plan.actions),
            done=plan.done,
            status=plan.status,
            raw=raw,
            observe_kinds=list(plan.observe_kinds),
        )
    if with_docs:
        return annotate_plan_with_docs(plan, ["doc/功能/控制/说明.md"])
    return plan


def main() -> None:
    print("== rules match ==")
    goal = "改代码：修补 fawai_avatar/control/executor.py"
    rules = match_rules(goal)
    assert rules, "expected matched rules"
    block = rules_prompt_block(goal)
    assert "守规约束" in block
    assert "arch_doc_refs" in block or "hard" in block
    print("[PASS] rules_prompt_block")

    print("== architecture reject ==")
    bad = _write_control_plan()
    err = validate_architecture(goal, bad)
    assert err, "expected architecture violation"
    err2 = validate_plan(goal, bad)
    assert err2 and "架构校验" in err2
    print("[PASS] reject without doc_refs")

    print("== architecture accept doc_refs ==")
    good = _write_control_plan(with_docs=True)
    assert validate_architecture(goal, good) is None
    assert validate_plan(goal, good) is None
    assert plan_satisfies_architecture(good)
    print("[PASS] doc_refs ok")

    print("== architecture accept skip ==")
    skipped = _write_control_plan(skip="仅修注释，文档下版再同步")
    assert validate_architecture(goal, skipped) is None
    assert validate_plan(goal, skipped) is None
    print("[PASS] architecture_skip_reason ok")

    print("== notes write no arch gate ==")
    notes = Plan(
        thought="write notes",
        actions=[
            Action(
                kind="fs_write",
                args={"path": "notes/hello.txt", "content": "hi"},
                write_resources=["notes/hello.txt"],
            )
        ],
        done=False,
        status="continue",
    )
    assert validate_architecture("写入 notes/hello.txt", notes) is None
    print("[PASS] notes path exempt")

    print("== native write auto-bind doc_refs ==")
    native = plan_from_tool_calls(
        [
            {
                "name": "fs_patch",
                "arguments_obj": {
                    "path": "fawai_avatar/channel/static/index.html",
                    "old": "a",
                    "new": "b",
                },
            }
        ]
    )
    assert native is not None
    assert validate_architecture("会话页支持 Markdown", native) is None
    assert plan_satisfies_architecture(native)
    assert (native.raw or {}).get("architecture_auto_bound") is True
    assert "doc/功能/会话通道/说明.md" in ((native.raw or {}).get("doc_refs") or [])
    print("[PASS] native auto-bind")

    print("== native write args lift doc_refs ==")
    lifted = plan_from_tool_calls(
        [
            {
                "name": "fs_patch",
                "arguments_obj": {
                    "path": "fawai_avatar/control/executor.py",
                    "old": "a",
                    "new": "b",
                    "doc_refs": ["doc/功能/控制/说明.md"],
                },
            }
        ]
    )
    assert lifted is not None
    assert validate_architecture(goal, lifted) is None
    assert (lifted.raw or {}).get("architecture_auto_bound") is not True
    print("[PASS] native args lift")

    print("== A: unwrap nested args JSON string ==")
    from fawai_avatar.brain.capabilities import apply_capability_defaults, normalize_tool_args

    nested_raw = {
        "args": (
            '{"path": "fawai_avatar/channel/static/index.html", '
            '"old": "</head>", "new": "<script></script>\\n</head>"}'
        )
    }
    flat = normalize_tool_args(nested_raw)
    assert flat.get("path") == "fawai_avatar/channel/static/index.html"
    assert "args" not in flat or flat.get("old") == "</head>"
    nested_plan = plan_from_tool_calls(
        [{"name": "fs_patch", "arguments_obj": nested_raw}]
    )
    assert nested_plan is not None
    act0 = nested_plan.actions[0]
    assert act0.args.get("path") == "fawai_avatar/channel/static/index.html"
    assert "fawai_avatar/channel/static/index.html" in act0.write_resources
    assert validate_architecture("会话页支持 Markdown", nested_plan) is None
    # JSON Action 路径同样展平
    json_act = apply_capability_defaults(
        Action(kind="fs_patch", args=dict(nested_raw), write_resources=[])
    )
    assert json_act.args.get("path") == "fawai_avatar/channel/static/index.html"
    assert json_act.write_resources
    print("[PASS] unwrap nested args")

    print("== json still rejects without docs ==")
    bare = ensure_architecture_decls(_write_control_plan(), auto_default=False)
    assert validate_architecture(goal, bare)
    print("[PASS] json strict")

    print("== explore progress helpers ==")
    from fawai_avatar.brain.explore_progress import (
        explore_gate_should_abort_after_force,
        explore_gate_should_force_mutate,
        explore_only_gate_threshold,
        format_mutate_force_message,
        format_verify_read_message,
        history_requests_mutate_prefer,
        history_requests_verify_read,
        hot_repeated_paths,
        update_explore_only_streak,
        update_read_path_counts,
        update_read_windows,
    )
    from fawai_avatar.brain.native_tools import openai_tools_for_coding

    read_act = [Action(kind="fs_read", args={"path": "x"}, write_resources=[])]
    ok = [ExecResult(ok=True, message="ok")]
    assert is_explore_only_step(read_act, ok)
    streak = update_explore_only_streak(
        7, goal="改代码优化 index.html", write_set=[], actions=read_act, results=ok
    )
    assert streak == 8
    assert explore_gate_should_inject(streak=8, gate=8)
    assert not explore_gate_should_abort(streak=8, abort_at=16)
    assert explore_gate_should_abort(streak=16, abort_at=16)
    # 默认产品：探索闸门 / 强制改写均关
    assert explore_only_gate_threshold(goal="会话页面支持 markdown 渲染") == 0
    assert explore_only_gate_threshold(goal="改代码优化 index.html") == 0
    from fawai_avatar.brain.explore_progress import explore_only_abort_threshold

    assert explore_only_abort_threshold(goal="改代码") == 0
    assert not explore_gate_should_inject(
        streak=8, gate=explore_only_gate_threshold(goal="改代码")
    )
    counts = {}
    for _ in range(3):
        counts = update_read_path_counts(
            counts, actions=read_act, results=ok
        )
    assert counts.get("x") == 3
    assert "x" in hot_repeated_paths(counts)
    boosted = update_explore_only_streak(
        4,
        goal="会话页面支持 markdown 渲染",
        write_set=[],
        actions=read_act,
        results=ok,
        read_path_counts=counts,
    )
    assert boosted == 6  # +1 步 +1 同 path 加速
    # 同 path 重叠行窗：第二次读与历史窗相交 → 额外 +1
    win_act1 = [
        Action(
            kind="fs_read",
            args={"path": "index.html", "offset": 100, "limit": 40},
            write_resources=[],
        )
    ]
    win_act2 = [
        Action(
            kind="fs_read",
            args={"path": "index.html", "offset": 120, "limit": 40},
            write_resources=[],
        )
    ]
    wins = {}
    wins = update_read_windows(wins, actions=win_act1, results=ok)
    overlap_streak = update_explore_only_streak(
        2,
        goal="会话页面支持 markdown 渲染",
        write_set=[],
        actions=win_act2,
        results=ok,
        read_path_counts={"index.html": 1},
        read_windows=wins,
    )
    assert overlap_streak == 4  # +1 步 +1 重叠窗
    assert explore_gate_should_force_mutate(
        streak=10, abort_at=10, write_set=[], mutate_forced=False
    )
    assert not explore_gate_should_force_mutate(
        streak=10, abort_at=10, write_set=[], mutate_forced=True
    )
    assert explore_gate_should_abort_after_force(
        streak=10,
        abort_at=10,
        write_set=[],
        mutate_forced=True,
        actions=read_act,
        results=ok,
    )
    assert "【探索改写强制】" in format_mutate_force_message(
        streak=10, gate=5, abort_at=10, repeated_paths=["x"]
    )
    assert history_requests_mutate_prefer(
        [{"role": "user", "content": "【探索进度闸门】下一步必须：fs_patch"}]
    )
    names = {t["function"]["name"] for t in openai_tools_for_coding(mutate_prefer=True)}
    assert "fs_patch" in names and "fs_read" not in names
    # 【反省开读】/【开读须改写】硬闸已退役：检测恒 False，且不再驱动 mutate_prefer
    assert not history_requests_verify_read(
        [
            {"role": "user", "content": "【探索进度闸门】下一步必须：fs_patch"},
            {"role": "user", "content": format_verify_read_message(paths=["a.py"])},
        ]
    )
    assert not history_requests_mutate_prefer(
        [{"role": "user", "content": format_verify_read_message(paths=["a.py"])}]
    )
    from fawai_avatar.brain.explore_progress import (
        extract_exact_old_windows,
        format_verify_must_patch_message,
        history_requests_verify_must_patch,
        is_blind_file_head_read,
    )

    assert "【开读须改写】" in format_verify_must_patch_message(
        paths=["a.py"], reason="开读后仍只读未 patch"
    )
    assert not history_requests_verify_must_patch(
        [
            {"role": "user", "content": format_verify_read_message(paths=["a.py"])},
            {
                "role": "user",
                "content": format_verify_must_patch_message(paths=["a.py"]),
            },
        ]
    )
    assert not history_requests_verify_read(
        [
            {"role": "user", "content": format_verify_read_message(paths=["a.py"])},
            {
                "role": "user",
                "content": format_verify_must_patch_message(paths=["a.py"]),
            },
        ]
    )
    assert is_blind_file_head_read(
        [Action(kind="fs_read", args={"path": "a.py", "offset": 1, "limit": 200})]
    )
    assert not is_blind_file_head_read(
        [Action(kind="fs_read", args={"path": "a.py", "offset": 640, "limit": 40})]
    )
    win_msg = (
        "old not found；【补丁锚点未接地】\n"
        "文件中与 old 记号最接近的原文小窗（须逐字作 old，勿改写）：\n"
        "663:    \"dimensions\": {},\n664:    \"items\": [],"
    )
    wins = extract_exact_old_windows([ExecResult(ok=False, message=win_msg)])
    assert wins and "663:" in wins[0]
    assert "原文小窗" in format_verify_read_message(
        paths=["a.py"], exact_windows=wins
    )
    names_vr = {
        t["function"]["name"]
        for t in openai_tools_for_coding(mutate_prefer=True, verify_read=True)
    }
    assert "fs_patch" in names_vr and "fs_read" in names_vr and "fs_grep" in names_vr

    from fawai_avatar.brain.explore_progress import (
        anchor_quality_score,
        explore_progress_armed,
        extract_locate_anchors,
        format_compressed_hits_message,
        format_compressed_unread_ban_message,
        format_locate_budget_message,
        format_locate_mutate_force_message,
        format_wide_grep_message,
        history_requests_mutate_prefer as history_mutate_prefer_locate,
        locate_budget_should_abort_after_force,
        locate_budget_should_force_mutate,
        locate_budget_should_inject,
        locate_budget_threshold,
        locate_force_threshold,
        merge_locate_anchors,
        preferred_locate_anchors,
        step_has_search_actions,
        step_repeats_banned_wide_grep,
        step_wide_grep_paths,
        update_post_locate_streak,
    )
    from fawai_avatar.brain.capabilities import looks_like_coding_goal

    grep_hits = ExecResult(
        ok=True,
        message="grep 2",
        data={
            "count": 2,
            "hits": [
                {
                    "path": "fawai_avatar/channel/static/index.html",
                    "line": 2847,
                    "text": '<script src="/assets/markdown-render.js">',
                }
            ],
        },
    )
    grep_act = [
        Action(
            kind="fs_grep",
            args={"path": "fawai_avatar/channel/static", "pattern": "markdown"},
            write_resources=[],
        )
    ]
    anchors = extract_locate_anchors(grep_act, [grep_hits])
    assert any("2847" in a for a in anchors), anchors
    anchors = merge_locate_anchors([], anchors)
    streak = 0
    for _ in range(3):
        streak = update_post_locate_streak(
            streak,
            goal="会话页面支持 markdown 渲染",
            locate_anchors=anchors,
            actions=grep_act,
            results=[grep_hits],
        )
    assert streak == 3
    # 默认产品：定位预算 / 强制改写均关——多轮只读不算违规
    assert locate_budget_threshold() == 0
    assert locate_force_threshold(budget=3) == 0
    assert not locate_budget_should_inject(streak=streak)  # 默认 budget=0
    assert locate_budget_should_inject(streak=streak, budget=3)  # 显式开启才注入
    assert "【定位预算】" in format_locate_budget_message(
        streak=3, budget=3, anchors=anchors
    )
    assert "不因只读步数强制" in format_locate_budget_message(
        streak=3, budget=3, anchors=anchors
    )
    # 口语 goal 漏检启发式时：有锚点仍计定位 streak（供显式开关使用）
    colloquial = "应该取消这个限制啊。修改一下代码"
    assert looks_like_coding_goal(colloquial)
    assert explore_progress_armed(goal="随便看看", locate_anchors=anchors)
    assert explore_progress_armed(
        goal="随便看看", locate_anchors=[], behavior_armed=True
    )
    colloquial_streak = 0
    for _ in range(3):
        colloquial_streak = update_post_locate_streak(
            colloquial_streak,
            goal="与编码无关的闲聊",
            locate_anchors=anchors,
            actions=grep_act,
            results=[grep_hits],
        )
    assert colloquial_streak == 3
    # 强制改写仅显式 FAWAI_LOCATE_FORCE_STEPS 开启
    import os

    _prev_force = os.environ.get("FAWAI_LOCATE_FORCE_STEPS")
    os.environ["FAWAI_LOCATE_FORCE_STEPS"] = "4"
    try:
        assert locate_force_threshold(budget=3) == 4
        assert locate_budget_should_force_mutate(
            streak=4, budget=3, write_set=[], mutate_forced=False
        )
        assert not locate_budget_should_force_mutate(
            streak=4, budget=3, write_set=[], mutate_forced=True
        )
        assert locate_budget_should_abort_after_force(
            streak=4,
            budget=3,
            write_set=[],
            mutate_forced=True,
            actions=grep_act,
            results=[grep_hits],
        )
        assert "【定位改写强制】" in format_locate_mutate_force_message(
            streak=4, budget=3, anchors=anchors
        )
    finally:
        if _prev_force is None:
            os.environ.pop("FAWAI_LOCATE_FORCE_STEPS", None)
        else:
            os.environ["FAWAI_LOCATE_FORCE_STEPS"] = _prev_force
    assert history_mutate_prefer_locate(
        [{"role": "user", "content": "【定位改写强制】必须 fs_patch"}]
    )
    # 锚点质量：源码 path:line 优先于 README
    mixed = merge_locate_anchors(
        ["README.md:10", "fawai_avatar/channel/hub.py:1605"],
        ["demo_cancel.py:2"],
    )
    prefer = preferred_locate_anchors(mixed, limit=2)
    assert prefer and "hub.py" in prefer[0], prefer
    assert anchor_quality_score("fawai_avatar/channel/hub.py:1605") > anchor_quality_score(
        "README.md:10"
    )
    wide_res = ExecResult(
        ok=True,
        message="grep 50",
        data={"count": 50, "hits": [{"path": "a.html", "line": i, "text": "x"} for i in range(50)]},
    )
    wide_act = [
        Action(
            kind="fs_grep",
            args={"path": "fawai_avatar/channel/static", "pattern": "."},
            write_resources=[],
        )
    ]
    wide_paths = step_wide_grep_paths(wide_act, [wide_res], hit_threshold=20)
    assert "fawai_avatar/channel/static" in wide_paths
    assert "【宽检索降质】" in format_wide_grep_message(
        paths=wide_paths, hit_threshold=20
    )
    assert step_repeats_banned_wide_grep(
        actions=wide_act, banned_paths=wide_paths
    )
    # 单文件：命中 30（≥目录阈值 20）但 < 单文件默认阈值 → 不降质
    file_res = ExecResult(
        ok=True,
        message="grep 30",
        data={
            "count": 30,
            "hits": [
                {"path": "fawai_avatar/runtime/loop.py", "line": i, "text": "x"}
                for i in range(30)
            ],
        },
    )
    file_act = [
        Action(
            kind="fs_grep",
            args={
                "path": "fawai_avatar/runtime/loop.py",
                "pattern": "wide_grep",
            },
            write_resources=[],
        )
    ]
    # 单文件豁免依赖 FAWAI_WIDE_GREP_FILE_HITS（默认已关=0 时与目录阈值相同）
    import os

    os.environ["FAWAI_WIDE_GREP_FILE_HITS"] = "48"
    try:
        assert step_wide_grep_paths(file_act, [file_res], hit_threshold=20) == []
    finally:
        os.environ.pop("FAWAI_WIDE_GREP_FILE_HITS", None)
    # 降质后：近似宽 pattern 仍禁；收窄 + context 放行
    assert step_repeats_banned_wide_grep(
        actions=wide_act,
        banned_paths=wide_paths,
        banned_patterns={"fawai_avatar/channel/static": "."},
    )
    narrow_act = [
        Action(
            kind="fs_grep",
            args={
                "path": "fawai_avatar/channel/static",
                "pattern": "markdown-render.js script src",
                "context_before": 1,
                "context_after": 1,
            },
            write_resources=[],
        )
    ]
    assert not step_repeats_banned_wide_grep(
        actions=narrow_act,
        banned_paths=wide_paths,
        banned_patterns={"fawai_avatar/channel/static": "."},
    )
    assert "【检索观察压缩】" in format_compressed_hits_message(
        paths=["fawai_avatar/runtime/loop.py"]
    )
    ban_msg = format_compressed_unread_ban_message(
        dump_paths=["data/observe/tool_x_hits.txt"]
    )
    assert "【压缩未读·禁宽搜】" in ban_msg
    assert "禁止" in ban_msg and "fs_grep" in ban_msg
    assert step_has_search_actions(
        [Action(kind="fs_grep", args={"pattern": "x"}, write_resources=[])]
    )
    assert not step_has_search_actions(
        [Action(kind="fs_read", args={"path": "a.py"}, write_resources=[])]
    )
    # 原生 schema 须暴露 fs_grep.pattern，避免空 properties
    grep_tool = next(
        t
        for t in openai_tools_for_coding()
        if t["function"]["name"] == "fs_grep"
    )
    assert "pattern" in grep_tool["function"]["parameters"].get("properties", {})
    # 软【定位预算】不再收紧 mutate 工具面；仅【定位改写强制】才收紧
    assert not history_requests_mutate_prefer(
        [
            {
                "role": "user",
                "content": format_locate_budget_message(
                    streak=3, budget=3, anchors=anchors
                ),
            }
        ]
    )
    assert history_requests_mutate_prefer(
        [
            {
                "role": "user",
                "content": format_locate_mutate_force_message(
                    streak=4, budget=3, anchors=anchors
                ),
            }
        ]
    )
    print("[PASS] explore progress")

    print("== write effect / weak write ==")
    from fawai_avatar.brain.answer_effect import (
        answer_coverage_reject_reason,
        fail_message_for_answer_coverage,
        goal_needs_answer_coverage_gate,
        thought_is_meta_process_narration,
        thought_ok_for_answer_coverage,
        unread_compressed_dumps,
    )
    from fawai_avatar.brain.reflect import (
        SHELL_BYPASS_FS_REPLACEMENT,
        Reflection,
        ground_reflect_constraints,
        is_shell_bypass_fs_constraint,
        mock_reflect,
    )
    from fawai_avatar.brain.write_effect import (
        assess_patch_effect,
        format_visible_effect_prompt,
        goal_needs_visible_effect_gate,
        thought_ok_for_visible_effect,
    )
    from fawai_avatar.skills.review import format_steps_for_review
    from fawai_avatar.skills.store import SkillStore, ensure_builtin_skills

    old_css = ":root {\n  --chat-max: 720px;\n}\n.msg { padding: 8px; }\n"
    new_dead = (
        ":root {\n  --chat-max: 720px;\n  --msg-gap: 12px;\n"
        "  --bubble-radius: 18px;\n}\n.msg { padding: 8px; }\n"
    )
    dead = assess_patch_effect(
        "static/index.html", old_css, new_dead, goal="优化手机会话页面布局"
    )
    assert dead.weak, dead
    new_live = (
        ":root {\n  --chat-max: 720px;\n  --msg-gap: 12px;\n}\n"
        ".msg { padding: 8px; gap: var(--msg-gap); }\n"
    )
    live = assess_patch_effect(
        "static/index.html", old_css, new_live, goal="优化手机会话页面布局"
    )
    assert not live.weak, live
    prop = assess_patch_effect(
        "static/index.html",
        old_css,
        ":root {\n  --chat-max: 720px;\n}\n.msg { padding: 16px; }\n",
        goal="优化布局",
    )
    assert not prop.weak, prop
    assert goal_needs_visible_effect_gate("这个手机会话页面优化一下布局吧")
    assert not thought_ok_for_visible_effect(
        "native tool_calls", goal="优化会话页布局"
    )
    assert thought_ok_for_visible_effect(
        "已收紧顶栏并加大消息气泡间距，窄屏更易读",
        goal="优化会话页布局",
    )
    ve_prompt = format_visible_effect_prompt(
        goal="优化手机步骤列表布局",
        write_set=["static/index.html"],
    )
    assert "可选验收" in ve_prompt
    assert "viewport=mobile" in ve_prompt
    assert "不得因此 failed" in ve_prompt
    import tempfile
    from pathlib import Path

    skills_tmp = Path(tempfile.mkdtemp(prefix="fawai-skills-"))
    installed = ensure_builtin_skills(skills_tmp)
    assert "lesson_write_set_not_acceptance" in installed
    assert "lesson_verify_read_not_endless_explore" in installed
    assert "lesson_answer_must_cover_goal" in installed
    assert "lesson_compressed_dump_must_read" in installed
    assert "lesson_no_shell_bypass_fs" in installed
    stored = SkillStore(skills_tmp).get("lesson_write_set_not_acceptance")
    assert stored is not None
    assert "验收" in str(stored.get("title") or "")
    stored_vr = SkillStore(skills_tmp).get("lesson_verify_read_not_endless_explore")
    assert stored_vr is not None
    assert "无限只读" in str(stored_vr.get("title") or "")
    stored_ans = SkillStore(skills_tmp).get("lesson_answer_must_cover_goal")
    assert stored_ans is not None
    assert "问点" in str(stored_ans.get("title") or "")

    # 运行时默认关闸；单测启发式时临时打开
    os.environ["FAWAI_ANSWER_COVERAGE_GATE"] = "1"
    assert goal_needs_answer_coverage_gate("远端任务进度如何？")
    assert not goal_needs_answer_coverage_gate("优化一下会话页布局")
    hw_goal = "查看4090云服务器的硬件配置，地址账号在我的磁盘中，但我忘记在哪里了"
    assert goal_needs_answer_coverage_gate(hw_goal)
    assert thought_is_meta_process_narration(
        "已获取日志，汇总配置与当前进度并结束。"
    )
    assert thought_is_meta_process_narration(
        "已成功获取4090服务器GPU及训练进程状态，汇总结果并完成任务。"
    )
    assert not thought_ok_for_answer_coverage(
        "已获取日志，汇总配置与当前进度并结束。",
        goal="远端任务进度如何？",
    )
    assert thought_ok_for_answer_coverage(
        "训练仍在跑，约 epoch 12/50，loss 0.32，GPU 占用正常。",
        goal="远端任务进度如何？",
    )
    # 配置盘点：只报 GPU+CPU 过薄；须多维或结构化 + 来源路径
    thin_hw = "已找到4090服务器账号并获取配置：1张RTX 4090(24G显存)，CPU为224核的Xeon 8481C。"
    assert not thought_ok_for_answer_coverage(thin_hw, goal=hw_goal)
    thin_hw_reason = answer_coverage_reject_reason(thin_hw, goal=hw_goal)
    assert thin_hw_reason in {"inventory_thin", "meta_narration"}, thin_hw_reason
    assert "盘点过薄" in fail_message_for_answer_coverage("inventory_thin")
    thin_status = (
        "凭据在`study/配置信息/云服务重要信息.md`。4090显存占24G，`train.py`自11/1运行中。"
    )
    assert answer_coverage_reject_reason(thin_status, goal=hw_goal) == "inventory_thin"
    rich_hw = (
        "账号在 `study/配置信息/云服务重要信息.md`（4090 AutoDL 节）。\n"
        "- GPU：RTX 4090 24GB\n"
        "- CPU：2×Xeon 8481C（224 逻辑核）\n"
        "- 内存：约 754Gi\n"
        "- 磁盘：系统盘约 30G；数据盘 `/root/autodl-tmp` 约 250G\n"
        "- 系统：Ubuntu 22.04，驱动 580 / CUDA 13.0"
    )
    assert thought_ok_for_answer_coverage(rich_hw, goal=hw_goal)
    assert unread_compressed_dumps(
        ["/tmp/observe/tool_x_stdout.txt"], []
    ) == ["/tmp/observe/tool_x_stdout.txt"]
    assert (
        unread_compressed_dumps(
            ["/tmp/observe/tool_x_stdout.txt"],
            ["/tmp/observe/tool_x_stdout.txt"],
        )
        == []
    )
    from fawai_avatar.brain.answer_effect import (
        prune_hits_dumps_after_source_evidence,
        should_suppress_compressed_unread_ban,
    )

    assert not should_suppress_compressed_unread_ban(write_set=[], read_ok_paths=[])
    assert should_suppress_compressed_unread_ban(
        write_set=[], read_ok_paths=["fawai_avatar/skills/review.py"]
    )
    assert should_suppress_compressed_unread_ban(
        write_set=["fawai_avatar/skills/review.py"], read_ok_paths=[]
    )
    dumps = [
        "/tmp/data/observe/tool_20261103T145230Z_aad581c1_hits.txt",
        "/tmp/data/observe/tool_20261103T145230Z_bbbbbbbb_stdout.txt",
    ]
    pruned = prune_hits_dumps_after_source_evidence(
        dumps,
        write_set=[],
        read_ok_paths=["fawai_avatar/channel/hub.py"],
    )
    assert pruned == [dumps[1]], pruned
    assert (
        prune_hits_dumps_after_source_evidence(dumps, write_set=[], read_ok_paths=[])
        == dumps
    )
    assert is_shell_bypass_fs_constraint(
        "禁止使用 fs_read 读取绝对路径；改用 shell 工具执行 cat 命令直接读取目标文件内容"
    )
    assert not is_shell_bypass_fs_constraint("改用 explore_search 定位后 fs_patch")
    esc = mock_reflect(
        "查配置",
        [Action(kind="fs_read", args={"path": "/outside/a.md"})],
        [ExecResult(ok=False, message="path escapes work root: /outside/a.md")],
        fingerprint="deadbeefdeadbeef",
    )
    grounded = ground_reflect_constraints(
        esc,
        actions=[Action(kind="fs_read", args={"path": "/outside/a.md"})],
        results=[ExecResult(ok=False, message="path escapes work root: /outside/a.md")],
    )
    assert any("禁止用 shell cat" in c for c in grounded.constraints)
    assert SHELL_BYPASS_FS_REPLACEMENT in grounded.constraints
    bypass_reflected = ground_reflect_constraints(
        Reflection(
            cause="越权",
            decision="alternate",
            constraints=[
                "禁止使用 fs_read 读取绝对路径",
                "改用 shell 工具执行 cat 命令直接读取目标文件内容",
            ],
            thought="改 shell",
            fingerprint="x",
        ),
        results=[ExecResult(ok=False, message="path escapes work root: /x")],
    )
    assert not any(is_shell_bypass_fs_constraint(c) for c in bypass_reflected.constraints)
    assert any("禁止用 shell cat" in c for c in bypass_reflected.constraints)
    from fawai_avatar.brain.reflect import (
        NOT_FOUND_FS_REPLACEMENT,
        is_ban_absolute_fs_read_constraint,
        is_force_glob_only_constraint,
    )

    assert is_ban_absolute_fs_read_constraint(
        "禁止再次直接 fs_read /Users/shunyaoyin/... 绝对路径"
    )
    assert is_force_glob_only_constraint(
        "必须使用 fs_glob 搜索 **/*云服务*.md 或 **/*配置*.md 以定位文件真实路径"
    )
    missing_grounded = ground_reflect_constraints(
        Reflection(
            cause="目标文件绝对路径在沙箱中不存在，疑似路径越界被屏蔽",
            decision="alternate",
            constraints=[
                "禁止再次直接 fs_read /Users/shunyaoyin/... 绝对路径",
                "必须使用 fs_glob 搜索 **/*云服务*.md",
                "禁止复用失败动作指纹 abc",
            ],
            thought="改用 glob",
            fingerprint="b73b",
        ),
        results=[
            ExecResult(
                ok=False,
                message=(
                    "not found (file missing): /Users/demo/study/x.md；≠ 沙箱越界/禁读"
                ),
            )
        ],
    )
    assert missing_grounded.raw.get("ban_absolute_fs_read_stripped")
    assert missing_grounded.raw.get("force_glob_only_stripped")
    assert missing_grounded.raw.get("not_found_grounded")
    assert NOT_FOUND_FS_REPLACEMENT in missing_grounded.constraints
    assert not any(is_ban_absolute_fs_read_constraint(c) for c in missing_grounded.constraints)
    assert not any(is_force_glob_only_constraint(c) for c in missing_grounded.constraints)
    assert "非沙箱越界" in (missing_grounded.cause or "")
    from fawai_avatar.brain.reflect import (
        abort_should_deliver_commands,
        format_abort_delivery,
    )
    from fawai_avatar.control.shell_guard import shell_local_fs_bypass_error

    blocked = shell_local_fs_bypass_error(
        'cp "/Users/demo/study/配置信息/x.md" ./x.md && cat ./x.md',
        work_root=".",
        read_roots=["/"],
    )
    assert blocked and "fs_read" in blocked, blocked
    assert (
        shell_local_fs_bypass_error(
            "ssh -p 22 user@host uptime",
            work_root=".",
            read_roots=["/"],
        )
        is None
    )
    abort_ref = Reflection(
        cause="无 SSH 凭据",
        decision="abort",
        constraints=["读取部署脚本，输出完整本地执行命令供用户在宿主机终端运行"],
        thought="止损并交命令",
        fingerprint="ab",
    )
    assert abort_should_deliver_commands(abort_ref)
    delivered = format_abort_delivery(abort_ref)
    assert "建议下一步" in delivered and "输出完整本地" in delivered

    from fawai_avatar.brain.method_recovery import (
        CONSTRAINT_SSH_AUTH,
        enrich_reflection_method_switch,
        format_remote_away_veto,
        looks_like_remote_away_text,
        looks_like_remote_ops_goal,
        remote_idle_gate_threshold,
        strip_disabled_remote_gate_history,
        update_remote_idle_streak,
    )
    from fawai_avatar.brain.reflect import mock_reflect

    assert looks_like_remote_ops_goal("将法外分身升级到5080测试服务器")
    assert looks_like_remote_away_text("你想办法读取一下，我没在电脑边")
    assert "【远程操作约束】" in format_remote_away_veto(answer="没在电脑边")
    ssh_fail = mock_reflect(
        "升级到5080",
        [Action(kind="shell", args={"cmd": "ssh -o BatchMode=yes -p 22 u@h true"})],
        [
            ExecResult(
                ok=False,
                message="u@h: Permission denied (publickey,password).",
            )
        ],
        fingerprint="sshdeadbeef01",
    )
    assert ssh_fail.decision == "alternate", ssh_fail
    assert any("BatchMode" in c or "shovel" in c or "ssh-run" in c for c in ssh_fail.constraints)
    enriched = enrich_reflection_method_switch(
        Reflection(
            cause="缺私钥",
            decision="abort",
            constraints=[],
            thought="止损",
            fingerprint="x",
        ),
        actions=[Action(kind="shell", args={"cmd": "ssh -p 22 u@h true"})],
        results=[
            ExecResult(ok=False, message="Permission denied (publickey,password).")
        ],
        goal="部署到服务器",
    )
    assert enriched.decision == "alternate"
    assert CONSTRAINT_SSH_AUTH in enriched.constraints
    saved_remote_gate = os.environ.pop("FAWAI_REMOTE_IDLE_GATE", None)
    try:
        assert remote_idle_gate_threshold(goal="升级到5080") == 0
        os.environ["FAWAI_REMOTE_IDLE_GATE"] = "6"
        assert remote_idle_gate_threshold(goal="升级到5080") == 6
        os.environ.pop("FAWAI_REMOTE_IDLE_GATE", None)
        kept = strip_disabled_remote_gate_history(
            [
                {"role": "user", "content": "【远端进度闸门】\n禁止继续只读"},
                {"role": "user", "content": "执行结果 ok"},
            ],
            goal="升级到5080",
        )
        assert len(kept) == 1 and "执行结果" in kept[0]["content"]
    finally:
        if saved_remote_gate is None:
            os.environ.pop("FAWAI_REMOTE_IDLE_GATE", None)
        else:
            os.environ["FAWAI_REMOTE_IDLE_GATE"] = saved_remote_gate
    streak = update_remote_idle_streak(
        0,
        goal="升级到5080",
        actions=[Action(kind="fs_read", args={"path": "README.md"})],
        results=[ExecResult(ok=True, message="read ok")],
    )
    assert streak == 1
    streak = update_remote_idle_streak(
        streak,
        goal="升级到5080",
        actions=[
            Action(
                kind="shell",
                args={"cmd": "bash fawai_shovels/shell/rsync-ssh-deploy.sh"},
            )
        ],
        results=[ExecResult(ok=True, message="deployed")],
    )
    assert streak == 0

    from fawai_avatar.channel.redact import (
        message_dict_for_client,
        redact_for_client,
        redact_secrets,
        _redact_secrets_impl,
    )
    from fawai_avatar.channel.message import Message

    # 规划侧透传；Web 出站强制脱敏
    leaked = 'expect -c \'send "QSfDa/ENNvvQ\\r"\''
    assert redact_secrets(leaked) == leaked
    assert "***" in redact_for_client(leaked)
    assert "QSfDa" not in redact_for_client(leaked)
    assert "***" in _redact_secrets_impl(leaked)
    ssh_block = "ssh -p 31057 root@example.com\nQSfDa/ENNvvQ\n"
    assert redact_secrets(ssh_block) == ssh_block
    redacted_ssh = redact_for_client(ssh_block)
    assert "***" in redacted_ssh
    assert "QSfDa" not in redacted_ssh
    progress = Message.progress(
        'shell: sshpass -p \'QSfDa/ENNvvQ\' ssh -p 22 root@h',
        meta={"cmd": "sshpass -p 'QSfDa/ENNvvQ' ssh root@h", "phase": "act"},
    )
    client_payload = message_dict_for_client(progress)
    assert "QSfDa" not in str(client_payload.get("text") or "")
    assert "QSfDa" not in str((client_payload.get("meta") or {}).get("cmd") or "")
    assert progress.text.startswith("shell: sshpass")  # 原信封未改
    long_steps = [
        {
            "plan": {
                "thought": f"step-{i}",
                "status": "continue" if i < 19 else "success",
                "actions": [{"kind": "shell", "args": {"cmd": f"echo {i}"}}],
            },
            "results": [{"ok": True, "message": ("x" * 200) + str(i)}],
        }
        for i in range(20)
    ]
    sampled = format_steps_for_review(long_steps, max_chars=4000, head_n=2, tail_n=3)
    assert "steps_index:" in sampled
    assert "头尾采样" in sampled or "steps_detail" in sampled
    assert "step-19" in sampled or '"status": "success"' in sampled
    print("[PASS] write effect / answer coverage")

    print("== history window ==")
    from fawai_avatar.brain.history_window import compress_history_for_plan

    fat = []
    for i in range(12):
        fat.append({"role": "assistant", "content": f"thought-{i}"})
        fat.append(
            {
                "role": "user",
                "content": (
                    "执行结果（工具结果观察）：\n"
                    f"- fs_read path='fawai_avatar/channel/static/index.html' → ok: read\n"
                    "  content（全文）:\n" + ("LINE\n" * 800)
                ),
            }
        )
    before = sum(len(m["content"]) for m in fat)
    slim = compress_history_for_plan(fat, keep_recent=4, max_chars=6000)
    after = sum(len(str(m.get("content") or "")) for m in slim)
    assert after < before
    assert after <= 6000
    assert len(slim) < len(fat)
    print(f"[PASS] history window {before} → {after} chars, msgs {len(fat)} → {len(slim)}")

    from fawai_avatar.brain.durable_memory import durable_memory_text
    from fawai_avatar.brain.history_window import plan_history
    from fawai_avatar.brain.step_todos import normalize_todos, task_keeps_step_todos

    calls = {"n": 0}

    def _sum(excerpt: str) -> str:
        calls["n"] += 1
        assert "LINE" in excerpt
        return "读过配置，host=10.0.0.1。禁止再读该文件。"

    compact_in = []
    for i in range(6):
        compact_in.append({"role": "assistant", "content": f"thought-{i} " + ("x" * 400)})
        compact_in.append(
            {
                "role": "user",
                "content": "执行结果（工具结果观察）：\n" + ("LINE\n" * 200) + f"\nitem-{i}",
            }
        )
    once, cache = plan_history(
        compact_in, cache=None, summarize=_sum, keep_recent=2, max_chars=4000
    )
    twice, cache2 = plan_history(
        compact_in, cache=cache, summarize=_sum, keep_recent=2, max_chars=4000
    )
    assert calls["n"] == 1
    assert cache2.get("prefix_hash") == cache.get("prefix_hash")
    head = str(once[0].get("content") or "")
    assert "host=10.0.0.1" in head
    assert "禁止再读" not in head
    assert "定向再读" in head
    assert "host=10.0.0.1" in str(twice[0].get("content") or "")
    remembered = durable_memory_text("host=10.0.0.1\npassword: hunter2\n禁止再读")
    assert "hunter2" not in remembered
    assert "10.0.0.1" in remembered
    assert "禁止再读" not in remembered
    todos = normalize_todos(
        [
            {"text": "读配置 password: hunter2", "status": "in_progress"},
            {"text": "查训练", "status": "待办"},
        ]
    )
    assert todos[0]["status"] == "进行中"
    assert "hunter2" not in todos[0]["text"]
    assert todos[1]["status"] == "待办"
    assert task_keeps_step_todos({}) is True
    assert task_keeps_step_todos({"plan_role": "supervisor"}) is False
    print("[PASS] compact + step todos")

    print("== json write auto-bind like native ==")
    json_write = ensure_architecture_decls(_write_control_plan(), auto_default=True)
    assert validate_architecture(goal, json_write) is None
    assert (json_write.raw or {}).get("architecture_auto_bound") is True
    print("[PASS] json auto-bind")

    print("== architecture abs path + soft fallback ==")
    from fawai_avatar.brain.compliance import (
        apply_architecture_skip_fallback,
        layer_prefix_for,
    )

    abs_path = (
        "/Users/demo/ai-company/fawai-avatar/fawai_avatar/channel/static/index.html"
    )
    assert layer_prefix_for(abs_path) == "fawai_avatar/channel/"
    bare_chan = Plan(
        thought="write",
        actions=[
            Action(
                kind="fs_patch",
                args={"path": abs_path, "old": "a", "new": "b"},
                write_resources=[abs_path],
            )
        ],
        done=False,
        status="continue",
        raw={},
    )
    bound = ensure_architecture_decls(bare_chan, auto_default=True)
    assert validate_architecture(goal, bound) is None
    # soft fallback marks skip when somehow still unsatisfied
    soft = apply_architecture_skip_fallback(
        Plan(
            thought="write",
            actions=list(bare_chan.actions),
            done=False,
            status="continue",
            raw={},
        ),
        reason="测试兜底 skip",
    )
    assert validate_architecture(goal, soft) is None
    print("[PASS] abs path + soft fallback")

    print("== necessary path ledger ==")
    from fawai_avatar.brain.necessary_paths import (
        CONSTRAINT_PATH_LEDGER,
        enrich_reflection_path_ledger,
        failed_needs_path_ledger_retry,
        format_path_ledger_fail_prompt,
        goal_needs_path_ledger_gate,
        path_ledger_prompt_lines,
        thought_claims_command_delivery,
        thought_has_copyable_command,
        thought_has_path_breakdown,
    )
    from fawai_avatar.brain.reflect import Reflection

    assert goal_needs_path_ledger_gate(
        "根据磁盘上的配置，把最新版本升级到测试服务器"
    )
    assert not goal_needs_path_ledger_gate("现在几点了")
    assert "必要路径" in path_ledger_prompt_lines(short=False)
    assert "证据保真" in path_ledger_prompt_lines(short=False)
    assert "证据保真" in path_ledger_prompt_lines(short=True)
    assert "证据" in CONSTRAINT_PATH_LEDGER
    empty_cmd = "沙箱禁读致凭据缺失，闸门已触。给出本机可复制部署命令，待用户补密码。"
    assert thought_claims_command_delivery(empty_cmd)
    assert not thought_has_copyable_command(empty_cmd)
    assert failed_needs_path_ledger_retry(
        goal="根据配置升级到测试服务器", thought=empty_cmd
    )
    good = (
        "必要路径：鉴权材料阻塞（口令未读到）；作用对象/脚本已齐（本地仓+部署脚本）。"
        "二是阻塞于口令。可复制命令：\n"
        "```\nbash fawai_shovels/shell/rsync-ssh-deploy.sh\n```"
    )
    assert thought_has_path_breakdown(good)
    assert thought_has_copyable_command(good)
    assert not failed_needs_path_ledger_retry(
        goal="根据配置升级到测试服务器", thought=good
    )
    enriched = enrich_reflection_path_ledger(
        Reflection(
            cause="缺密",
            decision="alternate",
            constraints=["换工具"],
            thought="再找",
            fingerprint="p",
        )
    )
    assert CONSTRAINT_PATH_LEDGER in enriched.constraints
    scrubbed = enrich_reflection_path_ledger(
        Reflection(
            cause="无法截屏",
            decision="alternate",
            constraints=[
                "须验证移动端布局；若无法取得页面须 status=failed",
                "换工具",
            ],
            thought="再验",
            fingerprint="v",
        )
    )
    assert all("须验证移动端" not in c for c in scrubbed.constraints)
    assert any("可选验收" in c for c in scrubbed.constraints)
    prompt = format_path_ledger_fail_prompt(
        goal="根据配置升级到测试服务器", thought=empty_cmd
    )
    assert "【必要路径收尾】" in prompt and "可复制命令" in prompt
    print("[PASS] necessary path ledger")

    print("== step repeat allow_repeat skip ==")
    from fawai_avatar.brain.reflect import (
        action_skips_step_repeat,
        plan_skips_step_repeat,
        step_fingerprint,
    )

    same_read = Action(
        kind="fs_read",
        args={"path": "hub.py", "offset": 1470, "limit": 60},
        write_resources=[],
    )
    verify_read = Action(
        kind="fs_read",
        args={
            "path": "hub.py",
            "offset": 1470,
            "limit": 60,
            "allow_repeat": True,
        },
        write_resources=[],
    )
    assert not action_skips_step_repeat(same_read)
    assert action_skips_step_repeat(verify_read)
    assert plan_skips_step_repeat([verify_read])
    assert plan_skips_step_repeat([], plan_raw={"allow_repeat": True})
    assert not plan_skips_step_repeat([same_read], plan_raw={})
    # 标识不进指纹：有/无 allow_repeat 的同读指纹一致
    assert step_fingerprint([same_read]) == step_fingerprint(
        [
            Action(
                kind="fs_read",
                args={
                    "path": "hub.py",
                    "offset": 1470,
                    "limit": 60,
                    "allow_repeat": True,
                },
                write_resources=[],
            )
        ]
    )
    print("[PASS] step repeat allow_repeat skip")

    print("== stagnation grounded sanitize ==")
    from fawai_avatar.brain.reflect import _ground_stagnation_constraints

    bad = [
        "禁止再次读取 index.html",
        "去 src/components 找 Vue 组件",
        "换个观察方式",
        "必须先使用 fs_read 读取 index.html 的完整内容",
        "继续扩大 offset 再读一遍",
    ]
    acts = [
        Action(
            kind="fs_read",
            args={"path": "fawai_avatar/channel/static/index.html"},
            write_resources=[],
        )
    ]
    cleaned = _ground_stagnation_constraints(
        bad, actions=acts, decision="alternate"
    )
    assert not any("禁止再次读取" in c for c in cleaned)
    assert not any("Vue" in c for c in cleaned)
    assert not any("必须先使用 fs_read" in c for c in cleaned)
    assert not any("继续扩大 offset" in c for c in cleaned)
    assert any("换指纹" in c and "fs_patch" in c for c in cleaned)
    assert not any("改写相位" in c and "必须进入" in c for c in cleaned)
    rewritten = _ground_stagnation_constraints(
        [
            "必须先使用 fs_read 读取 index.html，再调用 fs_patch 替换",
        ],
        actions=acts,
        decision="alternate",
    )
    assert any("exact old" in c and "fs_patch" in c for c in rewritten)
    assert any("alternate ≠ 强制写状态" in c for c in rewritten)
    # 强制写相位文案须被改写成换指纹
    forced_phase = _ground_stagnation_constraints(
        ["已定位到目标正则，下一步必须进入改写相位，直接修改代码"],
        actions=acts,
        decision="alternate",
    )
    assert any("换指纹" in c for c in forced_phase)
    assert not any("必须进入改写相位" in c for c in forced_phase)
    from fawai_avatar.brain.reflect import _scrub_force_mutate_phase_text

    scrubbed = _scrub_force_mutate_phase_text(
        "已定位到目标正则，下一步必须进入改写相位，直接修改代码以解除或调整取消限制。"
    )
    assert "必须进入改写相位" not in scrubbed
    assert "换指纹" in scrubbed
    # 仅有 locate_anchors + 薄 grep：先拿正文，不逼写相位
    wide_only = _ground_stagnation_constraints(
        ["必须使用 fs_grep 在全仓再搜一遍 cancel", "换个观察方式"],
        actions=[
            Action(
                kind="fs_grep",
                args={"path": ".", "pattern": "cancel"},
                write_resources=[],
            )
        ],
        decision="alternate",
        locate_anchors=["fawai_avatar/channel/hub.py:1605"],
        results=[ExecResult(ok=True, message="grep 12")],
    )
    assert not any("必须使用 fs_grep" in c for c in wide_only)
    assert any("hub.py" in c and "可复制正文" in c for c in wide_only)
    assert any("alternate ≠ 强制写状态" in c for c in wide_only)
    print("[PASS] stagnation grounded")

    print("== B: architecture hard-fail rescued before capability failed ==")
    from fawai_avatar.brain.compliance import apply_architecture_skip_fallback

    # 裸写计划（无 docs）经 soft fallback 后不得再以架构文案堵死
    bare = _write_control_plan(with_docs=False)
    assert validate_architecture(goal, bare)
    rescued_b = apply_architecture_skip_fallback(
        bare, reason="测试：漏声明仍允许写入"
    )
    assert validate_plan(goal, rescued_b) is None
    assert (rescued_b.raw or {}).get("architecture_soft_fallback") or (
        rescued_b.raw or {}
    ).get("architecture_auto_bound")
    print("[PASS] architecture soft rescue")

    print("== D: coerce abort → alternate（反省不再 abort）==")
    from fawai_avatar.brain.reflect import (
        Reflection,
        apply_fingerprint_guard,
        coerce_reflection_no_abort,
        soften_abort_when_mutated_format_error,
    )

    abort_ref = Reflection(
        cause="fs_patch requires path",
        decision="abort",
        constraints=["禁止复用失败动作指纹 abc"],
        thought="止损",
        fingerprint="68145e8864fce992",
        forced=True,
    )
    coerced = coerce_reflection_no_abort(abort_ref)
    assert coerced.decision == "alternate"
    assert (coerced.raw or {}).get("coerced_from_abort") is True
    assert any("status=failed" in c for c in coerced.constraints)
    # 指纹守卫：alternate 后仍同指纹 → 升约束，仍不 abort
    guarded = apply_fingerprint_guard(
        Reflection(
            cause="again",
            decision="retry",
            constraints=[],
            thought="x",
            fingerprint="fp1",
        ),
        fingerprint="fp1",
        fail_counts={"fp1": 3},
        prior_alternate_fps={"fp1"},
    )
    assert guarded.decision == "alternate"
    assert guarded.forced is True
    assert any("status=failed" in c for c in guarded.constraints)
    # 兼容旧 soften 辅助仍可用（若上游仍塞 abort）
    soft = soften_abort_when_mutated_format_error(
        abort_ref,
        write_set=["fawai_avatar/channel/static/markdown-render.js"],
        results=[ExecResult(ok=False, message="fs_patch requires path")],
    )
    assert soft.decision == "alternate"
    print("[PASS] coerce abort / fingerprint no-abort")

    print("== patch args coerce + verify_read reflect ==")
    from fawai_avatar.brain.capabilities import normalize_tool_args
    from fawai_avatar.brain.reflect import (
        failed_write_paths,
        reflection_needs_verify_read,
    )
    from fawai_avatar.workspace.fs import (
        coerce_patch_hunks,
        patch_anchor_quality_error,
        resolve_patch_ops,
    )

    coerced = coerce_patch_hunks('[{"old":"a","new":"b"}]')
    assert coerced == [{"old": "a", "new": "b"}]
    flat = normalize_tool_args(
        {"path": "x.py", "hunks": '[{"old":"return 1","new":"return 2"}]'}
    )
    assert isinstance(flat.get("hunks"), list)
    assert flat["hunks"][0]["old"] == "return 1"
    try:
        coerce_patch_hunks({"old": "a"})
        raise AssertionError("expected type error")
    except ValueError as exc:
        assert "dict" in str(exc)
    assert patch_anchor_quality_error("foo…bar", "x") is not None
    assert patch_anchor_quality_error("return 1", "return 2") is None
    assert patch_anchor_quality_error("return 1...", "return 2") is not None
    ops = resolve_patch_ops(hunks='[{"old":"a","new":"b"}]')
    assert ops["ops"][0]["old"] == "a"
    need = Reflection(
        cause="old not found",
        decision="alternate",
        constraints=["必须先使用 fs_read 读取 index.html"],
        thought="先读再改",
        fingerprint="fp",
    )
    # 硬闸退役：即使 cause 含 fs_read 也不再注入反省开读
    assert not reflection_needs_verify_read(need)
    assert not reflection_needs_verify_read(
        Reflection(cause="lease busy", decision="alternate", fingerprint="y")
    )
    paths = failed_write_paths(
        [Action(kind="fs_patch", args={"path": "a.py"}, write_resources=["a.py"])],
        [ExecResult(ok=False, message="old not found")],
    )
    assert paths == ["a.py"]
    print("[PASS] patch coerce + verify_read")

    print("== batch continue on read-only fail ==")
    import tempfile
    from pathlib import Path

    from fawai_avatar.runtime.loop import WorkerLoop, should_break_action_batch
    from fawai_avatar.task.supervisor import Supervisor

    # 单元：只读失败不 break；写/shell 失败 break
    short = Action(kind="fs_grep", args={"pattern": "复盘"})
    long = Action(kind="fs_grep", args={"pattern": "cancel.*review"})
    assert not should_break_action_batch(
        short, ExecResult(ok=False, message="pattern too short (2 chars)")
    )
    assert not should_break_action_batch(long, ExecResult(ok=True, message="grep 1"))
    assert should_break_action_batch(
        Action(kind="fs_patch", args={"path": "a.py"}, write_resources=["a.py"]),
        ExecResult(ok=False, message="old not found"),
    )
    assert should_break_action_batch(
        Action(kind="shell", args={"cmd": "false"}),
        ExecResult(ok=False, message="exit 1"),
    )

    class _SeqController:
        """首条短词失败、次条成功；写失败则返回失败。"""

        def __init__(self) -> None:
            self.calls: list[Action] = []
            self._background = None

        def execute(self, action: Action, *, task_id: str | None = None) -> ExecResult:
            self.calls.append(action)
            kind = (action.kind or "").strip().lower()
            pat = str((action.args or {}).get("pattern") or "")
            if kind == "fs_write":
                return ExecResult(ok=False, message="write blocked")
            if pat == "复盘":
                return ExecResult(
                    ok=False, message="pattern too short (2 chars), use more specific pattern"
                )
            return ExecResult(
                ok=True,
                message="grep 1",
                data={"hits": [{"path": "notes/a.txt", "line": 1, "text": "cancel review"}]},
            )

    data = Path(tempfile.mkdtemp(prefix="fawai_batch_data_"))
    sup = Supervisor(str(data / "sup"))
    tid = sup.create_task("batch continue")
    assert sup.set_running(tid)
    ctrl = _SeqController()
    loop = WorkerLoop(sup, controller=ctrl, max_steps=2, capture=False)  # type: ignore[arg-type]
    results = loop._execute_actions(
        tid,
        [
            Action(kind="fs_grep", args={"pattern": "复盘", "path": "notes"}),
            Action(
                kind="fs_grep",
                args={"pattern": "cancel review", "path": "notes"},
            ),
        ],
    )
    assert len(results) == 2, results
    assert not results[0].ok and results[1].ok, results
    assert len(ctrl.calls) == 2
    assert (results[1].data or {}).get("hits"), results[1]
    # 写失败仍掐后续只读
    ctrl.calls.clear()
    results_w = loop._execute_actions(
        tid,
        [
            Action(
                kind="fs_write",
                args={"path": "a.txt", "content": "x"},
                write_resources=["a.txt"],
            ),
            Action(kind="fs_grep", args={"pattern": "cancel review", "path": "notes"}),
        ],
    )
    assert len(results_w) == 1 and not results_w[0].ok, results_w
    assert len(ctrl.calls) == 1
    print("[PASS] batch continue on read-only fail")

    print("demo_compliance: all passed")


if __name__ == "__main__":
    main()
