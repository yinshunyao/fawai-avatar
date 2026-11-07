# term:截屏识别接地  tool:obs_ocr_table  doc:doc/功能/截屏识别接地/说明.md
"""OCR 文字框元素表：编号 ref（ocr1…）+ 全局 bounds，供 ui_click 解析。"""
from __future__ import annotations

import threading
from typing import Any

_MAX_NODES = 80
_SUMMARY_TEXT_MAX = 60

_lock = threading.RLock()
_REF_NODES: dict[str, dict[str, Any]] = {}


def clear_ocr_registry() -> None:
    with _lock:
        _REF_NODES.clear()


def get_ocr_node(ref: str) -> dict[str, Any] | None:
    key = str(ref or "").strip()
    if not key:
        return None
    with _lock:
        node = _REF_NODES.get(key)
        return dict(node) if node else None


def list_ocr_nodes() -> list[dict[str, Any]]:
    with _lock:
        return [dict(v) for _, v in sorted(_REF_NODES.items(), key=lambda kv: kv[0])]


def format_available_ocr_refs(*, limit: int = 12) -> str:
    nodes = list_ocr_nodes()
    if not nodes:
        return "（当前无 OCR 表；请确认截屏识别方式为 ocr 且本步已 obs_screen）"
    parts: list[str] = []
    for n in nodes[:limit]:
        t = str(n.get("title") or n.get("text") or "")[:24]
        parts.append(f"{n.get('ref')}={t!r}")
    more = f" …共{len(nodes)}" if len(nodes) > limit else ""
    return "可用 OCR ref：" + ", ".join(parts) + more


def resolve_ocr_ref(ref: str) -> tuple[dict[str, Any] | None, str]:
    """精确匹配 ocrN；也接受裸数字若唯一对应 ocr{n}。"""
    key = str(ref or "").strip()
    if not key:
        return None, "empty"
    node = get_ocr_node(key)
    if node is not None:
        return node, "exact"
    # 兼容模型写 ocr:1 / OCR1
    alt = key.lower().replace(":", "").replace("_", "")
    if alt.startswith("ocr") and alt[3:].isdigit():
        node = get_ocr_node(f"ocr{alt[3:]}")
        if node is not None:
            return node, "exact"
    if key.isdigit():
        node = get_ocr_node(f"ocr{key}")
        if node is not None:
            return node, "exact"
    # 按文案子串模糊
    q = key.lower()
    with _lock:
        hits = [
            dict(v)
            for v in _REF_NODES.values()
            if q and q in str(v.get("title") or "").lower()
        ]
    if len(hits) == 1:
        return hits[0], "fuzzy"
    return None, "miss"


def register_ocr_words(
    words: list[dict[str, Any]],
    *,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    coord_scale_x: float = 1.0,
    coord_scale_y: float = 1.0,
    image_path: str | None = None,
) -> list[dict[str, Any]]:
    """把 OCR 图内框心登记为全局 x/y/w/h（与 AX 节点同形）。"""
    sx = max(float(coord_scale_x or 1.0), 1e-6)
    sy = max(float(coord_scale_y or 1.0), 1e-6)
    ox, oy = float(origin_x or 0), float(origin_y or 0)
    registered: list[dict[str, Any]] = []
    with _lock:
        _REF_NODES.clear()
        for i, w in enumerate(words[:_MAX_NODES], start=1):
            text = str(w.get("text") or "").strip()
            if not text:
                continue
            try:
                cx = float(w.get("cx") or 0)
                cy = float(w.get("cy") or 0)
                bw = float(w.get("w") or 0)
                bh = float(w.get("h") or 0)
            except (TypeError, ValueError):
                continue
            # 图内中心 → 逻辑屏 → 全局；框也按 scale
            gx_c = ox + cx * sx
            gy_c = oy + cy * sy
            gw = max(bw * sx, 4.0)
            gh = max(bh * sy, 4.0)
            ref = f"ocr{i}"
            node = {
                "ref": ref,
                "role": "ocr_text",
                "title": text[:_SUMMARY_TEXT_MAX],
                "text": text,
                "value": text[:_SUMMARY_TEXT_MAX],
                "x": gx_c - gw / 2.0,
                "y": gy_c - gh / 2.0,
                "w": gw,
                "h": gh,
                "source": "ocr",
                "image_cx": cx,
                "image_cy": cy,
                "image_path": image_path,
            }
            _REF_NODES[ref] = node
            registered.append(dict(node))
    return registered


def format_ocr_summary(nodes: list[dict[str, Any]] | None = None) -> str:
    rows = nodes if nodes is not None else list_ocr_nodes()
    if not rows:
        return "OCR 观察：无文字框。"
    lines = [
        f"OCR 观察（nodes={len(rows)}）。动作请用 ui_click/ui_type，args.ref 取下表编号（如 ocr3）。",
        "ref | text | bounds(x,y,w,h)",
    ]
    for n in rows:
        t = str(n.get("title") or "").replace("|", "/")
        lines.append(
            f"{n.get('ref')} | {t} | "
            f"({n.get('x'):.0f},{n.get('y'):.0f},{n.get('w'):.0f},{n.get('h'):.0f})"
        )
    return "\n".join(lines)
