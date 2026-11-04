# term:LLM 接入  tool:llm_chat  doc:doc/功能/LLM接入/说明.md
"""按调用角色 + 模型档案解析 enable_thinking / thinking_budget。"""
from __future__ import annotations

from typing import Any

# 角色默认：主脑开思考；快模型 / 观察反省关。不做逐步 LLM 分类。
ROLE_ENABLE_THINKING: dict[str, bool] = {
    "main": True,
    "fast": False,
    "obs_reflect": False,
}

# 主脑开启思考时的默认预算（防 runaway）；档案 / 显式参数可覆盖。
DEFAULT_MAIN_THINKING_BUDGET = 1024


def normalize_thinking_role(role: str | None) -> str | None:
    raw = (role or "").strip().lower()
    if not raw:
        return None
    if raw in ROLE_ENABLE_THINKING:
        return raw
    # 兼容别名
    if raw in {"planner", "brain", "plan"}:
        return "main"
    if raw in {"delegate", "delegate_fast"}:
        return "fast"
    if raw in {"obs", "reflect", "observation_reflect"}:
        return "obs_reflect"
    return raw


def resolve_thinking(
    *,
    thinking_role: str | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
    profile_enable: bool | None = None,
    profile_budget: int | None = None,
) -> dict[str, Any]:
    """
    优先级：本次显式参数 > 调用角色默认 > 模型档案 > 不传（供应商默认）。

    返回：
    - ``enable_thinking``: ``True``/``False`` 时写入请求；``None`` 表示省略
    - ``thinking_budget``: 正整数或 ``None``（关思考或不限制时为 ``None``）
    - ``role``: 归一化角色（可空）
    """
    role = normalize_thinking_role(thinking_role)

    enabled: bool | None
    if enable_thinking is not None:
        enabled = bool(enable_thinking)
    elif role is not None and role in ROLE_ENABLE_THINKING:
        enabled = ROLE_ENABLE_THINKING[role]
        # 档案可覆盖角色默认（例如主脑模型强制关思考）
        if profile_enable is not None:
            enabled = bool(profile_enable)
    elif profile_enable is not None:
        enabled = bool(profile_enable)
    else:
        enabled = None

    budget: int | None = None
    if enabled is True:
        if thinking_budget is not None and int(thinking_budget) > 0:
            budget = int(thinking_budget)
        elif profile_budget is not None and int(profile_budget) > 0:
            budget = int(profile_budget)
        elif role == "main":
            budget = int(DEFAULT_MAIN_THINKING_BUDGET)
        # 其它角色若强制开思考且无预算，不传 budget（供应商默认）

    return {
        "enable_thinking": enabled,
        "thinking_budget": budget,
        "role": role,
    }


def provider_supports_thinking_extra(
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> bool:
    """仅对 DashScope / 千问系写入 ``enable_thinking``，避免其它兼容口报未知参数。"""
    blob = f"{base_url or ''} {model or ''}".lower()
    return any(
        x in blob
        for x in (
            "dashscope",
            "aliyuncs.com",
            "qwen",
            "tongyi",
        )
    )


def thinking_extra_body(
    resolved: dict[str, Any] | None,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """DashScope / 千问兼容：``extra_body`` 字段。"""
    if not isinstance(resolved, dict):
        return {}
    if not provider_supports_thinking_extra(base_url=base_url, model=model):
        return {}
    out: dict[str, Any] = {}
    enabled = resolved.get("enable_thinking")
    if enabled is not None:
        out["enable_thinking"] = bool(enabled)
    budget = resolved.get("thinking_budget")
    if enabled is True and budget is not None and int(budget) > 0:
        out["thinking_budget"] = int(budget)
    return out


def thinking_meta(resolved: dict[str, Any] | None) -> dict[str, Any] | None:
    """写入 plan.raw / progress meta，供时间线展示。"""
    if not isinstance(resolved, dict):
        return None
    enabled = resolved.get("enable_thinking")
    if enabled is None and resolved.get("thinking_budget") is None:
        return None
    meta: dict[str, Any] = {"enabled": bool(enabled) if enabled is not None else None}
    if resolved.get("role"):
        meta["role"] = resolved["role"]
    budget = resolved.get("thinking_budget")
    if budget is not None:
        meta["budget"] = int(budget)
    return meta
