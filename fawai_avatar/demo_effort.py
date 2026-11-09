# term:Effort  doc:doc/功能/Effort/说明.md
"""Effort 开/关：解析、提示词门槛、约束清洗自检。"""

from __future__ import annotations

from fawai_avatar.brain.effort import (
    coerce_effort,
    effort_from_inbound_meta,
    effort_from_meta,
    ground_effort_constraints,
    obs_reflect_effort_rule,
    planner_effort_rule,
    reflect_effort_rule,
)
from fawai_avatar.brain.obs_reflect import obs_reflect_system_prompt
from fawai_avatar.brain.reflect import reflect_system_prompt


def _ok(name: str, cond: bool) -> None:
    print(("OK  " if cond else "FAIL") + f" {name}")
    if not cond:
        raise SystemExit(1)


def main() -> None:
    _ok("coerce_false_default", coerce_effort(None) is False)
    _ok("coerce_true", coerce_effort("1") is True)
    _ok("meta_off", effort_from_meta({}) is False)
    _ok("meta_on", effort_from_meta({"effort": True}) is True)
    _ok("inbound_absent", effort_from_inbound_meta({}) is None)
    _ok("inbound_on", effort_from_inbound_meta({"effort": "yes"}) is True)

    off_plan = planner_effort_rule(effort=False)
    on_plan = planner_effort_rule(effort=True)
    _ok("plan_on_no_cap_ceiling", "不限制模型生成正文" in on_plan or "创意 skill" in on_plan)
    _ok("plan_off_mentions_关", "Effort 关" in off_plan)
    _ok("plan_on_mentions_开", "Effort 开" in on_plan)
    _ok("plan_on_fresh", "不得当完成" in on_plan or "不得够用" in on_plan)
    _ok("plan_on_pptx", ".pptx" in on_plan or "大纲" in on_plan)

    on_obs = obs_reflect_effort_rule(effort=True)
    _ok("obs_on_cautious", "强制谨慎" in on_obs or "默认倾向 advanced" in on_obs)
    sys_obs = obs_reflect_system_prompt(effort=True)
    _ok("obs_prompt_inject", "Effort 开" in sys_obs)

    _ok("reflect_off_empty", reflect_effort_rule(effort=False) == "")
    sys_ref = reflect_system_prompt(effort=True)
    _ok("reflect_prompt_inject", "Effort 开" in sys_ref)

    cleaned = ground_effort_constraints(
        [
            "停止尝试自动化 SSH 连接，直接基于基线回答",
            "禁止复用失败动作指纹 abc",
        ],
        effort=True,
        err_blob="couldn't read file \"probe.exp\": no such file or directory",
    )
    blob = " ".join(cleaned)
    _ok("strip_stop_ssh", "停止尝试自动化" not in blob)
    _ok("inject_path_retry", "绝对路径" in blob or "cwd" in blob.lower())
    _ok(
        "pass_through_when_off",
        ground_effort_constraints(
            ["停止尝试自动化 SSH"],
            effort=False,
            err_blob="no such file",
        )
        == ["停止尝试自动化 SSH"],
    )
    print("demo_effort: all ok")


if __name__ == "__main__":
    main()
