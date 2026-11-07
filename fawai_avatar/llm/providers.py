# term:LLM 接入  doc:doc/功能/LLM接入/说明.md
"""内置 LLM 供应商预设：选供应商 → 默认 base_url + 路由模型；密钥仍由用户填写。"""
from __future__ import annotations

from typing import Any

# 预设 id 稳定，写入 FAWAI_LLM_PROVIDER；custom 时 base_url 可手改。
PROVIDER_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "dashscope",
        "label": "阿里云百炼",
        "label_en": "Alibaba DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "defaults": {
            "FAWAI_LLM_TEXT_MODEL": "qwen3.7-plus",
            "FAWAI_LLM_VLM_MODEL": "qwen3-vl-plus",
            "FAWAI_LLM_FAST_MODEL": "qwen3.8-27b",
            "FAWAI_LLM_ASR_MODEL": "qwen3-asr-flash",
            "FAWAI_LLM_OCR_MODEL": "qwen3.5-ocr",
            "FAWAI_LORE_EMBED_MODEL": "text-embedding-v3",
            "FAWAI_LLM_MODEL": "",
        },
    },
    {
        "id": "kklt",
        "label": "小黄",
        "label_en": "Xiaohuang (kklt)",
        "base_url": "https://api.kklt.lol/v1",
        "defaults": {
            "FAWAI_LLM_TEXT_MODEL": "gpt-6-sol",
            "FAWAI_LLM_VLM_MODEL": "gpt-6-sol",
            "FAWAI_LLM_FAST_MODEL": "gpt-6-sol",
            # 本组通常无 ASR / OCR / embed 渠道，留空避免误调
            "FAWAI_LLM_ASR_MODEL": "",
            "FAWAI_LLM_OCR_MODEL": "",
            "FAWAI_LORE_EMBED_MODEL": "",
            "FAWAI_LLM_MODEL": "",
        },
    },
    {
        "id": "custom",
        "label": "自定义",
        "label_en": "Custom",
        "base_url": "",
        "defaults": {},
    },
)

_PROVIDER_BY_ID = {str(p["id"]): p for p in PROVIDER_PRESETS}


def list_provider_presets() -> list[dict[str, Any]]:
    """给 Web / API 的精简预设表（无密钥）。"""
    out: list[dict[str, Any]] = []
    for p in PROVIDER_PRESETS:
        out.append(
            {
                "id": str(p["id"]),
                "label": str(p["label"]),
                "label_en": str(p.get("label_en") or p["label"]),
                "base_url": str(p.get("base_url") or ""),
                "defaults": dict(p.get("defaults") or {}),
            }
        )
    return out


def get_provider(provider_id: str | None) -> dict[str, Any] | None:
    pid = (provider_id or "").strip()
    if not pid:
        return None
    return _PROVIDER_BY_ID.get(pid)


def match_provider_id(base_url: str | None) -> str:
    """按 base_url 反推预设；无匹配则 custom。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return "custom"
    for p in PROVIDER_PRESETS:
        pid = str(p["id"])
        if pid == "custom":
            continue
        preset = str(p.get("base_url") or "").strip().rstrip("/")
        if preset and url == preset:
            return pid
        # 兼容少写 /v1 或尾斜杠差异已在上方 rstrip；再比 host 关键字
        if pid == "dashscope" and "dashscope.aliyuncs.com" in url:
            return pid
        if pid == "kklt" and "api.kklt.lol" in url:
            return pid
    return "custom"


def resolve_provider_id(*, provider: str | None, base_url: str | None) -> str:
    """显式 FAWAI_LLM_PROVIDER 优先；否则按 base_url 推断。"""
    pid = (provider or "").strip()
    if pid in _PROVIDER_BY_ID:
        return pid
    return match_provider_id(base_url)
