# term:应用前台  doc:doc/功能/多屏与全屏观察/说明.md
"""按 App 名 / bundle 激活前台（常用于找回全屏 Space 上的窗）。

成功须以 frontmost 回证为准：脚本 activate 退出码 0 不够。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable


_VERIFY_SETTLE_SEC = 0.45
_VERIFY_RETRIES = 2


def focus_app(
    name: str,
    *,
    timeout_sec: float = 8.0,
    verify: bool = True,
    probe_frontmost: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """激活应用。返回 {ok, message, platform, app, ...}。

    verify=True（默认）时：activate 后探测 frontmost，名称对不上则 ok=false
   （典型：他 App 全屏占独立 Space，脚本仍返回成功）。
    """
    app = (name or "").strip()
    if not app:
        return {
            "ok": False,
            "message": "app_focus 需要 args.app（应用名或 bundle id）",
            "platform": sys.platform,
            "app": None,
        }
    try:
        from fawai_avatar.platform_info import is_headless, runtime_platform

        if is_headless():
            p = runtime_platform()
            return {
                "ok": False,
                "message": f"无界面，无法 app_focus（{p.system}/{p.headless_source}）",
                "platform": p.system,
                "app": app,
            }
        system = runtime_platform().system
    except Exception:  # noqa: BLE001
        system = "Darwin" if sys.platform == "darwin" else sys.platform

    if system == "Darwin" or sys.platform == "darwin":
        return _focus_macos(
            app,
            timeout_sec=timeout_sec,
            verify=verify,
            probe_frontmost=probe_frontmost,
        )
    if system == "Linux" or sys.platform.startswith("linux"):
        return _focus_linux(
            app,
            timeout_sec=timeout_sec,
            verify=verify,
            probe_frontmost=probe_frontmost,
        )
    return {
        "ok": False,
        "message": f"当前 OS（{system}）本切片不支持 app_focus",
        "platform": system,
        "app": app,
    }


def app_name_matches(target: str, frontmost: str | None) -> bool:
    """目标 App 名 / bundle 是否与前台进程名相符（宽松、大小写不敏感）。"""
    if not frontmost:
        return False
    a = _norm_app_token(target)
    b = _norm_app_token(frontmost)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    # bundle id → 末段（com.tencent.xinWeChat → xinwechat）
    if "." in (target or ""):
        tail = _norm_app_token((target or "").rsplit(".", 1)[-1])
        if tail and (tail == b or tail in b or b in tail):
            return True
    return False


def _norm_app_token(name: str) -> str:
    s = (name or "").strip().lower()
    if s.endswith(".app"):
        s = s[:-4]
    s = re.sub(r"[\s_\-]+", "", s)
    return s


def _focus_macos(
    app: str,
    *,
    timeout_sec: float,
    verify: bool,
    probe_frontmost: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    methods: list[str] = []
    last_err = ""

    # 1) AppleScript activate + reopen（尽量拉起已有窗）
    script = (
        f'tell application "{_escape_as(app)}"\n'
        "  activate\n"
        "  try\n"
        "    reopen\n"
        "  end try\n"
        "end tell"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "message": f"app_focus 超时：{app}",
            "platform": "Darwin",
            "app": app,
        }
    except OSError as exc:
        return {
            "ok": False,
            "message": f"app_focus osascript 失败：{exc}",
            "platform": "Darwin",
            "app": app,
        }
    if proc.returncode == 0:
        methods.append("osascript")
        if not verify:
            return {
                "ok": True,
                "message": f"activated {app}",
                "platform": "Darwin",
                "app": app,
                "method": "osascript",
            }
        checked = _verify_frontmost(
            app,
            platform="Darwin",
            method="osascript",
            probe_frontmost=probe_frontmost,
        )
        if checked.get("ok"):
            return checked
        last_err = str(checked.get("message") or "")
    else:
        last_err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"

    # 2) System Events 强制 frontmost（应用名与进程名常一致）
    se_script = (
        'tell application "System Events"\n'
        f'  set frontmost of first process whose name is "{_escape_as(app)}" to true\n'
        "end tell"
    )
    try:
        proc_se = subprocess.run(
            ["osascript", "-e", se_script],
            capture_output=True,
            text=True,
            timeout=min(timeout_sec, 5.0),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        proc_se = None
    if proc_se is not None and proc_se.returncode == 0:
        methods.append("system-events")
        if verify:
            checked = _verify_frontmost(
                app,
                platform="Darwin",
                method="+".join(methods),
                probe_frontmost=probe_frontmost,
            )
            if checked.get("ok"):
                return checked
            last_err = str(checked.get("message") or last_err)

    # 3) open -a（勿用 -g：会故意不抢前台）
    try:
        proc2 = subprocess.run(
            ["open", "-a", app],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        err = last_err or str(exc)
        return {
            "ok": False,
            "message": f"app_focus 失败：{err}",
            "platform": "Darwin",
            "app": app,
            "methods_tried": methods,
        }
    if proc2.returncode == 0:
        methods.append("open-a")
        if not verify:
            return {
                "ok": True,
                "message": f"opened {app}",
                "platform": "Darwin",
                "app": app,
                "method": "open-a",
            }
        checked = _verify_frontmost(
            app,
            platform="Darwin",
            method="+".join(methods),
            probe_frontmost=probe_frontmost,
        )
        if checked.get("ok"):
            return checked
        # 最终仍非目标前台 → 诚实失败
        return checked

    err = (proc2.stderr or last_err or proc2.stdout or "").strip()
    return {
        "ok": False,
        "message": f"app_focus 失败：{err or f'exit {proc2.returncode}'}",
        "platform": "Darwin",
        "app": app,
        "methods_tried": methods,
    }


def _focus_linux(
    app: str,
    *,
    timeout_sec: float,
    verify: bool,
    probe_frontmost: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    methods: list[str] = []
    # 尽力：wmctrl -a / xdotool search --name
    if shutil.which("wmctrl"):
        try:
            proc = subprocess.run(
                ["wmctrl", "-a", app],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {
                "ok": False,
                "message": f"app_focus wmctrl 失败：{exc}",
                "platform": "Linux",
                "app": app,
            }
        if proc.returncode == 0:
            methods.append("wmctrl")
            if not verify:
                return {
                    "ok": True,
                    "message": f"wmctrl activated {app}",
                    "platform": "Linux",
                    "app": app,
                    "method": "wmctrl",
                }
            checked = _verify_frontmost(
                app,
                platform="Linux",
                method="wmctrl",
                probe_frontmost=probe_frontmost,
            )
            if checked.get("ok"):
                return checked
    if shutil.which("xdotool"):
        try:
            search = subprocess.run(
                ["xdotool", "search", "--name", app],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            wid = (search.stdout or "").strip().splitlines()
            if search.returncode == 0 and wid:
                act = subprocess.run(
                    ["xdotool", "windowactivate", wid[0]],
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    check=False,
                )
                if act.returncode == 0:
                    methods.append("xdotool")
                    if not verify:
                        return {
                            "ok": True,
                            "message": f"xdotool activated {app}",
                            "platform": "Linux",
                            "app": app,
                            "method": "xdotool",
                        }
                    return _verify_frontmost(
                        app,
                        platform="Linux",
                        method="+".join(methods) or "xdotool",
                        probe_frontmost=probe_frontmost,
                    )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {
                "ok": False,
                "message": f"app_focus xdotool 失败：{exc}",
                "platform": "Linux",
                "app": app,
            }
    return {
        "ok": False,
        "message": (
            f"app_focus 失败：未找到窗口 {app!r}（需 wmctrl 或 xdotool；"
            "Linux Workspace/全屏为弱支持）"
        ),
        "platform": "Linux",
        "app": app,
        "methods_tried": methods,
    }


def _verify_frontmost(
    app: str,
    *,
    platform: str,
    method: str,
    probe_frontmost: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    """activate 后探测前台；对不上则失败（疑全屏 Space）。"""
    probe = probe_frontmost
    if probe is None:
        from fawai_avatar.observe.frontmost import probe_frontmost as _probe

        probe = _probe

    front_app: str | None = None
    front_window: str | None = None
    reason: str | None = None
    for attempt in range(_VERIFY_RETRIES):
        if attempt == 0 or _VERIFY_SETTLE_SEC > 0:
            time.sleep(_VERIFY_SETTLE_SEC if attempt == 0 else _VERIFY_SETTLE_SEC * 0.5)
        try:
            info = probe(timeout_sec=2.0)
        except TypeError:
            info = probe()
        except Exception as exc:  # noqa: BLE001
            reason = f"probe_error:{exc}"
            continue
        if not isinstance(info, dict):
            reason = "invalid_probe"
            continue
        raw_app = info.get("app")
        front_app = str(raw_app).strip() if raw_app is not None else None
        if not front_app:
            front_app = None
        raw_win = info.get("window")
        front_window = str(raw_win).strip() if raw_win is not None else None
        if not front_window:
            front_window = None
        if not info.get("ok"):
            reason = str(info.get("reason") or "probe_failed")
            continue
        if app_name_matches(app, front_app):
            return {
                "ok": True,
                "message": f"activated {app} (frontmost={front_app})",
                "platform": platform,
                "app": app,
                "method": method,
                "frontmost_app": front_app,
                "frontmost_window": front_window,
                "verified": True,
            }
        reason = "frontmost_mismatch"

    shown = front_app or "（未知）"
    return {
        "ok": False,
        "message": (
            f"app_focus：脚本已尝试激活 {app!r}，但前台仍是 {shown}"
            f"（method={method}；疑全屏 Space/未切到目标桌面；"
            "可改用 hotkey 切 Space 后再 app_focus，或请用户退出他 App 全屏）"
        ),
        "platform": platform,
        "app": app,
        "method": method,
        "frontmost_app": front_app,
        "frontmost_window": front_window,
        "verified": False,
        "reason": reason or "frontmost_mismatch",
    }


def _escape_as(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
