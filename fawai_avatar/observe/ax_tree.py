# term:AX观察 tool:obs_ax doc:doc/功能/自主操控/说明.md
"""macOS 辅助功能（AX）树观察：产出带 ref/bounds 的控件摘要。

非 Darwin / headless：失败摘要，不抛崩闭环。
ref 缓存在模块级，供 ui_click / ui_type / verify_ui_ax 解析。
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Any

from fawai_avatar.types import Observation

_MAX_NODES = 80
_MAX_DEPTH = 8
_SUMMARY_VALUE_MAX = 80

_lock = threading.RLock()
# ref(str) -> node dict（含 x/y/w/h、role、title、value、pid）
_REF_NODES: dict[str, dict[str, Any]] = {}
_LAST_FLAT_TEXT = ""


@dataclass(frozen=True)
class AxNode:
    ref: str
    role: str
    title: str
    value: str
    x: float
    y: float
    w: float
    h: float
    pid: int


def clear_ax_registry() -> None:
    with _lock:
        _REF_NODES.clear()
        global _LAST_FLAT_TEXT
        _LAST_FLAT_TEXT = ""


def get_ax_node(ref: str) -> dict[str, Any] | None:
    key = str(ref or "").strip()
    if not key:
        return None
    with _lock:
        node = _REF_NODES.get(key)
        return dict(node) if node else None


def list_ax_nodes() -> list[dict[str, Any]]:
    with _lock:
        return [dict(v) for _, v in sorted(_REF_NODES.items(), key=lambda kv: kv[0])]


def _normalize_query(text: str) -> str:
    s = (text or "").strip().lower()
    for ch in ("-", "_", ".", "/", ":", "#"):
        s = s.replace(ch, " ")
    return " ".join(s.split())


def resolve_ax_ref(ref: str) -> tuple[dict[str, Any] | None, str]:
    """解析 ui_* 的 ref：先精确编号，再按 title/value/role 子串模糊匹配。

    返回 (node, how)。how 为 exact / fuzzy / empty / miss。
    """
    key = str(ref or "").strip()
    if not key:
        return None, "empty"
    node = get_ax_node(key)
    if node is not None:
        return node, "exact"

    query = _normalize_query(key)
    tokens = [t for t in query.split() if len(t) >= 2]
    if not tokens and query:
        tokens = [query]
    if not tokens:
        return None, "miss"

    best: dict[str, Any] | None = None
    best_score = 0
    for cand in list_ax_nodes():
        blob = _normalize_query(
            f"{cand.get('role') or ''} {cand.get('title') or ''} {cand.get('value') or ''}"
        )
        if not blob:
            continue
        score = 0
        if query and query in blob:
            score += 10
        for tok in tokens:
            if tok in blob:
                score += 1
        # 常见臆造名：input/field/chat/text 等弱词单独不够
        if score > best_score:
            best_score = score
            best = cand
    # 至少命中 2 个有意义 token，或整句子串命中
    weak = {"input", "field", "text", "button", "chat", "box", "area", "edit"}
    strong_hits = [t for t in tokens if t not in weak and t in _normalize_query(
        f"{(best or {}).get('role') or ''} {(best or {}).get('title') or ''} {(best or {}).get('value') or ''}"
    )] if best else []
    if best is not None and (
        best_score >= 10
        or len(strong_hits) >= 1
        or (best_score >= 2 and len(tokens) >= 2)
    ):
        return dict(best), "fuzzy"
    return None, "miss"


def format_available_refs(*, limit: int = 12) -> str:
    nodes = list_ax_nodes()
    if not nodes:
        return "（当前无 AX 缓存：请先 observe_kinds 含 obs_ax）"
    lines: list[str] = []
    for n in nodes[:limit]:
        title = str(n.get("title") or "")[:40]
        value = str(n.get("value") or "")[:40]
        role = str(n.get("role") or "")
        lines.append(f"{n.get('ref')}|{role}|{title}|{value}")
    more = f" …共{len(nodes)}个" if len(nodes) > limit else ""
    return "可用 ref（编号|role|title|value）：" + " || ".join(lines) + more


def ax_flat_text() -> str:
    with _lock:
        return _LAST_FLAT_TEXT


def observe_ax(*, max_nodes: int = _MAX_NODES, max_depth: int = _MAX_DEPTH) -> Observation:
    """采集前台 App 的 AX 树摘要。"""
    try:
        from fawai_avatar.platform_info import is_headless, runtime_platform

        plat = runtime_platform()
        if is_headless():
            return Observation(
                summary="无界面运行，无法采集 AX（obs_ax）。请改用 obs_fs / shell。",
                meta={"observe_method": "obs_ax", "ok": False, "reason": "headless"},
            )
        if plat.system != "Darwin" and not sys.platform.startswith("darwin"):
            return Observation(
                summary=(
                    f"当前平台 {plat.system} 首期未实现 obs_ax（仅 macOS AX）。"
                    "请改用 obs_screen 坐标 click，或在 Darwin 上验收 C3。"
                ),
                meta={
                    "observe_method": "obs_ax",
                    "ok": False,
                    "reason": "unsupported_os",
                    "platform": plat.system,
                },
            )
    except Exception as exc:  # noqa: BLE001
        return Observation(
            summary=f"obs_ax 平台探测失败：{exc}",
            meta={"observe_method": "obs_ax", "ok": False, "reason": "platform_probe_failed"},
        )

    try:
        nodes, app_name, flat = _collect_macos_ax(max_nodes=max_nodes, max_depth=max_depth)
    except Exception as exc:  # noqa: BLE001
        return Observation(
            summary=f"AX 采集失败：{exc}（请确认已授权辅助功能）",
            meta={
                "observe_method": "obs_ax",
                "ok": False,
                "reason": "ax_failed",
                "error": str(exc),
            },
        )

    with _lock:
        _REF_NODES.clear()
        for n in nodes:
            _REF_NODES[n.ref] = {
                "ref": n.ref,
                "role": n.role,
                "title": n.title,
                "value": n.value,
                "x": n.x,
                "y": n.y,
                "w": n.w,
                "h": n.h,
                "pid": n.pid,
            }
        global _LAST_FLAT_TEXT
        _LAST_FLAT_TEXT = flat

    lines = [
        f"AX 观察（app={app_name or '?'}，nodes={len(nodes)}）。"
        "动作请用 ui_click/ui_type，args.ref 取下表编号。",
        "ref | role | title | value | bounds(x,y,w,h)",
    ]
    for n in nodes:
        title = _short(n.title)
        value = _short(n.value)
        lines.append(
            f"{n.ref} | {n.role} | {title} | {value} | "
            f"({int(n.x)},{int(n.y)},{int(n.w)},{int(n.h)})"
        )
    if not nodes:
        lines.append("（无节点：检查辅助功能权限，或 App 无暴露 AX）")

    return Observation(
        summary="\n".join(lines),
        meta={
            "observe_method": "obs_ax",
            "ok": True,
            "app": app_name,
            "ax_count": len(nodes),
            "ax_nodes": [
                {
                    "ref": n.ref,
                    "role": n.role,
                    "title": n.title,
                    "value": n.value,
                    "x": n.x,
                    "y": n.y,
                    "w": n.w,
                    "h": n.h,
                }
                for n in nodes
            ],
        },
    )


def refresh_ax_flat_text(*, max_nodes: int = _MAX_NODES, max_depth: int = _MAX_DEPTH) -> str:
    """再采一遍 AX，更新 registry 与扁平文本，供回证。"""
    obs = observe_ax(max_nodes=max_nodes, max_depth=max_depth)
    if not (obs.meta or {}).get("ok"):
        return ""
    return ax_flat_text()


def find_ref_for_role(*, role_substr: str = "", title_substr: str = "") -> str | None:
    """测试辅助：按角色/标题子串找第一个 ref。"""
    role_l = (role_substr or "").lower()
    title_l = (title_substr or "").lower()
    for node in list_ax_nodes():
        role = str(node.get("role") or "").lower()
        title = str(node.get("title") or "").lower()
        value = str(node.get("value") or "").lower()
        if role_l and role_l not in role:
            continue
        if title_l and title_l not in title and title_l not in value:
            continue
        return str(node.get("ref") or "") or None
    return None


def _short(text: str, limit: int = _SUMMARY_VALUE_MAX) -> str:
    s = (text or "").replace("\n", "\\n").strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _collect_macos_ax(*, max_nodes: int, max_depth: int) -> tuple[list[AxNode], str, str]:
    """经 ApplicationServices / Cocoa 采前台应用 AX 树。"""
    try:
        from ApplicationServices import (  # type: ignore
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            kAXChildrenAttribute,
            kAXErrorSuccess,
            kAXPositionAttribute,
            kAXRoleAttribute,
            kAXSizeAttribute,
            kAXTitleAttribute,
            kAXValueAttribute,
            kAXWindowsAttribute,
        )
        from Cocoa import NSWorkspace  # type: ignore
        from Quartz import (  # type: ignore
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError as exc:
        import sys

        missing = str(exc).replace("No module named ", "").strip("'\"") or "pyobjc"
        raise RuntimeError(
            f"缺少 pyobjc 模块（{missing}；需 ApplicationServices/Cocoa/Quartz）。"
            f"请用当前解释器安装：{sys.executable} -m pip install -e '.[gui]' "
            "（勿用裸 pip，易命中坏 shebang）；并确认已授权辅助功能"
        ) from exc

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        raise RuntimeError("无前台应用")
    pid = int(app.processIdentifier())
    app_name = str(app.localizedName() or app.bundleIdentifier() or "")

    ax_app = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(ax_app, kAXWindowsAttribute, None)
    # pyobjc 常返回 (error, value) 或直接 value；兼容两种
    windows = _ax_unwrap(err, windows)
    if not windows:
        # 退化：系统级前台窗
        windows = []

    nodes: list[AxNode] = []
    flat_parts: list[str] = [app_name]

    def walk(element: Any, depth: int) -> None:
        if len(nodes) >= max_nodes or depth > max_depth:
            return
        role = _ax_str(element, kAXRoleAttribute, AXUIElementCopyAttributeValue) or ""
        title = _ax_str(element, kAXTitleAttribute, AXUIElementCopyAttributeValue) or ""
        value = _ax_str(element, kAXValueAttribute, AXUIElementCopyAttributeValue) or ""
        pos = _ax_point(element, kAXPositionAttribute, AXUIElementCopyAttributeValue)
        size = _ax_size(element, kAXSizeAttribute, AXUIElementCopyAttributeValue)
        x = float(pos[0]) if pos else 0.0
        y = float(pos[1]) if pos else 0.0
        w = float(size[0]) if size else 0.0
        h = float(size[1]) if size else 0.0

        interesting = _is_interesting(role, title, value, w, h, x=x, y=y)
        if interesting:
            ref = str(len(nodes) + 1)
            nodes.append(
                AxNode(
                    ref=ref,
                    role=role,
                    title=title,
                    value=value,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    pid=pid,
                )
            )
            for part in (role, title, value):
                if part:
                    flat_parts.append(str(part))

        if depth >= max_depth:
            return
        children = _ax_attr(element, kAXChildrenAttribute, AXUIElementCopyAttributeValue)
        if not children:
            return
        try:
            child_list = list(children)
        except TypeError:
            return
        for child in child_list:
            if len(nodes) >= max_nodes:
                break
            walk(child, depth + 1)

    if windows:
        try:
            win_list = list(windows)
        except TypeError:
            win_list = []
        for win in win_list[:3]:
            walk(win, 0)
    else:
        # 无 windows 属性时走应用根
        walk(ax_app, 0)

    # 若树空，尝试用 CGWindow 标题补一条信息
    if not nodes:
        try:
            infos = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
            for info in list(infos or [])[:5]:
                owner = str(info.get("kCGWindowOwnerName") or "")
                if owner and app_name and owner != app_name:
                    continue
                title = str(info.get("kCGWindowName") or "")
                bounds = info.get("kCGWindowBounds") or {}
                ref = "1"
                nodes.append(
                    AxNode(
                        ref=ref,
                        role="AXWindow",
                        title=title or owner,
                        value="",
                        x=float(bounds.get("X") or 0),
                        y=float(bounds.get("Y") or 0),
                        w=float(bounds.get("Width") or 0),
                        h=float(bounds.get("Height") or 0),
                        pid=pid,
                    )
                )
                break
        except Exception:  # noqa: BLE001
            pass

    flat = "\n".join(p for p in flat_parts if p)
    return nodes, app_name, flat


def _ax_unwrap(err: Any, value: Any) -> Any:
    # 部分绑定：CopyAttributeValue 返回 value，err 为 out；此处 err 可能是 value
    if value is None and err is not None and not isinstance(err, int):
        return err
    return value


def _ax_attr(element: Any, attr: Any, copy_fn: Any) -> Any:
    try:
        result = copy_fn(element, attr, None)
    except TypeError:
        try:
            result = copy_fn(element, attr)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    if isinstance(result, tuple) and len(result) == 2:
        err, val = result
        if isinstance(err, int) and err != 0:
            return None
        return val
    return result


def _ax_str(element: Any, attr: Any, copy_fn: Any) -> str:
    val = _ax_attr(element, attr, copy_fn)
    if val is None:
        return ""
    try:
        return str(val)
    except Exception:  # noqa: BLE001
        return ""


def _ax_point(element: Any, attr: Any, copy_fn: Any) -> tuple[float, float] | None:
    val = _ax_attr(element, attr, copy_fn)
    if val is None:
        return None
    try:
        # AXValue / NSValue
        if hasattr(val, "x") and hasattr(val, "y"):
            return float(val.x), float(val.y)
        if isinstance(val, (tuple, list)) and len(val) >= 2:
            return float(val[0]), float(val[1])
        # CF AXValue decode via description fallback
        text = str(val)
        # e.g. "x=10 y=20" or NSPoint
        import re

        m = re.search(r"([-\d.]+).+?([-\d.]+)", text)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:  # noqa: BLE001
        return None
    return None


def _ax_size(element: Any, attr: Any, copy_fn: Any) -> tuple[float, float] | None:
    val = _ax_attr(element, attr, copy_fn)
    if val is None:
        return None
    try:
        if hasattr(val, "width") and hasattr(val, "height"):
            return float(val.width), float(val.height)
        if isinstance(val, (tuple, list)) and len(val) >= 2:
            return float(val[0]), float(val[1])
        import re

        text = str(val)
        m = re.search(r"([-\d.]+).+?([-\d.]+)", text)
        if m:
            return float(m.group(1)), float(m.group(2))
    except Exception:  # noqa: BLE001
        return None
    return None


def _is_interesting(
    role: str, title: str, value: str, w: float, h: float, *, x: float = 0.0, y: float = 0.0
) -> bool:
    # Electron 等常给出荒谬 bounds；仍进树会诱使 ui_click 假成功——直接不收
    if w <= 0 or h <= 0:
        return False
    if abs(x + w / 2.0) > 100_000 or abs(y + h / 2.0) > 100_000:
        return False
    role_l = (role or "").lower()
    if any(
        k in role_l
        for k in (
            "button",
            "text",
            "field",
            "area",
            "link",
            "menu",
            "checkbox",
            "radio",
            "tab",
            "cell",
            "row",
            "window",
            "scroll",
            "combo",
            "pop",
            "slider",
            "image",
            "static",
            "group",
        )
    ):
        return True
    if (title or value) and (w > 2 or h > 2):
        return True
    return False
