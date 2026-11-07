# term:截屏识别接地  doc:doc/功能/截屏识别接地/说明.md
"""OCR 接地自检：解析/登记/配置；可选真调 API。"""
from __future__ import annotations

from fawai_avatar.llm.ocr import normalize_word_boxes, _native_generation_url
from fawai_avatar.observe.ocr_tree import (
    clear_ocr_registry,
    format_ocr_summary,
    get_ocr_node,
    register_ocr_words,
    resolve_ocr_ref,
)
from fawai_avatar.observe.screen_grounding import (
    SCREENSHOT_GROUNDING_OCR,
    normalize_screenshot_grounding,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    _assert(normalize_screenshot_grounding("OCR") == SCREENSHOT_GROUNDING_OCR, "norm ocr")
    _assert(normalize_screenshot_grounding("") == "vlm", "norm default")

    words = normalize_word_boxes(
        [
            {"text": "发送", "rotate_rect": [100, 50, 40, 20, 0]},
            {"text": "", "rotate_rect": [1, 1, 10, 10, 0]},
            {"location": [0, 0, 20, 0, 20, 10, 0, 10], "text": "取消"},
        ]
    )
    _assert(len(words) == 2, f"words={words}")
    _assert(words[0]["text"] == "发送", words[0])
    _assert(abs(words[0]["cx"] - 100) < 1e-6, words[0])

    clear_ocr_registry()
    nodes = register_ocr_words(
        words,
        origin_x=10,
        origin_y=20,
        coord_scale_x=0.5,
        coord_scale_y=0.5,
    )
    _assert(len(nodes) == 2, nodes)
    n0 = get_ocr_node("ocr1")
    _assert(n0 is not None, "ocr1 missing")
    # cx=100 → logical 50 + origin 10 = 60 center; half w=20*0.5=10 → x=50
    _assert(abs(float(n0["x"]) + float(n0["w"]) / 2 - 60) < 1e-3, n0)
    node, how = resolve_ocr_ref("ocr1")
    _assert(how == "exact" and node is not None, (how, node))
    node2, how2 = resolve_ocr_ref("1")
    _assert(how2 == "exact" and node2 is not None, (how2, node2))
    summary = format_ocr_summary()
    _assert("ocr1" in summary and "发送" in summary, summary)

    url = _native_generation_url(
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    _assert(url is not None and "multimodal-generation" in url, url)

    from fawai_avatar.llm.settings_store import get_store, reset_store_cache
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        reset_store_cache()
        store = get_store(data_dir=tmp)
        _assert(store.get_screenshot_grounding() == "vlm", "default grounding")
        store.set_screenshot_grounding("ocr")
        _assert(store.get_screenshot_grounding() == "ocr", "set ocr")
        payload = store.settings_payload()
        _assert(payload.get("screenshot_grounding") == "ocr", payload)
        _assert("ocr" in payload.get("roles") or [], payload.get("roles"))
        routes = store.list_routes()
        _assert("ocr" in routes, routes)
        reset_store_cache()

    print("demo_ocr_grounding: ok")
    print({"native_url": url, "summary_lines": summary.count("\n") + 1})


if __name__ == "__main__":
    CHECK_ONLY = True
    IMAGE_PATH = ""  # 非空且 CHECK_ONLY=False 时真调 OCR
    main()
    if not CHECK_ONLY and IMAGE_PATH:
        from fawai_avatar.llm.ocr import ocr_image_words

        r = ocr_image_words(IMAGE_PATH)
        print({"api_ok": r.get("ok"), "n": len(r.get("words") or []), "error": r.get("error")})
