# term:控制 doc:doc/功能/控制/说明.md
"""macOS 权限探测与主动弹窗引导（辅助功能 / 屏幕录制）。

启动时调用 ``ensure_startup_permissions`` 一次性触发系统授权；
任务执行中默认不再弹窗，只读当前授权状态。
"""
from __future__ import annotations

import platform
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_startup_done = False
_allow_runtime_dialogs = True


def is_macos() -> bool:
    return platform.system() == "Darwin"


@lru_cache(maxsize=1)
def accessibility_trusted() -> bool:
    if not is_macos():
        return True
    try:
        from ctypes import c_bool, cdll, util

        path = util.find_library("ApplicationServices")
        if not path:
            return False
        lib = cdll.LoadLibrary(path)
        lib.AXIsProcessTrusted.restype = c_bool
        lib.AXIsProcessTrusted.argtypes = []
        return bool(lib.AXIsProcessTrusted())
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def screen_recording_granted() -> bool:
    """macOS 10.15+：``CGPreflightScreenCaptureAccess``；失败则试一次静默截屏探测。"""
    if not is_macos():
        return True
    try:
        from ctypes import c_bool, cdll, util

        path = util.find_library("CoreGraphics")
        if path:
            lib = cdll.LoadLibrary(path)
            if hasattr(lib, "CGPreflightScreenCaptureAccess"):
                lib.CGPreflightScreenCaptureAccess.restype = c_bool
                lib.CGPreflightScreenCaptureAccess.argtypes = []
                return bool(lib.CGPreflightScreenCaptureAccess())
    except Exception:  # noqa: BLE001
        pass
    return _probe_screen_capture()


def clear_permission_cache() -> None:
    accessibility_trusted.cache_clear()
    screen_recording_granted.cache_clear()


def _osascript(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )


def _open_privacy_pane(pane: str) -> None:
    # Ventura+ / 较旧系统两套 URL 都试
    urls = [
        f"x-apple.systempreferences:com.apple.preference.security?{pane}",
        f"x-apple.systempreferences:com.apple.Settings.PrivacySecurity.extension?{pane}",
    ]
    for url in urls:
        subprocess.run(["open", url], capture_output=True, text=True)


def _ax_prompt_native() -> bool | None:
    """``AXIsProcessTrustedWithOptions``；无法构造选项时返回 None。"""
    try:
        from ctypes import (
            POINTER,
            c_bool,
            c_char_p,
            c_long,
            c_uint32,
            c_void_p,
            cast,
            cdll,
            util,
        )

        cf_path = util.find_library("CoreFoundation")
        as_path = util.find_library("ApplicationServices")
        if not cf_path or not as_path:
            return None
        cf = cdll.LoadLibrary(cf_path)
        appservices = cdll.LoadLibrary(as_path)

        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p
        k_utf8 = 0x08000100
        key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", k_utf8)
        if not key:
            return None

        # kCFBooleanTrue 符号值为 CFBooleanRef
        true_sym = c_void_p.in_dll(cf, "kCFBooleanTrue")
        true_ref = cast(true_sym, c_void_p)

        keys = (c_void_p * 1)(key)
        values = (c_void_p * 1)(true_ref)

        key_cbs = c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        val_cbs = c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        cf.CFDictionaryCreate.argtypes = [
            c_void_p,
            POINTER(c_void_p),
            POINTER(c_void_p),
            c_long,
            c_void_p,
            c_void_p,
        ]
        cf.CFDictionaryCreate.restype = c_void_p
        options = cf.CFDictionaryCreate(None, keys, values, 1, key_cbs, val_cbs)
        if not options:
            return None

        appservices.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
        appservices.AXIsProcessTrustedWithOptions.restype = c_bool
        return bool(appservices.AXIsProcessTrustedWithOptions(options))
    except Exception:  # noqa: BLE001
        return None


def _cg_request_screen_recording() -> bool | None:
    """``CGRequestScreenCaptureAccess``（10.15+）；不可用返回 None。"""
    try:
        from ctypes import c_bool, cdll, util

        path = util.find_library("CoreGraphics")
        if not path:
            return None
        lib = cdll.LoadLibrary(path)
        if not hasattr(lib, "CGRequestScreenCaptureAccess"):
            return None
        lib.CGRequestScreenCaptureAccess.restype = c_bool
        lib.CGRequestScreenCaptureAccess.argtypes = []
        return bool(lib.CGRequestScreenCaptureAccess())
    except Exception:  # noqa: BLE001
        return None


def _probe_screen_capture() -> bool:
    """静默截一帧探测屏幕录制是否真正可用。"""
    try:
        import mss
        import mss.tools

        with tempfile.TemporaryDirectory(prefix="fawai-perm-") as tmp:
            out = Path(tmp) / "probe.png"
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                mss.tools.to_png(shot.rgb, shot.size, output=str(out))
            return out.is_file() and out.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def prompt_accessibility(*, force_dialog: bool = True) -> bool:
    """若无辅助功能权限：触发系统/引导弹窗。返回当前是否已授权。"""
    if not is_macos():
        return True
    clear_permission_cache()
    if accessibility_trusted():
        return True
    # 启动已引导过：任务中不再弹窗
    if not force_dialog or (_startup_done and not _allow_runtime_dialogs):
        return False

    native = _ax_prompt_native()
    clear_permission_cache()
    if accessibility_trusted():
        return True
    if native is True:
        return True

    # 原生未弹出或仍未授权：自定义引导 + 打开设置
    _osascript(
        'display dialog "fawai-avatar 需要「辅助功能」权限才能控制鼠标键盘。'
        '请在系统设置中勾选运行本程序的终端 / Cursor / Python，然后重试。" '
        'buttons {"打开设置", "稍后"} default button 1 with title "需要辅助功能权限"'
    )
    _open_privacy_pane("Privacy_Accessibility")
    clear_permission_cache()
    return accessibility_trusted()


def prompt_screen_recording(*, force_dialog: bool = True) -> bool:
    """请求屏幕录制权限；返回当前是否已授权（尽力探测）。"""
    if not is_macos():
        return True
    clear_permission_cache()
    if screen_recording_granted():
        return True
    if not force_dialog or (_startup_done and not _allow_runtime_dialogs):
        return False

    native = _cg_request_screen_recording()
    clear_permission_cache()
    if screen_recording_granted():
        return True
    if native is True:
        return True

    _osascript(
        'display dialog "fawai-avatar 需要「屏幕录制」权限才能截屏观察。'
        '请在系统设置中勾选运行本程序的终端 / Cursor / Python，然后重试。" '
        'buttons {"打开设置", "稍后"} default button 1 with title "需要屏幕录制权限"'
    )
    _open_privacy_pane("Privacy_ScreenCapture")
    clear_permission_cache()
    return screen_recording_granted()


def ensure_gui_permissions() -> tuple[bool, str]:
    """GUI 动作前调用。启动已引导过则只检查、不再弹窗。"""
    if not is_macos():
        return True, "not macos"
    clear_permission_cache()
    if accessibility_trusted():
        return True, "accessibility ok"
    if _startup_done or not _allow_runtime_dialogs:
        return False, (
            "macOS 辅助功能未授权：请在系统设置 → 隐私与安全性 → 辅助功能 "
            "中勾选运行本程序的终端 / Cursor / Python 后重启"
        )
    ok = prompt_accessibility(force_dialog=True)
    if ok:
        return True, "accessibility granted"
    return False, (
        "macOS 辅助功能未授权：已弹窗引导，请在系统设置 → 隐私与安全性 → 辅助功能 "
        "中勾选后重试"
    )


def full_disk_access_likely() -> bool:
    """尽力探测「完全磁盘访问」：可读家目录外受保护路径之一则视为已授权。"""
    if not is_macos():
        return True
    probes = [
        Path.home() / "Library" / "Mail",
        Path.home() / "Library" / "Safari",
        Path("/Library/Application Support"),
    ]
    for p in probes:
        try:
            if p.exists():
                next(p.iterdir(), None)
                return True
        except PermissionError:
            continue
        except OSError:
            continue
    # 探针都不存在时无法断定；默认不拦启动
    return True


def prompt_full_disk_access(*, force_dialog: bool = True) -> bool:
    """引导打开「完全磁盘访问」；macOS 无程序化授权 API，只能开设置页。"""
    if not is_macos():
        return True
    if full_disk_access_likely():
        return True
    if not force_dialog or (_startup_done and not _allow_runtime_dialogs):
        return False
    _osascript(
        'display dialog "fawai-avatar 默认可只读整盘（FAWAI_READ_ROOTS=/）。'
        '请在系统设置中为运行本程序的终端 / Cursor / Python 开启「完全磁盘访问」，'
        '否则部分配置目录读不到。" '
        'buttons {"打开设置", "稍后"} default button 1 with title "需要完全磁盘访问"'
    )
    _open_privacy_pane("Privacy_AllFiles")
    return full_disk_access_likely()


@dataclass(frozen=True)
class PermissionReport:
    accessibility: bool
    screen_recording: bool
    prompted: bool
    full_disk_access: bool = True
    read_roots: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "accessibility": self.accessibility,
            "screen_recording": self.screen_recording,
            "prompted": self.prompted,
            "full_disk_access": self.full_disk_access,
            "read_roots": list(self.read_roots),
        }

    def summary_line(self) -> str:
        ax = "已授权" if self.accessibility else "未授权"
        sc = "已授权" if self.screen_recording else "未授权"
        fda = "已授权/可用" if self.full_disk_access else "可能未授权"
        roots = ":".join(self.read_roots) if self.read_roots else "(仅 work_root)"
        return f"辅助功能={ax}  屏幕录制={sc}  完全磁盘={fda}  只读根={roots}"


def ensure_startup_permissions(
    *,
    accessibility: bool = True,
    screen_recording: bool = True,
    full_disk_access: bool = True,
    force: bool = False,
) -> PermissionReport:
    """进程启动时调用：一次性请求相关权限，避免任务中反复弹窗。

    同一进程默认只执行一次（``force=True`` 可再请求）。
    当 FAWAI_READ_ROOTS 默认含 ``/`` 时，会引导「完全磁盘访问」。
    """
    global _startup_done, _allow_runtime_dialogs

    from fawai_avatar.config import RuntimeConfig

    cfg = RuntimeConfig.from_env()
    roots = tuple(cfg.read_roots)

    if not is_macos():
        _startup_done = True
        _allow_runtime_dialogs = False
        return PermissionReport(
            accessibility=True,
            screen_recording=True,
            prompted=False,
            full_disk_access=True,
            read_roots=roots,
        )

    if _startup_done and not force:
        clear_permission_cache()
        return PermissionReport(
            accessibility=accessibility_trusted(),
            screen_recording=screen_recording_granted(),
            prompted=False,
            full_disk_access=full_disk_access_likely(),
            read_roots=roots,
        )

    prompted = False
    clear_permission_cache()

    if accessibility and not accessibility_trusted():
        prompt_accessibility(force_dialog=True)
        prompted = True
        clear_permission_cache()

    if screen_recording and not screen_recording_granted():
        prompt_screen_recording(force_dialog=True)
        prompted = True
        clear_permission_cache()

    want_fda = full_disk_access and any(
        str(r) in {"/", "/System", "/Volumes"} or str(r).startswith("/")
        for r in roots
    )
    if want_fda and not full_disk_access_likely():
        prompt_full_disk_access(force_dialog=True)
        prompted = True

    _startup_done = True
    # 启动已引导过：任务内不再弹自定义对话框（仍可读状态拒绝动作）
    _allow_runtime_dialogs = False

    return PermissionReport(
        accessibility=accessibility_trusted(),
        screen_recording=screen_recording_granted(),
        prompted=prompted,
        full_disk_access=full_disk_access_likely(),
        read_roots=roots,
    )
