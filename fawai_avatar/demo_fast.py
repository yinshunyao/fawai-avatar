# term:Fast  doc:doc/功能/Fast/说明.md
"""Fast 极速：解析、思考覆盖、规划短规则自检。"""

from __future__ import annotations

from fawai_avatar.brain.fast import (
    coerce_fast,
    fast_from_inbound_meta,
    fast_from_meta,
    planner_fast_rule,
)
from fawai_avatar.llm.thinking import resolve_thinking


def _ok(name: str, cond: bool) -> None:
    print(("OK  " if cond else "FAIL") + f" {name}")
    if not cond:
        raise SystemExit(1)


def main() -> None:
    _ok("coerce_false_default", coerce_fast(None) is False)
    _ok("coerce_true", coerce_fast("1") is True)
    _ok("coerce_fast_token", coerce_fast("fast") is True)
    _ok("meta_off", fast_from_meta({}) is False)
    _ok("meta_on", fast_from_meta({"fast": True}) is True)
    _ok("inbound_absent", fast_from_inbound_meta({}) is None)
    _ok("inbound_on", fast_from_inbound_meta({"fast": "yes"}) is True)

    _ok("rule_off_empty", planner_fast_rule(fast=False) == "")
    on_rule = planner_fast_rule(fast=True)
    _ok("rule_on_mentions", "Fast 开" in on_rule or "极速" in on_rule)
    _ok("rule_on_hard_gates", "硬闸" in on_rule)

    # 显式 enable_thinking=False 须盖过 main 角色默认与档案开思考
    resolved = resolve_thinking(
        thinking_role="main",
        enable_thinking=False,
        profile_enable=True,
        profile_budget=8000,
    )
    _ok("thinking_forced_off", resolved.get("enable_thinking") is False)
    _ok("budget_cleared_when_off", resolved.get("thinking_budget") is None)

    # 无显式覆盖时 main 仍可开
    main_on = resolve_thinking(thinking_role="main", profile_enable=None)
    _ok("main_role_default_on", main_on.get("enable_thinking") is True)

    print("demo_fast: all ok")


if __name__ == "__main__":
    main()
