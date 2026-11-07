# term:截屏识别接地  tool:screenshot_grounding  doc:doc/功能/截屏识别接地/说明.md
"""截屏后按配置做 VLM 附图或 OCR 出框表。"""
from __future__ import annotations

from typing import Any

from fawai_avatar.types import Observation

SCREENSHOT_GROUNDING_VLM = "vlm"
SCREENSHOT_GROUNDING_OCR = "ocr"
_VALID = frozenset({SCREENSHOT_GROUNDING_VLM, SCREENSHOT_GROUNDING_OCR})


def normalize_screenshot_grounding(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if s in {"ocr", "qwen-ocr", "text_ocr"}:
        return SCREENSHOT_GROUNDING_OCR
    if s in {"vlm", "vision", "multimodal", ""}:
        return SCREENSHOT_GROUNDING_VLM
    return SCREENSHOT_GROUNDING_VLM if s not in _VALID else s


def get_screenshot_grounding() -> str:
    from fawai_avatar.llm.settings_store import get_store

    return normalize_screenshot_grounding(get_store().get_screenshot_grounding())


def apply_screenshot_grounding(obs: Observation) -> Observation:
    """对已成功截屏的 Observation 按配置后处理。

    - vlm：只打 meta，规划仍附图。
    - ocr：调 API → 登记 ocr ref → 摘要追加表；成功则标记 detach_image_for_plan。
    """
    mode = get_screenshot_grounding()
    meta = dict(obs.meta or {})
    meta["screenshot_grounding"] = mode
    if mode != SCREENSHOT_GROUNDING_OCR:
        return Observation(
            summary=obs.summary,
            image_path=obs.image_path,
            width=obs.width,
            height=obs.height,
            meta=meta,
        )

    path = (obs.image_path or "").strip()
    if not path:
        meta["ocr_ok"] = False
        meta["ocr_error"] = "no_image_path"
        return Observation(
            summary=obs.summary + "\nOCR 跳过：无截屏路径。",
            image_path=obs.image_path,
            width=obs.width,
            height=obs.height,
            meta=meta,
        )

    from fawai_avatar.llm.ocr import ocr_image_words
    from fawai_avatar.observe.ocr_tree import clear_ocr_registry, format_ocr_summary, register_ocr_words

    clear_ocr_registry()
    result = ocr_image_words(path)
    if not result.get("ok"):
        meta["ocr_ok"] = False
        meta["ocr_error"] = str(result.get("error") or "ocr_failed")
        meta["ocr_model"] = result.get("model")
        # 降级：仍允许规划附图（VLM 兜底）
        meta["detach_image_for_plan"] = False
        meta["ocr_fallback_attach_image"] = True
        summary = (
            f"{obs.summary}\nOCR 失败（将降级附图给 VLM）：{meta['ocr_error']}"
        )
        return Observation(
            summary=summary,
            image_path=obs.image_path,
            width=obs.width,
            height=obs.height,
            meta=meta,
        )

    words = list(result.get("words") or [])
    nodes = register_ocr_words(
        words,
        origin_x=float(meta.get("origin_x") or 0),
        origin_y=float(meta.get("origin_y") or 0),
        coord_scale_x=float(meta.get("coord_scale_x") or 1),
        coord_scale_y=float(meta.get("coord_scale_y") or 1),
        image_path=path,
    )
    table = format_ocr_summary(nodes)
    meta["ocr_ok"] = True
    meta["ocr_count"] = len(nodes)
    meta["ocr_model"] = result.get("model")
    meta["ocr_transport"] = result.get("transport")
    meta["detach_image_for_plan"] = True
    meta["ocr_usage"] = result.get("usage") or {}
    summary = f"{obs.summary}\n{table}"
    return Observation(
        summary=summary,
        image_path=obs.image_path,
        width=obs.width,
        height=obs.height,
        meta=meta,
    )
