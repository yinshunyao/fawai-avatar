# term:LLM 接入  tool:llm_chat  doc:doc/功能/LLM接入/说明.md
from __future__ import annotations

import json
from typing import Any, Callable

from fawai_avatar.config import LlmConfig
from fawai_avatar.llm.router import route_model
from fawai_avatar.llm.thinking import (
    resolve_thinking,
    thinking_extra_body,
    thinking_meta,
)

OnDelta = Callable[[dict[str, Any]], None]


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
        route_role: str | None = None,
        thinking_role: str | None = None,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
        on_delta: OnDelta | None = None,
    ) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError:
            return {"ok": False, "error": "未安装 openai 包", "content": "", "tool_calls": []}

        from fawai_avatar.llm.settings_store import get_store

        chosen, route_kind, role = route_model(
            messages,
            self.config,
            model=model,
            force_multimodal=force_multimodal,
            route_role=route_role,
        )
        ep = get_store().resolve_route(role)
        api_key = (ep.api_key or "").strip()
        if not api_key:
            return {
                "ok": False,
                "error": f"角色 {role} 的供应商未配置 API Key（请在模型配置中填写）",
                "content": "",
                "tool_calls": [],
                "model": chosen,
                "route": route_kind,
                "provider": ep.provider_id,
            }
        if not (chosen or "").strip():
            return {
                "ok": False,
                "error": f"角色 {role} 未选择模型",
                "content": "",
                "tool_calls": [],
                "route": route_kind,
                "provider": ep.provider_id,
            }

        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": ep.base_url or None,
        }
        if ep.timeout_sec is not None:
            client_kwargs["timeout"] = ep.timeout_sec
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
            base_url=ep.base_url,
            model=chosen,
            provider_id=ep.provider_id,
        )
        if extra:
            kwargs["extra_body"] = extra

        t_meta = thinking_meta(thinking)
        if on_delta is not None:
            return self._chat_stream(
                client,
                kwargs,
                chosen=chosen,
                route_kind=route_kind,
                thinking_meta_val=t_meta,
                on_delta=on_delta,
                provider_id=ep.provider_id,
            )

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
                "provider": ep.provider_id,
                "thinking": t_meta,
            }

        msg = resp.choices[0].message
        return {
            "ok": True,
            "content": msg.content or "",
            "reasoning": _message_reasoning(msg),
            "tool_calls": _tool_calls_from_message(msg),
            "model": chosen,
            "route": route_kind,
            "provider": ep.provider_id,
            "usage": _usage_dict(getattr(resp, "usage", None)),
            "thinking": t_meta,
        }

    def _chat_stream(
        self,
        client: Any,
        kwargs: dict[str, Any],
        *,
        chosen: str,
        route_kind: str,
        thinking_meta_val: dict[str, Any] | None,
        on_delta: OnDelta,
        provider_id: str = "",
    ) -> dict[str, Any]:
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        # OpenAI 兼容：末包带 usage；不支持的供应商会忽略或报错，外层再兜底
        stream_kwargs["stream_options"] = {"include_usage": True}

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}

        def _notify() -> None:
            try:
                on_delta(
                    {
                        "thinking": "".join(reasoning_parts),
                        "content": "".join(content_parts),
                        "model": chosen,
                        "thinking_meta": thinking_meta_val,
                    }
                )
            except Exception:  # noqa: BLE001 — 直播回调不得打断生成
                pass

        try:
            stream = client.chat.completions.create(**stream_kwargs)
        except Exception:
            # 部分兼容口不认 stream_options：去掉再试
            stream_kwargs.pop("stream_options", None)
            try:
                stream = client.chat.completions.create(**stream_kwargs)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": str(exc),
                    "content": "",
                    "tool_calls": [],
                    "model": chosen,
                    "route": route_kind,
                    "provider": provider_id,
                    "thinking": thinking_meta_val,
                }

        try:
            for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = _usage_dict(chunk_usage) or usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(str(piece))
                reason = _delta_reasoning(delta)
                if reason:
                    reasoning_parts.append(reason)
                _accumulate_tool_delta(tool_acc, getattr(delta, "tool_calls", None))
                if piece or reason:
                    _notify()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "content": "".join(content_parts),
                "reasoning": "".join(reasoning_parts),
                "tool_calls": _tool_calls_from_acc(tool_acc),
                "model": chosen,
                "route": route_kind,
                "provider": provider_id,
                "usage": usage,
                "thinking": thinking_meta_val,
            }

        _notify()
        return {
            "ok": True,
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "tool_calls": _tool_calls_from_acc(tool_acc),
            "model": chosen,
            "route": route_kind,
            "provider": provider_id,
            "usage": usage,
            "thinking": thinking_meta_val,
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
        try:
            from openai import OpenAI
        except ImportError:
            return {"ok": False, "error": "未安装 openai 包", "embeddings": []}
        from fawai_avatar.llm.settings_store import get_store

        ep = get_store().resolve_route("embed")
        api_key = (ep.api_key or "").strip()
        if not api_key:
            return {
                "ok": False,
                "error": "嵌入路由的供应商未配置 API Key",
                "embeddings": [],
            }
        chosen = (model or ep.model or "").strip()
        if not chosen:
            return {"ok": False, "error": "未配置嵌入模型", "embeddings": []}
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": ep.base_url or None,
        }
        if ep.timeout_sec is not None:
            client_kwargs["timeout"] = ep.timeout_sec
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
            "provider": ep.provider_id,
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
    on_delta: OnDelta | None = None,
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
        on_delta=on_delta,
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


def _attr_or_extra(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    val = getattr(obj, name, None)
    if val:
        return val
    if isinstance(obj, dict):
        return obj.get(name)
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict) and extra.get(name):
        return extra.get(name)
    # pydantic v1 / 部分 SDK
    extra2 = getattr(obj, "__pydantic_extra__", None)
    if isinstance(extra2, dict) and extra2.get(name):
        return extra2.get(name)
    return None


def _message_reasoning(msg: Any) -> str:
    for key in ("reasoning_content", "reasoning", "thinking"):
        val = _attr_or_extra(msg, key)
        if isinstance(val, str) and val:
            return val
    return ""


def _delta_reasoning(delta: Any) -> str:
    for key in ("reasoning_content", "reasoning", "thinking"):
        val = _attr_or_extra(delta, key)
        if isinstance(val, str) and val:
            return val
    return ""


def _tool_calls_from_message(msg: Any) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    raw = getattr(msg, "tool_calls", None) or []
    for call in raw:
        args = call.function.arguments or "{}"
        tool_calls.append(
            {
                "id": call.id,
                "name": call.function.name,
                "arguments": args,
                "arguments_obj": _safe_json(args),
            }
        )
    return tool_calls


def _accumulate_tool_delta(acc: dict[int, dict[str, Any]], tool_calls: Any) -> None:
    if not tool_calls:
        return
    for tc in tool_calls:
        idx = int(getattr(tc, "index", 0) or 0)
        slot = acc.setdefault(
            idx,
            {"id": "", "name": "", "arguments": ""},
        )
        tid = getattr(tc, "id", None)
        if tid:
            slot["id"] = str(tid)
        fn = getattr(tc, "function", None)
        if fn is not None:
            name = getattr(fn, "name", None)
            if name:
                slot["name"] = str(name)
            args = getattr(fn, "arguments", None)
            if args:
                slot["arguments"] = str(slot.get("arguments") or "") + str(args)


def _tool_calls_from_acc(acc: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx in sorted(acc.keys()):
        slot = acc[idx]
        args = slot.get("arguments") or "{}"
        out.append(
            {
                "id": slot.get("id") or f"call_{idx}",
                "name": slot.get("name") or "",
                "arguments": args,
                "arguments_obj": _safe_json(args),
            }
        )
    return out


if __name__ == "__main__":
    # 本地试跑：纯文本路由
    PROMPT = "只回复两个字：你好"
    CHECK_ROUTE_ONLY = False  # True 时不调 API，只打印路由结果
    STREAM_DEMO = False  # True 时走 on_delta 流式

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
        if STREAM_DEMO:
            chunks: list[str] = []

            def _on_delta(d: dict[str, Any]) -> None:
                chunks.append(
                    f"t={len(d.get('thinking') or '')} c={len(d.get('content') or '')}"
                )

            r = LlmClient(cfg).chat(
                text_msgs, thinking_role="main", on_delta=_on_delta
            )
            print({"deltas": len(chunks), "last": chunks[-3:] if chunks else []})
        else:
            r = LlmClient(cfg).chat(text_msgs, thinking_role="main")
        print(
            {
                "ok": r.get("ok"),
                "model": r.get("model"),
                "route": r.get("route"),
                "thinking": r.get("thinking"),
                "reasoning_len": len(r.get("reasoning") or ""),
                "content": (r.get("content") or "")[:80],
                "error": r.get("error"),
            }
        )
