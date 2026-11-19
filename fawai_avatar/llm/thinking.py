# term:LLM 接入  tool:llm_chat  doc:doc/功能/01-接入与呈现/01-LLM接入/说明.md
"""按调用角色 + 模型档案解析 enable_thinking / thinking_budget。"""
from __future__ import annotations

from typing import Any

# 角色默认：主脑开思考；快模型 / 观察反省关。不做逐步 LLM 分类。
ROLE_ENABLE_THINKING: dict[str, bool] = {
    "main": True,
    "fast": False,
    "obs_reflect": False,
}


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
        # 0/空不封顶，不传 thinking_budget。禁止再注入角色默认预算。

    return {
        "enable_thinking": enabled,
        "thinking_budget": budget,
        "role": role,
    }


def _thinking_context_blob(
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> str:
    return f"{base_url or ''} {model or ''} {provider_id or ''}".lower()


def _is_dashscope_family(
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> bool:
    pid = (provider_id or "").strip().lower()
    if pid == "dashscope":
        return True
    blob = _thinking_context_blob(
        base_url=base_url, model=model, provider_id=provider_id
    )
    return any(
        x in blob
        for x in (
            "dashscope",
            "aliyuncs.com",
            "qwen",
            "tongyi",
        )
    )


def _is_kklt(
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> bool:
    pid = (provider_id or "").strip().lower()
    if pid == "kklt":
        return True
    blob = _thinking_context_blob(
        base_url=base_url, model=model, provider_id=provider_id
    )
    return "kklt.lol" in blob


def _is_deepseek(
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> bool:
    """官方 DeepSeek、模型名含 deepseek、或自定义口挂 deepseek 模型。"""
    pid = (provider_id or "").strip().lower()
    if pid == "deepseek":
        return True
    blob = _thinking_context_blob(
        base_url=base_url, model=model, provider_id=provider_id
    )
    return "deepseek" in blob


def provider_supports_thinking_extra(
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> bool:
    """对已接线供应商写入思考开关，避免未知兼容口报参数错误。

    已接线：DashScope / 千问系、内置小黄（``kklt``）、DeepSeek（模型名 / ``api.deepseek.com``）。
    """
    return (
        _is_dashscope_family(
            base_url=base_url, model=model, provider_id=provider_id
        )
        or _is_kklt(base_url=base_url, model=model, provider_id=provider_id)
        or _is_deepseek(base_url=base_url, model=model, provider_id=provider_id)
    )


def thinking_extra_body(
    resolved: dict[str, Any] | None,
    *,
    base_url: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """已接线供应商：按方言写入 ``extra_body`` 思考开关。

    - DashScope / 千问 / 小黄 / 百炼上的 DeepSeek：``enable_thinking``（及可选 ``thinking_budget``）
    - 官方 DeepSeek 与其它挂 deepseek 模型的兼容口：``thinking.type=enabled|disabled``
      （官方默认开思考；关必须显式 ``disabled``。不把 ``thinking_budget`` 映射成 ``reasoning_effort``。）
    """
    if not isinstance(resolved, dict):
        return {}
    if not provider_supports_thinking_extra(
        base_url=base_url, model=model, provider_id=provider_id
    ):
        return {}

    enabled = resolved.get("enable_thinking")
    # DeepSeek 官方方言：非百炼族时用 thinking.type（百炼文档对 DeepSeek 仍用 enable_thinking）
    use_deepseek_native = _is_deepseek(
        base_url=base_url, model=model, provider_id=provider_id
    ) and not _is_dashscope_family(
        base_url=base_url, model=model, provider_id=provider_id
    )
    if use_deepseek_native:
        if enabled is None:
            return {}
        return {
            "thinking": {"type": "enabled" if bool(enabled) else "disabled"},
        }

    out: dict[str, Any] = {}
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
