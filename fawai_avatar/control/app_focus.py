# term:应用前台  doc:doc/功能/多屏与全屏观察/说明.md
"""按 App 名 / bundle 激活前台（常用于找回全屏 Space 上的窗）。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any


def focus_app(
    name: str,
    *,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    """激活应用。返回 {ok, message, platform, app}。"""
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
        return _focus_macos(app, timeout_sec=timeout_sec)
    if system == "Linux" or sys.platform.startswith("linux"):
        return _focus_linux(app, timeout_sec=timeout_sec)
    return {
        "ok": False,
        "message": f"当前 OS（{system}）本切片不支持 app_focus",
        "platform": system,
        "app": app,
    }


def _focus_macos(app: str, *, timeout_sec: float) -> dict[str, Any]:
    # 优先 tell application activate；失败再 open -a
    script = f'tell application "{_escape_as(app)}" to activate'
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
        return {
            "ok": True,
            "message": f"activated {app}",
            "platform": "Darwin",
            "app": app,
            "method": "osascript",
        }
    # 回落 open -a
    try:
        proc2 = subprocess.run(
            ["open", "-a", app],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        err = (proc.stderr or proc.stdout or "").strip()
        return {
            "ok": False,
            "message": f"app_focus 失败：{err or exc}",
            "platform": "Darwin",
            "app": app,
        }
    if proc2.returncode == 0:
        return {
            "ok": True,
            "message": f"opened {app}",
            "platform": "Darwin",
            "app": app,
            "method": "open-a",
        }
    err = (proc2.stderr or proc.stderr or proc2.stdout or "").strip()
    return {
        "ok": False,
        "message": f"app_focus 失败：{err or f'exit {proc2.returncode}'}",
        "platform": "Darwin",
        "app": app,
    }


def _focus_linux(app: str, *, timeout_sec: float) -> dict[str, Any]:
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
            return {
                "ok": True,
                "message": f"wmctrl activated {app}",
                "platform": "Linux",
                "app": app,
                "method": "wmctrl",
            }
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
                    return {
                        "ok": True,
                        "message": f"xdotool activated {app}",
                        "platform": "Linux",
                        "app": app,
                        "method": "xdotool",
                    }
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
    }


def _escape_as(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
