# term:LLM 接入  tool:llm_route  doc:doc/功能/LLM接入/说明.md
from __future__ import annotations

from typing import Any, Literal

from fawai_avatar.config import LlmConfig

RouteKind = Literal["text", "multimodal"]


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
) -> tuple[str, RouteKind]:
    """
    简单智能路由：
    - 显式 model / FAWAI_LLM_MODEL → 原样使用（kind 仍按内容标注）
    - 含图像或多模态标记 → vlm_model
    - 否则 → text_model
    """
    multimodal = force_multimodal if force_multimodal is not None else messages_need_vision(messages)
    kind: RouteKind = "multimodal" if multimodal else "text"
    if model:
        return model, kind
    if config.force_model:
        return config.force_model, kind
    if multimodal:
        return config.vlm_model, "multimodal"
    return config.text_model, "text"
