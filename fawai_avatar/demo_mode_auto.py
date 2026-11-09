# term:执行配置自动模式  doc:doc/功能/执行配置自动模式/说明.md
"""执行配置三态：推导、mode_prefs、启发式分析自检。"""

from __future__ import annotations

import os

from fawai_avatar.brain.mode_auto import (
    PREF_AUTO,
    PREF_OFF,
    PREF_ON,
    any_pref_auto,
    apply_auto_prefs,
    coerce_mode_pref,
    inbound_prefs_patch,
    prefs_from_analysis,
    prefs_from_meta,
    summarize_prefs,
)
from fawai_avatar.brain.orchestrator import analyze_task_exec_config


def _ok(name: str, cond: bool) -> None:
    print(("OK  " if cond else "FAIL") + f" {name}")
    if not cond:
        raise SystemExit(1)


def main() -> None:
    _ok("pref_default_auto", coerce_mode_pref(None) == PREF_AUTO)
    _ok("pref_on", coerce_mode_pref("on") == PREF_ON)
    _ok("pref_off", coerce_mode_pref(0) == PREF_OFF)

    _ok("meta_default_all_auto", prefs_from_meta({})["fast"] == PREF_AUTO)
    _ok(
        "meta_explicit",
        prefs_from_meta({"mode_prefs": {"fast": "on", "effort": "auto"}})["fast"]
        == PREF_ON,
    )
    _ok(
        "legacy_lock",
        prefs_from_meta(
            {"mode_auto": True, "mode_auto_lock": {"effort": True}, "effort": True}
        )["effort"]
        == PREF_ON,
    )
    _ok(
        "legacy_global_off",
        prefs_from_meta({"mode_auto": False, "fast": True})["fast"] == PREF_ON,
    )

    simple = prefs_from_analysis(simple=True, want_effort=True, enter=True)
    _ok("simple_forces_fast", simple["fast"] is True)
    _ok("simple_no_tier", simple["step_tier"] is False)

    mid = prefs_from_analysis(simple=False, want_effort=False, enter=False)
    _ok("mid_tier_on", mid["step_tier"] is True)

    meta = {
        "mode_prefs": {
            "fast": "auto",
            "effort": "off",
            "step_tier": "auto",
            "plan_auto": "auto",
        },
        "effort": False,
    }
    patch = apply_auto_prefs(meta, simple=False, want_effort=True, enter=True)
    _ok("skip_manual_effort", "effort" not in patch)
    _ok("apply_tier", patch.get("step_tier") is True)
    _ok("keep_prefs", patch.get("mode_prefs", {}).get("effort") == PREF_OFF)
    _ok("any_auto", any_pref_auto(meta) is True)

    inbound = inbound_prefs_patch(
        {
            "mode_prefs": {
                "fast": "auto",
                "effort": "on",
                "step_tier": "off",
                "plan_auto": "auto",
            }
        }
    )
    _ok("inbound_writes_effort", inbound.get("effort") is True)
    _ok("inbound_writes_tier_off", inbound.get("step_tier") is False)
    _ok("inbound_skips_fast_bool", "fast" not in inbound)

    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    cfg = analyze_task_exec_config("查一下 GPU 状态")
    _ok("heuristic_simple_query", cfg.get("simple") is True)

    cfg_mid = analyze_task_exec_config("修改 insect/train/foo.py 里的 batch 为 8")
    _ok("heuristic_mid_not_simple", cfg_mid.get("simple") is False)

    cfg2 = analyze_task_exec_config(
        "先读配置，然后登录服务器，接着替换模型并且要重启推理服务"
    )
    _ok("heuristic_multi_enter", cfg2.get("enter") is True)

    text = summarize_prefs(
        {
            "mode_prefs": {"fast": "auto", "effort": "on", "step_tier": "off", "plan_auto": "auto"},
            "fast": True,
            "effort": True,
            "step_tier": False,
            "plan_auto": False,
        }
    )
    _ok("summary_auto_resolved", "极速自动·开" in text)
    _ok("summary_manual_on", "全力开" in text)

    print("demo_mode_auto: all ok")


if __name__ == "__main__":
    main()
