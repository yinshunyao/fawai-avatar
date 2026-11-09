# term:步级执行档  doc:doc/功能/步级执行档/说明.md
"""规划同轮三档：reflect / brief / act —— 管成功后观察反省深度。

任务级开关：meta.step_tier（compose「分档」，默认关）。
开时提示词要求写 step_run，闭环按档分流；关时忽略字段、不写提示。
与 Fast / Effort 解耦。
"""
from __future__ import annotations

from typing import Any

from fawai_avatar.brain.action_segment import (
    check_action_expect,
    format_mechanical_obs_summary,
)
from fawai_avatar.types import Action, ExecResult

STEP_RUNS = frozenset({"reflect", "brief", "act"})


def coerce_step_tier(value: Any) -> bool:
    """任务级分档开关；缺省 / 无法识别 → False（关）。"""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    s = str(value or "").strip().lower()
    if not s:
        return False
    if s in {"1", "true", "yes", "on", "step_tier", "tier"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return False


def step_tier_from_meta(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return coerce_step_tier(meta.get("step_tier"))


def step_tier_from_inbound_meta(meta: dict[str, Any] | None) -> bool | None:
    """入站是否显式带 step_tier。未带 → None。"""
    if not isinstance(meta, dict) or "step_tier" not in meta:
        return None
    return coerce_step_tier(meta.get("step_tier"))


def planner_step_tier_rule(*, step_tier: bool) -> str:
    if not step_tier:
        return ""
    return (
        "- **分档开（step_tier）**：每步须填 `step_run`（与 actions 同轮写出，禁止另开一轮评难度）。"
        "`act`=仅执行（工具成功+规则/expect 过则跳过观察反省 LLM）；"
        "`brief`=短观察反省；`reflect`=完整观察反省。"
        "路径清楚/带 expect 用 `act`；要确认没跑偏用 `brief`；易假成功/架构/交稿前用 `reflect`。"
        "失败仍走执行反省。与极速/全力互不影响。\n"
    )


def planner_step_run_format_field(*, step_tier: bool) -> str:
    """JSON 示例里的 step_run 字段片段（含尾逗号）。"""
    if not step_tier:
        return ""
    return '"step_run":"act|brief|reflect",'

STEP_RUN_LABELS_ZH: dict[str, str] = {
    "reflect": "完整反省",
    "brief": "短观察反省",
    "act": "仅执行",
}

STEP_RUN_ALIASES: dict[str, str] = {
    "complex": "reflect",
    "full": "reflect",
    "obs_reflect": "reflect",
    "normal": "reflect",
    "simple": "brief",
    "briefly": "brief",
    "check": "brief",
    "execute": "act",
    "run": "act",
    "exec": "act",
    "skip": "act",
}


def step_run_label(kind: str) -> str:
    k = (kind or "").strip().lower()
    return STEP_RUN_LABELS_ZH.get(k, k or "执行档")


def normalize_step_run(raw: Any) -> str:
    """规范化 step_run；无法识别返回空串（跟现网默认）。"""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "act" if raw is False else "brief"
    m = str(raw or "").strip().lower()
    if not m:
        return ""
    m = STEP_RUN_ALIASES.get(m, m)
    # strip accidental typo key
    if m not in STEP_RUNS:
        return ""
    return m


def resolve_step_run(raw: Any) -> str:
    return normalize_step_run(raw)


def format_act_obs_summary(
    actions: list[Action],
    results: list[ExecResult],
) -> str:
    """仅执行档：规则检查摘要（不调观察反省 LLM）。"""
    # 与机械摘要同形，便于 history 解析；文案标明 act
    base = format_mechanical_obs_summary(actions, results)
    return base.replace(
        "机械只读批全部成功，跳过 LLM 观察反省。",
        "本步 step_run=act：工具成功且规则验收通过，跳过 LLM 观察反省。",
        1,
    ).replace("【观察反省·规则】", "【观察反省·仅执行·规则】", 1)


def act_rule_ok(
    actions: list[Action],
    results: list[ExecResult],
) -> tuple[bool, str]:
    """仅执行档成功路径的规则检查。

    前置：调用方已确认全部 result.ok。
    - 有 expect 的动作再核对一次（闭环 normally 已套过 expect）
    - 无 expect：工具 ok 即通过（不报错不需要反省）
    """
    if not actions or not results:
        return False, "empty batch"
    if len(results) < len(actions):
        return False, "incomplete results"
    if not all(r.ok for r in results):
        return False, "tool not ok"
    for i, action in enumerate(actions):
        result = results[i]
        ok, reason = check_action_expect(action, result)
        if not ok:
            return False, reason or "expect failed"
    return True, ""
