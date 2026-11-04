# term:LLM 接入  tool:llm_chat  doc:doc/功能/LLM接入/说明.md
from __future__ import annotations

import json
from typing import Any

from fawai_avatar.config import LlmConfig
from fawai_avatar.llm.router import route_model
from fawai_avatar.llm.thinking import (
    resolve_thinking,
    thinking_extra_body,
    thinking_meta,
)


class LlmClient:
    """OpenAI 兼容对话 + tool calling；按消息内容路由文本/多模态模型。"""

    def __init__(self, config: LlmConfig | None = None) -> None:
        self.config = config or LlmConfig.from_env()

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        force_multimodal: bool | None = None,
        thinking_role: str | None = None,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            return {
                "ok": False,
                "error": "未配置 FAWAI_LLM_API_KEY / OPENAI_API_KEY",
                "content": "",
                "tool_calls": [],
            }
        try:
            from openai import OpenAI
        except ImportError:
            return {"ok": False, "error": "未安装 openai 包", "content": "", "tool_calls": []}

        chosen, route_kind = route_model(
            messages,
            self.config,
            model=model,
            force_multimodal=force_multimodal,
        )
        client_kwargs: dict[str, Any] = {
            "api_key": self.config.api_key,
            "base_url": self.config.base_url or None,
        }
        if self.config.timeout_sec is not None:
            client_kwargs["timeout"] = self.config.timeout_sec
        client = OpenAI(**client_kwargs)
        kwargs: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
        }
        kwargs.update(self.config.chat_sampling_kwargs(chosen))
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice

        thinking = self._resolve_thinking_for_model(
            chosen,
            thinking_role=thinking_role,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        )
        extra = thinking_extra_body(
            thinking,
            base_url=self.config.base_url,
            model=chosen,
        )
        if extra:
            kwargs["extra_body"] = extra

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 对上层返回可行动错误
            return {
                "ok": False,
                "error": str(exc),
                "content": "",
                "tool_calls": [],
                "model": chosen,
                "route": route_kind,
                "thinking": thinking_meta(thinking),
            }

        msg = resp.choices[0].message
        tool_calls: list[dict[str, Any]] = []
        if msg.tool_calls:
            for call in msg.tool_calls:
                args = call.function.arguments or "{}"
                tool_calls.append(
                    {
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": args,
                        "arguments_obj": _safe_json(args),
                    }
                )
        return {
            "ok": True,
            "content": msg.content or "",
            "tool_calls": tool_calls,
            "model": chosen,
            "route": route_kind,
            "usage": _usage_dict(getattr(resp, "usage", None)),
            "thinking": thinking_meta(thinking),
        }

    def _resolve_thinking_for_model(
        self,
        model: str,
        *,
        thinking_role: str | None,
        enable_thinking: bool | None,
        thinking_budget: int | None,
    ) -> dict[str, Any]:
        from fawai_avatar.llm.model_profiles import load_profile, thinking_profile_params

        profile = load_profile(model)
        tp = thinking_profile_params(
            profile.get("params") if isinstance(profile, dict) else None
        )
        return resolve_thinking(
            thinking_role=thinking_role,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            profile_enable=tp.get("enable_thinking"),
            profile_budget=tp.get("thinking_budget"),
        )

    def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        """OpenAI-compatible embeddings API (for lore vector index)."""
        if not self.config.api_key:
            return {
                "ok": False,
                "error": "未配置 FAWAI_LLM_API_KEY / OPENAI_API_KEY",
                "embeddings": [],
            }
        try:
            from openai import OpenAI
        except ImportError:
            return {"ok": False, "error": "未安装 openai 包", "embeddings": []}
        from fawai_avatar.config import lore_embed_model

        chosen = (model or lore_embed_model()).strip()
        client_kwargs: dict[str, Any] = {
            "api_key": self.config.api_key,
            "base_url": self.config.base_url or None,
        }
        if self.config.timeout_sec is not None:
            client_kwargs["timeout"] = self.config.timeout_sec
        client = OpenAI(**client_kwargs)
        try:
            resp = client.embeddings.create(model=chosen, input=list(texts))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "embeddings": [], "model": chosen}
        vectors: list[list[float]] = []
        for item in resp.data:
            vectors.append([float(x) for x in item.embedding])
        return {
            "ok": True,
            "embeddings": vectors,
            "model": chosen,
            "usage": _usage_dict(getattr(resp, "usage", None)),
        }


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    model: str | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
    force_multimodal: bool | None = None,
    thinking_role: str | None = None,
    enable_thinking: bool | None = None,
    thinking_budget: int | None = None,
) -> dict[str, Any]:
    return LlmClient().chat(
        messages,
        tools,
        model=model,
        tool_choice=tool_choice,
        force_multimodal=force_multimodal,
        thinking_role=thinking_role,
        enable_thinking=enable_thinking,
        thinking_budget=thinking_budget,
    )


def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    config: LlmConfig | None = None,
) -> dict[str, Any]:
    """OpenAI-compatible embeddings; used by lore vector index."""
    return LlmClient(config).embed(texts, model=model)


def _safe_json(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"value": obj}
    except json.JSONDecodeError:
        return {"_raw": text}


def _usage_dict(usage: Any) -> dict[str, int]:
    """归一化 OpenAI usage → prompt/completion/total（缺字段则 0）。"""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
    else:
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        total = getattr(usage, "total_tokens", None)
    out: dict[str, int] = {}
    if prompt is not None:
        out["prompt_tokens"] = int(prompt)
    if completion is not None:
        out["completion_tokens"] = int(completion)
    if total is not None:
        out["total_tokens"] = int(total)
    elif out:
        out["total_tokens"] = int(out.get("prompt_tokens", 0)) + int(
            out.get("completion_tokens", 0)
        )
    return out


if __name__ == "__main__":
    # 本地试跑：纯文本路由
    PROMPT = "只回复两个字：你好"
    CHECK_ROUTE_ONLY = False  # True 时不调 API，只打印路由结果

    from fawai_avatar.llm.router import route_model

    cfg = LlmConfig.from_env()
    text_msgs = [{"role": "user", "content": PROMPT}]
    vision_msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "图里有什么？一句话"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"
                    },
                },
            ],
        }
    ]
    print("text route", route_model(text_msgs, cfg))
    print("vision route", route_model(vision_msgs, cfg))
    if not CHECK_ROUTE_ONLY:
        r = LlmClient(cfg).chat(text_msgs, thinking_role="main")
        print(
            {
                "ok": r.get("ok"),
                "model": r.get("model"),
                "route": r.get("route"),
                "thinking": r.get("thinking"),
                "content": (r.get("content") or "")[:80],
                "error": r.get("error"),
            }
        )
