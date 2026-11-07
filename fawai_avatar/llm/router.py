# term:LLM 接入  tool:llm_route  doc:doc/功能/LLM接入/说明.md
from __future__ import annotations

from typing import Any, Literal

from fawai_avatar.config import LlmConfig

RouteKind = Literal["text", "multimodal"]
RouteRole = Literal["text", "vlm", "fast", "asr", "ocr", "embed", "force"]


def messages_need_vision(messages: list[dict[str, Any]]) -> bool:
    """消息里是否含图片/多模态 content（OpenAI vision 形态）。"""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "")
                if ptype in {"image_url", "image", "input_image"}:
                    return True
                if "image_url" in part or "image" in part:
                    return True
        elif isinstance(content, dict):
            ptype = str(content.get("type") or "")
            if ptype in {"image_url", "image", "input_image"}:
                return True
    return False


def route_model(
    messages: list[dict[str, Any]],
    config: LlmConfig,
    *,
    model: str | None = None,
    force_multimodal: bool | None = None,
    route_role: str | None = None,
) -> tuple[str, RouteKind, str]:
    """
    简单智能路由，返回 ``(model_id, kind, role)``。

    - 显式 ``route_role`` → 用该角色库中的模型（可被显式 model 覆盖 id）
    - 显式 ``model`` 且无 role → 仍按内容标 kind，role 尽量匹配库中同 id 路由，否则 text
    - 含图像 → vlm；否则 text；force 路由有模型时覆盖 text/vlm 的模型 id
    """
    multimodal = force_multimodal if force_multimodal is not None else messages_need_vision(messages)
    kind: RouteKind = "multimodal" if multimodal else "text"

    from fawai_avatar.llm.settings_store import get_store

    store = get_store()
    role_n = (route_role or "").strip()
    if role_n in {"text", "vlm", "fast", "asr", "ocr", "embed", "force"}:
        ep = store.resolve_route(role_n)
        mid = (model or ep.model or "").strip()
        return mid, kind, role_n

    explicit = (model or "").strip()
    if explicit:
        # 显式模型：优先匹配已配置路由
        for cand in ("fast", "force", "vlm", "text", "asr", "ocr", "embed"):
            ep = store.resolve_route(cand)
            if ep.model and ep.model == explicit:
                return explicit, kind, cand
        return explicit, kind, "vlm" if multimodal else "text"

    force = store.resolve_route("force")
    if force.model:
        return force.model, kind, "force"
    if multimodal:
        ep = store.resolve_route("vlm")
        return (ep.model or config.vlm_model), "multimodal", "vlm"
    ep = store.resolve_route("text")
    return (ep.model or config.text_model), "text", "text"
