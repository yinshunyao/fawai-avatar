# term:观察 tool:ui_observe doc:doc/功能/观察/说明.md
from __future__ import annotations

import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.types import Observation


def _ensure_out_dir(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _default_observe_dir() -> Path:
    cfg = RuntimeConfig.from_env()
    return Path(cfg.data_dir) / "observe"


def _capture_with_mss(out_path: Path) -> tuple[int, int, dict[str, Any]]:
    try:
        import mss
        import mss.tools
    except ImportError as exc:
        raise RuntimeError(
            "未安装 mss。桌面截屏请：pip install -e '.[gui]'"
        ) from exc

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=str(out_path))
        meta: dict[str, Any] = {"method": "mss", "monitor": 1}
        return shot.width, shot.height, meta


def _capture_with_screencapture(out_path: Path) -> tuple[int, int, dict[str, Any]]:
    cmd = ["screencapture", "-x", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"screencapture failed: {err}")
    if not out_path.is_file():
        raise RuntimeError("screencapture did not create output file")
    width, height = _image_size(out_path)
    return width, height, {"method": "screencapture"}


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


def capture_screen(out_dir: str | Path) -> Observation:
    """Write a PNG under out_dir and return structured observation."""
    from fawai_avatar.platform_info import is_headless, runtime_platform

    if is_headless():
        p = runtime_platform()
        raise RuntimeError(
            f"无界面运行，无法截屏（{p.system}/{p.headless_source}）"
        )

    directory = _ensure_out_dir(Path(out_dir))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = directory / f"screen_{stamp}_{uuid4().hex[:8]}.png"

    width: int
    height: int
    meta: dict[str, Any] = {"captured_at": stamp}

    if sys.platform == "darwin":
        try:
            width, height, cap_meta = _capture_with_mss(out_path)
            meta.update(cap_meta)
        except Exception as mss_err:  # noqa: BLE001 — fallback path
            if out_path.is_file():
                out_path.unlink(missing_ok=True)
            try:
                width, height, cap_meta = _capture_with_screencapture(out_path)
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
    else:
        try:
            width, height, cap_meta = _capture_with_mss(out_path)
            meta.update(cap_meta)
        except Exception as mss_err:  # noqa: BLE001
            raise RuntimeError(
                f"截屏失败（请确认已安装 mss 且有可用显示）：{mss_err}"
            ) from mss_err

    summary = f"screenshot {width}x{height} at {out_path}"
    return Observation(
        summary=summary,
        image_path=str(out_path.resolve()),
        width=width,
        height=height,
        meta=meta,
    )


def observe(out_dir: str | Path | None = None) -> Observation:
    """Capture screen into data/observe/ (or out_dir) and return observation."""
    directory = out_dir if out_dir is not None else _default_observe_dir()
    return capture_screen(directory)
