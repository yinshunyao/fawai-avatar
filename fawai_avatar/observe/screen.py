# term:观察 tool:ui_observe doc:doc/功能/观察/说明.md
# term:多屏观察  doc:doc/功能/多屏与全屏观察/说明.md
from __future__ import annotations

import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.observe.displays import (
    list_displays,
    normalize_screen_target,
    resolve_capture_region,
)
from fawai_avatar.types import Observation


def _ensure_out_dir(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _default_observe_dir() -> Path:
    cfg = RuntimeConfig.from_env()
    return Path(cfg.data_dir) / "observe"


def _capture_with_mss(
    out_path: Path, region: dict[str, Any]
) -> tuple[int, int, dict[str, Any]]:
    try:
        import mss
        import mss.tools
    except ImportError as exc:
        raise RuntimeError(
            "未安装 mss。桌面截屏请用当前解释器："
            f"{__import__('sys').executable} -m pip install -e '.[gui]'"
        ) from exc

    grab = {
        "left": int(region["left"]),
        "top": int(region["top"]),
        "width": int(region["width"]),
        "height": int(region["height"]),
    }
    if grab["width"] <= 0 or grab["height"] <= 0:
        with mss.mss() as sct:
            # 区域无效时回落主屏
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            grab = {
                "left": int(mon["left"]),
                "top": int(mon["top"]),
                "width": int(mon["width"]),
                "height": int(mon["height"]),
            }
            region = {
                **region,
                **grab,
                "origin_x": grab["left"],
                "origin_y": grab["top"],
                "monitor_index": 1,
            }

    with mss.mss() as sct:
        shot = sct.grab(grab)
        mss.tools.to_png(shot.rgb, shot.size, output=str(out_path))
        meta: dict[str, Any] = {
            "method": "mss",
            "monitor": int(region.get("monitor_index") or 1),
            "origin_x": int(region.get("origin_x", grab["left"])),
            "origin_y": int(region.get("origin_y", grab["top"])),
            "screen_target": region.get("screen_target") or "frontmost",
            "target": region.get("target"),
            "is_virtual_all": bool(region.get("is_virtual_all")),
        }
        if region.get("screen_target_fallback"):
            meta["screen_target_fallback"] = region["screen_target_fallback"]
        if region.get("frontmost_fallback"):
            meta["frontmost_fallback"] = region["frontmost_fallback"]
        if region.get("frontmost_point"):
            meta["frontmost_point"] = region["frontmost_point"]
        return shot.width, shot.height, meta


def _capture_with_screencapture(
    out_path: Path, region: dict[str, Any]
) -> tuple[int, int, dict[str, Any]]:
    # -D: display id（1-based，近似 mss index）；all / 虚拟全桌面不传 -D
    cmd = ["screencapture", "-x"]
    mon = int(region.get("monitor_index") or 1)
    if not region.get("is_virtual_all") and mon >= 1:
        cmd.extend(["-D", str(mon)])
    cmd.append(str(out_path))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"screencapture failed: {err}")
    if not out_path.is_file():
        raise RuntimeError("screencapture did not create output file")
    width, height = _image_size(out_path)
    meta: dict[str, Any] = {
        "method": "screencapture",
        "monitor": mon,
        "origin_x": int(region.get("origin_x", 0)),
        "origin_y": int(region.get("origin_y", 0)),
        "screen_target": region.get("screen_target") or "frontmost",
        "target": region.get("target"),
        "is_virtual_all": bool(region.get("is_virtual_all")),
    }
    return width, height, meta


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
        return int(w), int(h)
    except ImportError:
        return _png_size_from_header(path)


def _png_size_from_header(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        header = fh.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"not a PNG or unreadable: {path}")
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def capture_screen(
    out_dir: str | Path,
    *,
    screen_target: str | None = None,
    displays: list[dict[str, Any]] | None = None,
) -> Observation:
    """Write a PNG under out_dir and return structured observation.

    screen_target: frontmost（默认）| main | index:<n> | all
    """
    from fawai_avatar.platform_info import is_headless, runtime_platform

    if is_headless():
        p = runtime_platform()
        raise RuntimeError(
            f"无界面运行，无法截屏（{p.system}/{p.headless_source}）"
        )

    directory = _ensure_out_dir(Path(out_dir))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = directory / f"screen_{stamp}_{uuid4().hex[:8]}.png"

    disp = displays if displays is not None else list_displays()
    region = resolve_capture_region(screen_target, displays=disp)
    target_norm = normalize_screen_target(screen_target)

    width: int
    height: int
    meta: dict[str, Any] = {
        "captured_at": stamp,
        "displays": disp,
        "screen_target": target_norm,
    }

    if sys.platform == "darwin":
        try:
            width, height, cap_meta = _capture_with_mss(out_path, region)
            meta.update(cap_meta)
        except Exception as mss_err:  # noqa: BLE001 — fallback path
            if out_path.is_file():
                out_path.unlink(missing_ok=True)
            try:
                width, height, cap_meta = _capture_with_screencapture(out_path, region)
                meta.update(cap_meta)
                meta["mss_error"] = str(mss_err)
            except Exception as sc_err:
                try:
                    from fawai_avatar.control.permissions_mac import prompt_screen_recording

                    prompt_screen_recording(force_dialog=True)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(
                    f"截屏失败（可能缺少屏幕录制权限）: {mss_err}; {sc_err}"
                ) from sc_err
    elif sys.platform.startswith("win"):
        # Windows：本切片多屏未承诺；尽力截主屏/解析区
        try:
            width, height, cap_meta = _capture_with_mss(out_path, region)
            meta.update(cap_meta)
            meta["platform_note"] = "windows_multiscreen_unsupported"
        except Exception as mss_err:  # noqa: BLE001
            raise RuntimeError(
                f"截屏失败（Windows 本切片弱支持）：{mss_err}"
            ) from mss_err
    else:
        try:
            width, height, cap_meta = _capture_with_mss(out_path, region)
            meta.update(cap_meta)
        except Exception as mss_err:  # noqa: BLE001
            raise RuntimeError(
                f"截屏失败（请确认已安装 mss 且有可用显示）：{mss_err}"
            ) from mss_err

    ox = meta.get("origin_x", 0)
    oy = meta.get("origin_y", 0)
    # 采集区逻辑尺寸（mss region）；PNG 可能因 Retina 更大 → click 需 scale
    cap_w = int(region.get("width") or 0)
    cap_h = int(region.get("height") or 0)
    if cap_w > 0 and cap_h > 0:
        meta["capture_width"] = cap_w
        meta["capture_height"] = cap_h
    meta["image_width"] = int(width)
    meta["image_height"] = int(height)
    if cap_w > 0 and cap_h > 0 and width > 0 and height > 0:
        sx = float(cap_w) / float(width)
        sy = float(cap_h) / float(height)
        meta["coord_scale_x"] = sx
        meta["coord_scale_y"] = sy
        if abs(sx - 1.0) > 0.05 or abs(sy - 1.0) > 0.05:
            meta["coord_scale_note"] = (
                f"PNG {width}x{height} ≠ capture {cap_w}x{cap_h}；"
                "click 图内坐标将按 scale 压到逻辑屏再加 origin"
            )
    tgt = meta.get("screen_target") or target_norm
    scale_hint = ""
    if meta.get("coord_scale_note"):
        scale_hint = f" scale=({meta.get('coord_scale_x'):.3f},{meta.get('coord_scale_y'):.3f})"
    summary = (
        f"screenshot {width}x{height} target={tgt} "
        f"origin=({ox},{oy}){scale_hint} at {out_path}"
    )
    return Observation(
        summary=summary,
        image_path=str(out_path.resolve()),
        width=width,
        height=height,
        meta=meta,
    )


def observe(
    out_dir: str | Path | None = None,
    *,
    screen_target: str | None = None,
    displays: list[dict[str, Any]] | None = None,
) -> Observation:
    """Capture screen into data/observe/ (or out_dir) and return observation."""
    directory = out_dir if out_dir is not None else _default_observe_dir()
    return capture_screen(
        directory, screen_target=screen_target, displays=displays
    )
