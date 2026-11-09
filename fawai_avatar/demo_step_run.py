# term:步级执行档  doc:doc/功能/步级执行档/说明.md
"""步级执行档：step_run 解析 + act 规则 + brief 短观察反省。"""
from __future__ import annotations

import os

from fawai_avatar.brain.obs_reflect import (
    mock_obs_reflect,
    obs_reflect_system_prompt,
    run_obs_reflect,
)
from fawai_avatar.brain.planner import _plan_from_payload
from fawai_avatar.brain.step_run import (
    act_rule_ok,
    coerce_step_tier,
    format_act_obs_summary,
    normalize_step_run,
    planner_step_tier_rule,
    step_run_label,
    step_tier_from_inbound_meta,
    step_tier_from_meta,
)
from fawai_avatar.types import Action, ExecResult


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name} failed" + (f": {detail}" if detail else ""))
    print(f"ok: {name}")


def main() -> None:
    _ok("tier_default_off", step_tier_from_meta({}) is False)
    _ok("tier_on", step_tier_from_meta({"step_tier": True}) is True)
    _ok("tier_coerce", coerce_step_tier("1") is True)
    _ok("tier_inbound_none", step_tier_from_inbound_meta({}) is None)
    _ok("tier_inbound_on", step_tier_from_inbound_meta({"step_tier": "1"}) is True)
    _ok("tier_rule_off", planner_step_tier_rule(step_tier=False) == "")
    _ok("tier_rule_on", "分档开" in planner_step_tier_rule(step_tier=True))

    _ok("norm_act", normalize_step_run("act") == "act")
    _ok("norm_brief", normalize_step_run("brief") == "brief")
    _ok("norm_reflect", normalize_step_run("reflect") == "reflect")
    _ok("alias_execute", normalize_step_run("execute") == "act")
    _ok("alias_simple", normalize_step_run("simple") == "brief")
    _ok("empty", normalize_step_run("") == "")
    _ok("bad", normalize_step_run("nope") == "")
    _ok("label_act", "仅执行" in step_run_label("act"))

    plan = _plan_from_payload(
        {
            "thought": "读 a",
            "status": "continue",
            "done": False,
            "step_kind": "act_serial",
            "step_run": "act",
            "observe_kinds": ["none"],
            "actions": [
                {
                    "kind": "fs_read",
                    "args": {"path": "a.md"},
                    "expect": {"contains": "hello"},
                }
            ],
        }
    )
    _ok("parse_step_run", plan.step_run == "act")
    _ok("to_dict_step_run", plan.to_dict().get("step_run") == "act")

    actions = [
        Action(kind="fs_read", args={"path": "a.md"}, expect={"contains": "hello"})
    ]
    ok_res = [ExecResult(ok=True, message="hello world")]
    bad_res = [ExecResult(ok=True, message="nope")]
    _ok("act_rule_ok", act_rule_ok(actions, ok_res)[0] is True)
    _ok("act_rule_miss", act_rule_ok(actions, bad_res)[0] is False)
    summary = format_act_obs_summary(actions, ok_res)
    _ok("act_summary", "仅执行" in summary or "step_run=act" in summary)

    # 回归：若再推进度，step_run 只能靠 **label_extra 注入，
    # 不可再写 step_run= 显式关键字（与 label_extra 撞 TypeError）。
    # 现网 act/fast/机械跳过已不推 obs_reflect 进度卡。
    def _progress_sink(text: str, *, phase: str, **extra: object) -> None:
        _ = (text, phase, extra)

    label_extra = {
        "step_run": "act",
        "step_run_label": step_run_label("act"),
    }
    _progress_sink(
        "观察反省中…",
        phase="obs_reflect",
        progress="advanced",
        **label_extra,
    )
    _ok("act_progress_kwargs_ok", True)
    raised = False
    try:
        _progress_sink(
            "x",
            phase="obs_reflect",
            step_run="act",
            **label_extra,
        )
    except TypeError as exc:
        raised = "step_run" in str(exc)
    _ok("act_progress_dup_kw_raises", raised)

    brief_sys = obs_reflect_system_prompt(brief=True)
    full_sys = obs_reflect_system_prompt(brief=False)
    _ok("brief_prompt", "短观察反省" in brief_sys and "≤40字" in brief_sys)
    _ok("full_prompt", "短观察反省" not in full_sys[:40] and "≤80字" in full_sys)

    prev = os.environ.get("FAWAI_BRAIN_MOCK")
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    try:
        ref = run_obs_reflect(
            "goal",
            actions,
            ok_res,
            tool_feedback="hello world",
            llm=None,
            brief=True,
        )
        _ok("brief_mock_raw", (ref.raw or {}).get("step_run") == "brief")
        _ok("brief_mock_short", len(ref.thought or "") <= 40)
    finally:
        if prev is None:
            os.environ.pop("FAWAI_BRAIN_MOCK", None)
        else:
            os.environ["FAWAI_BRAIN_MOCK"] = prev

    # 缺省仍可 mock
    _ok(
        "mock_default",
        bool(
            mock_obs_reflect("g", actions, ok_res, tool_feedback="x").thought
        ),
    )
    print("demo_step_run: all ok")


if __name__ == "__main__":
    main()
