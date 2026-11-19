# term:主脑委派  doc:doc/功能/03-大脑/02-主脑委派/说明.md
"""主脑 → 快模型 / Cursor Agent 委派：解析 plan.delegate、格式化回灌摘要。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fawai_avatar.types import Plan

_SUPPORTED_TIERS = frozenset({"fast", "cursor"})


@dataclass(frozen=True)
class DelegateSpec:
    tier: str
    goal: str
    stop_when: str = ""
    max_steps: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "goal": self.goal,
            "stop_when": self.stop_when,
            "max_steps": self.max_steps,
        }


_DEFAULT_MAX_STEPS = 5
_HARD_MAX_STEPS = 12


def fast_model_from_env() -> str:
    """快模型路由；空则未配置（委派关闭）。真源为 llm_settings 库。"""
    from fawai_avatar.llm.settings_store import get_store

    ep = get_store().resolve_route("fast")
    if not ep.has_key:
        return ""
    return (ep.model or "").strip()


def cursor_agent_ready() -> bool:
    """Cursor Agent 已启用且可委派。"""
    try:
        from fawai_avatar.cursor_agent import manager as ca_mgr

        return ca_mgr.ready_or_error() is None
    except Exception:  # noqa: BLE001
        return False


def cursor_model_id() -> str:
    try:
        from fawai_avatar.cursor_agent.settings_store import get_store

        return get_store().model_id()
    except Exception:  # noqa: BLE001
        return ""


def parse_delegate(raw: Any) -> DelegateSpec | None:
    """从 plan JSON / raw 解析委派；支持 tier=fast|cursor。"""
    if raw is None:
        return None
    if isinstance(raw, DelegateSpec):
        return raw if raw.tier in _SUPPORTED_TIERS and raw.goal.strip() else None
    if not isinstance(raw, dict):
        return None
    tier = str(raw.get("tier") or raw.get("model") or "").strip().lower()
    if tier in {"fast_model", "quick", "flash"}:
        tier = "fast"
    if tier in {"cursor_agent", "cursor-sdk", "coding_agent"}:
        tier = "cursor"
    if tier not in _SUPPORTED_TIERS:
        return None
    goal = str(raw.get("goal") or raw.get("subgoal") or raw.get("task") or "").strip()
    if not goal:
        return None
    stop_when = str(raw.get("stop_when") or raw.get("done_when") or "").strip()
    try:
        max_steps = int(raw.get("max_steps") or _DEFAULT_MAX_STEPS)
    except (TypeError, ValueError):
        max_steps = _DEFAULT_MAX_STEPS
    max_steps = max(1, min(_HARD_MAX_STEPS, max_steps))
    return DelegateSpec(tier=tier, goal=goal, stop_when=stop_when, max_steps=max_steps)


def plan_delegate(plan: Plan | None) -> DelegateSpec | None:
    if plan is None:
        return None
    if plan.delegate:
        return parse_delegate(plan.delegate)
    return parse_delegate((plan.raw or {}).get("delegate"))


def with_delegate(plan: Plan, spec: DelegateSpec | None) -> Plan:
    raw = dict(plan.raw or {})
    if spec is None:
        raw.pop("delegate", None)
        return Plan(
            thought=plan.thought,
            actions=list(plan.actions),
            done=plan.done,
            status=plan.status,
            raw=raw,
            observe_kinds=list(plan.observe_kinds),
            observe_args=dict(plan.observe_args or {}),
            title=plan.title or "",
            step_label=plan.step_label or "",
            step_kind=plan.step_kind or "",
            step_run=plan.step_run or "",
            delegate=None,
        )
    raw["delegate"] = spec.to_dict()
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        step_kind=plan.step_kind or "",
        step_run=plan.step_run or "",
        delegate=spec.to_dict(),
    )


def format_delegate_handoff(spec: DelegateSpec, *, model: str) -> str:
    stop = spec.stop_when or "子目标完成或无法继续"
    if spec.tier == "cursor":
        return (
            f"【主脑委派·Cursor Agent】model={model}\n"
            f"子目标：{spec.goal}\n"
            f"停条件：{stop}\n"
            "由 Cursor local agent 执行；完成后摘要回灌主脑。"
        )
    return (
        f"【主脑委派·快模型】model={model}\n"
        f"子目标：{spec.goal}\n"
        f"停条件：{stop}\n"
        f"段内最多 {spec.max_steps} 步；禁止再委派；完成后用 success/failed 收束并写清结果。"
    )


def format_delegate_feedback(
    spec: DelegateSpec,
    *,
    outcome: str,
    summary: str,
    steps_used: int,
    model: str,
) -> str:
    """回灌主脑的用户消息（摘要，非整段工具原文）。"""
    stop = spec.stop_when or "（未写停条件）"
    body = (summary or "").strip() or "（无摘要）"
    if len(body) > 1200:
        body = body[:1200] + "…"
    label = "Cursor Agent 回灌" if spec.tier == "cursor" else "快模型回灌"
    return (
        f"【{label}】outcome={outcome} model={model} steps={steps_used}\n"
        f"原委派子目标：{spec.goal}\n"
        f"原停条件：{stop}\n"
        f"结果摘要：\n{body}\n"
        "请主脑验收：继续规划、换道、或 success/failed 收尾。勿重复已完成的委派段。"
    )
