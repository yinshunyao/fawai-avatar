# term:多屏观察  doc:doc/功能/多屏与全屏观察/说明.md
"""显示器枚举与 screen_target 解析；不截屏。"""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Any

_DEFAULT_TARGET = "frontmost"


def list_displays() -> list[dict[str, Any]]:
    """结构化显示器清单（mss 序号 1…N；不含虚拟全桌面条目）。

    headless / 无 mss：返回空列表。
    """
    try:
        from fawai_avatar.platform_info import is_headless

        if is_headless():
            return []
    except Exception:  # noqa: BLE001
        pass
    try:
        import mss
    except ImportError:
        return []
    out: list[dict[str, Any]] = []
    with mss.mss() as sct:
        # monitors[0] = 虚拟全桌面；1…N = 单屏
        for idx in range(1, len(sct.monitors)):
            mon = sct.monitors[idx]
            left = int(mon.get("left", 0))
            top = int(mon.get("top", 0))
            width = int(mon.get("width", 0))
            height = int(mon.get("height", 0))
            out.append(
                {
                    "index": idx,
                    "bounds": {
                        "left": left,
                        "top": top,
                        "width": width,
                        "height": height,
                    },
                    "is_main": idx == 1,
                    "scale": None,
                    "id": None,
                }
            )
    if len(out) == 1:
        out[0]["is_main"] = True
    elif out:
        # 主屏：含原点 (0,0) 的那块；否则仍标 index=1
        main_i = 0
        for i, d in enumerate(out):
            b = d["bounds"]
            if b["left"] <= 0 <= b["left"] + b["width"] and b["top"] <= 0 <= b[
                "top"
            ] + b["height"]:
                main_i = i
                break
        for i, d in enumerate(out):
            d["is_main"] = i == main_i
    return out


def format_displays_block(displays: list[dict[str, Any]] | None) -> str:
    rows = displays or []
    if not rows:
        return "显示器清单：不可用（无界面或未装 mss）"
    lines = [f"显示器清单（共 {len(rows)} 块；截屏用 screen_target 点选）："]
    for d in rows:
        b = d.get("bounds") or {}
        main = "主屏" if d.get("is_main") else "外接"
        lines.append(
            f"- index={d.get('index')} ({main}) "
            f"bounds=({b.get('left')},{b.get('top')}) "
            f"{b.get('width')}x{b.get('height')}"
        )
    lines.append(
        "screen_target：frontmost（默认）| main | index:<n> | all；"
        "一步一块屏，未见目标再换屏。"
    )
    return "\n".join(lines)


def normalize_screen_target(raw: Any) -> str:
    """规范化 screen_target；空 → frontmost。"""
    if raw is None:
        return _DEFAULT_TARGET
    text = str(raw).strip().lower()
    if not text:
        return _DEFAULT_TARGET
    if text in {"frontmost", "front", "active", "focused"}:
        return "frontmost"
    if text in {"main", "primary", "primary_display"}:
        return "main"
    if text in {"all", "virtual", "virtual_all", "desktop"}:
        return "all"
    m = re.fullmatch(r"(?:index[:#]?)?(\d+)", text)
    if m:
        return f"index:{int(m.group(1))}"
    if text.startswith("index:"):
        rest = text.split(":", 1)[1].strip()
        if rest.isdigit():
            return f"index:{int(rest)}"
    return text


def resolve_capture_region(
    screen_target: str | None = None,
    *,
    displays: list[dict[str, Any]] | None = None,
    frontmost_point: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """解析为 mss grab 用的 region 与 meta。

    返回：left/top/width/height、origin_x/origin_y、screen_target、monitor_index、
    target（main|index|all|frontmost）、is_virtual_all。
    """
    target = normalize_screen_target(screen_target)
    rows = displays if displays is not None else list_displays()
    if not rows:
        # 无枚举时仍尝试主屏默认矩形，由上层 mss 再失败
        return {
            "left": 0,
            "top": 0,
            "width": 0,
            "height": 0,
            "origin_x": 0,
            "origin_y": 0,
            "screen_target": target,
            "monitor_index": 1,
            "target": target if target != "frontmost" else "main",
            "is_virtual_all": target == "all",
            "displays_empty": True,
        }

    def _from_row(row: dict[str, Any], *, label: str) -> dict[str, Any]:
        b = row["bounds"]
        return {
            "left": int(b["left"]),
            "top": int(b["top"]),
            "width": int(b["width"]),
            "height": int(b["height"]),
            "origin_x": int(b["left"]),
            "origin_y": int(b["top"]),
            "screen_target": target,
            "monitor_index": int(row["index"]),
            "target": label,
            "is_virtual_all": False,
            "displays_empty": False,
        }

    if target == "all":
        try:
            import mss

            with mss.mss() as sct:
                mon0 = sct.monitors[0]
                left = int(mon0.get("left", 0))
                top = int(mon0.get("top", 0))
                return {
                    "left": left,
                    "top": top,
                    "width": int(mon0.get("width", 0)),
                    "height": int(mon0.get("height", 0)),
                    "origin_x": left,
                    "origin_y": top,
                    "screen_target": "all",
                    "monitor_index": 0,
                    "target": "virtual_all",
                    "is_virtual_all": True,
                    "displays_empty": False,
                }
        except Exception:  # noqa: BLE001 — 拼合失败则退主屏
            target = "main"

    if target == "main":
        for row in rows:
            if row.get("is_main"):
                return _from_row(row, label="main")
        return _from_row(rows[0], label="main")

    if target.startswith("index:"):
        want = int(target.split(":", 1)[1])
        for row in rows:
            if int(row["index"]) == want:
                return _from_row(row, label=f"index:{want}")
        # 越界 → 主屏
        for row in rows:
            if row.get("is_main"):
                out = _from_row(row, label="main")
                out["screen_target_fallback"] = target
                return out
        out = _from_row(rows[0], label="main")
        out["screen_target_fallback"] = target
        return out

    # frontmost
    point = frontmost_point
    if point is None:
        point = probe_frontmost_point()
    if point is not None:
        px, py = point
        for row in rows:
            b = row["bounds"]
            if (
                b["left"] <= px < b["left"] + b["width"]
                and b["top"] <= py < b["top"] + b["height"]
            ):
                out = _from_row(row, label="frontmost")
                out["frontmost_point"] = [px, py]
                return out
    # 单屏或找不到前台点 → 主屏（单屏等价）
    for row in rows:
        if row.get("is_main"):
            out = _from_row(row, label="frontmost")
            out["frontmost_fallback"] = "main"
            return out
    return _from_row(rows[0], label="frontmost")


def probe_frontmost_point(
    *, timeout_sec: float = 2.0
) -> tuple[float, float] | None:
    """前台窗口中心的全局坐标；失败返回 None。"""
    try:
        from fawai_avatar.platform_info import is_headless, runtime_platform

        if is_headless():
            return None
        system = runtime_platform().system
    except Exception:  # noqa: BLE001
        system = "Darwin" if sys.platform == "darwin" else sys.platform

    if system == "Darwin" or sys.platform == "darwin":
        return _frontmost_point_macos(timeout_sec=timeout_sec)
    if system == "Linux" or sys.platform.startswith("linux"):
        return _frontmost_point_linux(timeout_sec=timeout_sec)
    return None


def _frontmost_point_macos(*, timeout_sec: float) -> tuple[float, float] | None:
    script = (
        'tell application "System Events"\n'
        "  set frontApp to first application process whose frontmost is true\n"
        "  try\n"
        "    set p to position of front window of frontApp\n"
        "    set s to size of front window of frontApp\n"
        "    set x to (item 1 of p) + (item 1 of s) / 2\n"
        "    set y to (item 2 of p) + (item 2 of s) / 2\n"
        '    return (x as string) & "," & (y as string)\n'
        "  end try\n"
        "end tell\n"
        'return ""\n'
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text or "," not in text:
        return None
    try:
        xs, ys = text.split(",", 1)
        return float(xs), float(ys)
    except ValueError:
        return None


def _frontmost_point_linux(*, timeout_sec: float) -> tuple[float, float] | None:
    import shutil

    if not shutil.which("xdotool"):
        return None
    try:
        proc = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowgeometry"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    # Position: X,Y  Geometry: WxH
    pos = None
    size = None
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("Position:"):
            # Position: 100,200 (screen: 0)
            part = line.split(":", 1)[1].strip().split()[0]
            try:
                xs, ys = part.split(",", 1)
                pos = (float(xs), float(ys))
            except ValueError:
                pass
        elif line.startswith("Geometry:"):
            part = line.split(":", 1)[1].strip().split()[0]
            try:
                ws, hs = part.lower().split("x", 1)
                size = (float(ws), float(hs))
            except ValueError:
                pass
    if pos is None:
        return None
    if size is None:
        return pos
    return pos[0] + size[0] / 2.0, pos[1] + size[1] / 2.0


def screen_target_from_observe_args(observe_args: dict[str, Any] | None) -> str:
    """从 Plan.observe_args.obs_screen 取 screen_target。"""
    raw = observe_args or {}
    screen = raw.get("obs_screen")
    if isinstance(screen, dict):
        return normalize_screen_target(screen.get("screen_target"))
    if isinstance(screen, str):
        return normalize_screen_target(screen)
    # 兼容顶栏误写
    if "screen_target" in raw:
        return normalize_screen_target(raw.get("screen_target"))
    return _DEFAULT_TARGET
