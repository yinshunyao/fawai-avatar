# term:LLM 接入  tool:llm_chat  doc:doc/功能/LLM接入/说明.md
"""本地自检：思考角色默认 + 档案覆盖 + extra_body。"""
from __future__ import annotations

from fawai_avatar.llm.thinking import (
    resolve_thinking,
    thinking_extra_body,
    thinking_meta,
)


def _ok(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(name)
    print("ok", name)


def main() -> None:
    main_r = resolve_thinking(thinking_role="main")
    _ok("main_on", main_r["enable_thinking"] is True)
    _ok("main_no_default_budget", main_r["thinking_budget"] is None)

    profile_cap = resolve_thinking(thinking_role="main", profile_budget=256)
    _ok("profile_budget", profile_cap["thinking_budget"] == 256)

    fast_r = resolve_thinking(thinking_role="fast")
    _ok("fast_off", fast_r["enable_thinking"] is False)
    _ok("fast_no_budget", fast_r["thinking_budget"] is None)

    obs_r = resolve_thinking(thinking_role="obs_reflect")
    _ok("obs_off", obs_r["enable_thinking"] is False)

    # 档案强制关主脑思考
    forced_off = resolve_thinking(thinking_role="main", profile_enable=False)
    _ok("profile_override_off", forced_off["enable_thinking"] is False)

    # 显式优先于角色
    explicit = resolve_thinking(
        thinking_role="fast",
        enable_thinking=True,
        thinking_budget=256,
    )
    _ok("explicit_on", explicit["enable_thinking"] is True)
    _ok("explicit_budget", explicit["thinking_budget"] == 256)

    extra = thinking_extra_body(main_r, model="qwen3.7-plus")
    _ok("extra_enable", extra.get("enable_thinking") is True)
    _ok("extra_no_default_budget", "thinking_budget" not in extra)

    off_extra = thinking_extra_body(fast_r, model="qwen3.7-plus")
    _ok("extra_off", off_extra.get("enable_thinking") is False)
    _ok("extra_off_no_budget", "thinking_budget" not in off_extra)

    kklt_extra = thinking_extra_body(
        main_r, model="gpt-6-sol", base_url="https://api.kklt.lol/v1"
    )
    _ok("kklt_url_enable", kklt_extra.get("enable_thinking") is True)
    kklt_by_id = thinking_extra_body(main_r, provider_id="kklt", model="gpt-6-sol")
    _ok("kklt_provider_id_enable", kklt_by_id.get("enable_thinking") is True)
    kklt_off = thinking_extra_body(fast_r, provider_id="kklt")
    _ok("kklt_role_off", kklt_off.get("enable_thinking") is False)

    skipped = thinking_extra_body(main_r, model="gpt-4o", base_url="https://api.openai.com/v1")
    _ok("skip_unknown_openai", skipped == {})

    meta = thinking_meta(main_r)
    _ok("meta_enabled", isinstance(meta, dict) and meta.get("enabled") is True)
    _ok("meta_no_default_budget", "budget" not in (meta or {}))

    print("demo_thinking: all passed")


if __name__ == "__main__":
    main()
