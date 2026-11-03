# term:观察  doc:doc/功能/观察/说明.md
# term:应用前台  doc:doc/议题/多屏与全屏观察/结论.md
"""廉价前台窗摘要：不截屏；失败可降级，不崩闭环。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any


_DEFAULT_TIMEOUT_SEC = 2.0


def probe_frontmost(*, timeout_sec: float = _DEFAULT_TIMEOUT_SEC) -> dict[str, Any]:
    """探测当前前台 App / 窗口标题。

    返回字段：ok, app, window, reason, platform。失败时 ok=False，仍可进观察 meta。
    """
    try:
        from fawai_avatar.platform_info import is_headless, runtime_platform

        plat = runtime_platform()
        system = plat.system
        if is_headless():
            return {
                "ok": False,
                "app": None,
                "window": None,
                "reason": "headless",
                "platform": system,
            }
    except Exception as exc:  # noqa: BLE001
        system = sys.platform
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": f"platform_probe_failed:{exc}",
            "platform": system,
        }

    if system == "Darwin" or sys.platform == "darwin":
        return _probe_macos(timeout_sec=timeout_sec, platform=system)
    if system == "Linux" or sys.platform.startswith("linux"):
        return _probe_linux(timeout_sec=timeout_sec, platform=system)
    return {
        "ok": False,
        "app": None,
        "window": None,
        "reason": "unsupported_os",
        "platform": system,
    }


def format_frontmost_block(info: dict[str, Any] | None) -> str:
    """给人 / LLM 读的前台摘要块。"""
    data = info or {}
    ok = bool(data.get("ok"))
    app = str(data.get("app") or "").strip()
    window = str(data.get("window") or "").strip()
    reason = str(data.get("reason") or "").strip()
    platform = str(data.get("platform") or "").strip()
    lines = ["前台窗摘要（未截屏）："]
    if ok:
        lines.append(f"- App：{app or '（未知）'}")
        lines.append(f"- 窗口：{window or '（无标题或未取到）'}")
        if platform:
            lines.append(f"- 平台：{platform}")
        lines.append(
            "目标依赖某 App 可见 UI 时：须对照此前台态；需要画面证据再点选 obs_screen。"
        )
        return "\n".join(lines)
    reason_map = {
        "headless": "无界面，无法探测前台窗",
        "unsupported_os": "当前 OS 本切片不支持前台窗探测",
        "timeout": "探测超时",
        "xdotool_missing": "未安装 xdotool，无法探测前台窗",
        "empty": "未取到前台 App/窗口",
    }
    human = reason_map.get(reason, reason or "未知原因")
    lines.append(f"- 不可用：{human}")
    if platform:
        lines.append(f"- 平台：{platform}")
    return "\n".join(lines)


def attach_frontmost(
    obs: Any,
    *,
    probe: Any | None = None,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
) -> Any:
    """将前台摘要并入 Observation.summary 与 meta。"""
    from fawai_avatar.types import Observation

    if not isinstance(obs, Observation):
        return obs
    info = probe() if callable(probe) else probe_frontmost(timeout_sec=timeout_sec)
    if not isinstance(info, dict):
        info = {
            "ok": False,
            "app": None,
            "window": None,
            "reason": "invalid_probe",
            "platform": sys.platform,
        }
    block = format_frontmost_block(info)
    summary = (obs.summary or "").rstrip()
    if summary:
        summary = f"{summary}\n\n{block}"
    else:
        summary = block
    meta = dict(obs.meta or {})
    meta["frontmost_ok"] = bool(info.get("ok"))
    meta["frontmost_app"] = info.get("app")
    meta["frontmost_window"] = info.get("window")
    meta["frontmost_reason"] = info.get("reason")
    meta["frontmost_platform"] = info.get("platform")
    return Observation(
        summary=summary,
        image_path=obs.image_path,
        width=obs.width,
        height=obs.height,
        meta=meta,
    )


def _probe_macos(*, timeout_sec: float, platform: str) -> dict[str, Any]:
    script = (
        'tell application "System Events"\n'
        "  set frontApp to first application process whose frontmost is true\n"
        "  set appName to name of frontApp\n"
        '  set winTitle to ""\n'
        "  try\n"
        "    set winTitle to name of front window of frontApp\n"
        "  end try\n"
        "end tell\n"
        'return appName & linefeed & winTitle\n'
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
            "app": None,
            "window": None,
            "reason": "timeout",
            "platform": platform,
        }
    except OSError as exc:
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": f"osascript_error:{exc}",
            "platform": platform,
        }
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": f"osascript_failed:{err[:200]}",
            "platform": platform,
        }
    lines = (proc.stdout or "").splitlines()
    app = (lines[0].strip() if lines else "") or None
    window = (lines[1].strip() if len(lines) > 1 else "") or None
    if not app and not window:
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": "empty",
            "platform": platform,
        }
    return {
        "ok": True,
        "app": app,
        "window": window,
        "reason": None,
        "platform": platform,
    }


def _probe_linux(*, timeout_sec: float, platform: str) -> dict[str, Any]:
    if not shutil.which("xdotool"):
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": "xdotool_missing",
            "platform": platform,
        }
    try:
        name_proc = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        pid_proc = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": "timeout",
            "platform": platform,
        }
    except OSError as exc:
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": f"xdotool_error:{exc}",
            "platform": platform,
        }

    window = (name_proc.stdout or "").strip() or None
    if name_proc.returncode != 0 and not window:
        err = (name_proc.stderr or "").strip() or f"exit {name_proc.returncode}"
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": f"xdotool_failed:{err[:200]}",
            "platform": platform,
        }

    app: str | None = None
    pid_s = (pid_proc.stdout or "").strip()
    if pid_proc.returncode == 0 and pid_s.isdigit():
        app = _linux_comm(int(pid_s)) or f"pid:{pid_s}"

    if not app and not window:
        return {
            "ok": False,
            "app": None,
            "window": None,
            "reason": "empty",
            "platform": platform,
        }
    return {
        "ok": True,
        "app": app,
        "window": window,
        "reason": None,
        "platform": platform,
    }


def _linux_comm(pid: int) -> str | None:
    try:
        path = f"/proc/{pid}/comm"
        with open(path, encoding="utf-8", errors="replace") as fh:
            name = fh.read().strip()
        return name or None
    except OSError:
        return None
