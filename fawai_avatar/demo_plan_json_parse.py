# term:大脑  doc:doc/功能/大脑/说明.md
"""规划 JSON 半截修复 + 格式软降级冒烟。"""
from __future__ import annotations

from fawai_avatar.brain.planner import (
    _parse_json_object,
    _soft_format_continue,
    _failed_plan,
    format_soft_fallback_history_message,
)
from fawai_avatar.types import Plan


def main() -> None:
    # 与现网截断样本同构：截在 doc_refs 键中间
    truncated = (
        '{"thought":"已激活微信但未观察界面；需对主屏截图并分析是否有新消息红点或内容",'
        '"title":"检查微信新消息","status":"continue","done":false,'
        '"observe_kinds":["obs_screen"],'
        '"observe_args":{"obs_screen":{"screen_target":"main"}},'
        '"doc_re'
    )
    parsed = _parse_json_object(truncated)
    assert parsed is not None, "truncated JSON should repair"
    assert parsed.get("status") == "continue"
    assert parsed.get("observe_kinds") == ["obs_screen"]
    assert (parsed.get("observe_args") or {}).get("obs_screen", {}).get(
        "screen_target"
    ) == "main"
    assert "doc_re" not in parsed

    # 截在 status 值中间
    mid_status = '{"thought":"x","status":"conti'
    parsed2 = _parse_json_object(mid_status)
    assert parsed2 is not None
    assert parsed2.get("thought") == "x"

    # 软降级：不得 done/failed
    failed = _failed_plan("无法解析 JSON：{bad")
    soft = _soft_format_continue(failed)
    assert soft.status == "continue"
    assert soft.done is False
    assert not soft.actions
    assert (soft.raw or {}).get("format_soft_fallback") is True
    msg = format_soft_fallback_history_message(soft)
    assert msg and "JSON" in msg

    # 普通 continue 不触发软降级文案
    plain = Plan(thought="ok", actions=[], done=False, status="continue")
    assert format_soft_fallback_history_message(plain) is None

    print("demo_plan_json_parse: ok")


if __name__ == "__main__":
    main()
