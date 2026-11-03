# term:语音输入  tool:llm_transcribe  doc:doc/功能/语音输入/说明.md
"""语音转写：OpenAI 兼容 chat + input_audio（默认 qwen3-asr-flash）。"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fawai_avatar.config import LlmConfig

# 与 DashScope Qwen3-ASR-Flash 同步上限对齐（编码后仍须 < 10MB）
MAX_AUDIO_BYTES = 8 * 1024 * 1024

_MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".webm": "audio/webm",
    ".flac": "audio/flac",
}


def mime_for_audio(filename: str | None, content_type: str | None = None) -> str:
    """从上传 Content-Type / 文件名推断 MIME；缺省 audio/webm。"""
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw.startswith("audio/") or raw.startswith("video/"):
        return raw
    suffix = Path(filename or "").suffix.lower()
    return _MIME_BY_SUFFIX.get(suffix, "audio/webm")


def transcribe_audio(
    data: bytes,
    *,
    mime_type: str = "audio/webm",
    language: str | None = None,
    config: LlmConfig | None = None,
) -> dict[str, Any]:
    """把录音字节转成文本。成功返回 ``ok/text/model``；失败 ``ok=False`` + ``error``。"""
    cfg = config or LlmConfig.from_env()
    if not cfg.api_key:
        return {"ok": False, "error": "未配置 FAWAI_LLM_API_KEY / OPENAI_API_KEY", "text": ""}
    if not data:
        return {"ok": False, "error": "音频为空", "text": ""}
    if len(data) > MAX_AUDIO_BYTES:
        return {
            "ok": False,
            "error": f"音频过大（>{MAX_AUDIO_BYTES // (1024 * 1024)}MB），请缩短录音",
            "text": "",
        }

    mime = (mime_type or "audio/webm").split(";")[0].strip().lower() or "audio/webm"
    b64 = base64.b64encode(data).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"
    model = cfg.asr_model
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": data_uri},
                }
            ],
        }
    ]
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "未安装 openai 包", "text": ""}

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    lang = (language or "").strip()
    if lang:
        kwargs["extra_body"] = {"asr_options": {"language": lang}}
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001 — 对上层返回可行动错误
        return {"ok": False, "error": str(exc), "text": "", "model": model}

    msg = resp.choices[0].message if resp.choices else None
    content = (getattr(msg, "content", None) or "") if msg else ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)
    text = str(content or "").strip()
    if not text:
        return {"ok": False, "error": "未识别到有效语音", "text": "", "model": model}
    return {
        "ok": True,
        "text": text,
        "model": model,
        "usage": _usage_dict(getattr(resp, "usage", None)),
    }


def _usage_dict(usage: Any) -> dict[str, int]:
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
    # 本地试跑：指向一段短 wav/mp3；不调 API 时可设 CHECK_ONLY
    AUDIO_PATH = ""  # 例：data/sample.wav
    CHECK_ONLY = True

    cfg = LlmConfig.from_env()
    print({"asr_model": cfg.asr_model, "base_url": cfg.base_url, "has_key": bool(cfg.api_key)})
    if not CHECK_ONLY and AUDIO_PATH:
        path = Path(AUDIO_PATH)
        r = transcribe_audio(path.read_bytes(), mime_type=mime_for_audio(path.name), config=cfg)
        print({"ok": r.get("ok"), "text": (r.get("text") or "")[:120], "error": r.get("error")})
