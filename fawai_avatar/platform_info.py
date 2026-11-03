# term:运行平台  doc:doc/功能/运行平台/说明.md
"""运行平台探测：OS / headless / GUI 可用性；供大脑提示与能力闸门共用。"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_flag(name: str) -> str | None:
    """返回规范化开关：1 / 0 / None（未设）。"""
    raw = _env(name)
    if raw is None:
        return None
    low = raw.lower()
    if low in {"1", "true", "yes", "on"}:
        return "1"
    if low in {"0", "false", "no", "off"}:
        return "0"
    return None


@dataclass(frozen=True)
class RuntimePlatform:
    """一次探测结果（可缓存）。"""

    system: str  # Darwin / Linux / Windows / …
    machine: str
    python: str
    headless: bool
    gui_available: bool
    capture_available: bool
    display: str | None
    headless_source: str  # env_force | auto_no_display | auto_desktop

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def os_family(self) -> str:
        if self.system == "Darwin":
            return "macos"
        if self.system == "Linux":
            return "linux"
        if self.system == "Windows":
            return "windows"
        return self.system.lower() or "unknown"


def _detect_display() -> str | None:
    for key in ("WAYLAND_DISPLAY", "DISPLAY"):
        val = _env(key)
        if val:
            return f"{key}={val}"
    # macOS 桌面会话通常无 DISPLAY；有登录图形会话则视为有显示
    if platform.system() == "Darwin":
        # SSH 无 Aqua 时仍可能无显示；用安全启发式：有 SSH_CONNECTION 且无 Aqua 会话变量
        if _env("SSH_CONNECTION") and not _env("TERM_PROGRAM") and not _env("SECURITYSESSIONID"):
            return None
        return "macos-session"
    return None


def _resolve_headless(display: str | None) -> tuple[bool, str]:
    forced = _env_flag("FAWAI_HEADLESS")
    if forced == "1":
        return True, "env_force"
    if forced == "0":
        return False, "env_force"
    if display is None:
        return True, "auto_no_display"
    return False, "auto_desktop"


@lru_cache(maxsize=1)
def runtime_platform() -> RuntimePlatform:
    """探测当前进程运行平台（读 env + 系统；结果缓存）。"""
    system = platform.system() or "Unknown"
    machine = platform.machine() or ""
    py = sys.version.split()[0]
    display = _detect_display()
    headless, source = _resolve_headless(display)
    gui_ok = not headless
    capture_ok = not headless
    # 显式关截屏时仍可有 GUI 键鼠；能力闸门另看 capture 配置
    return RuntimePlatform(
        system=system,
        machine=machine,
        python=py,
        headless=headless,
        gui_available=gui_ok,
        capture_available=capture_ok,
        display=display,
        headless_source=source,
    )


def clear_platform_cache() -> None:
    runtime_platform.cache_clear()


def is_headless() -> bool:
    return runtime_platform().headless


def gui_available() -> bool:
    return runtime_platform().gui_available


def shell_os_hint() -> str:
    """给大脑 / shell 失败提示用的 OS 命令口径。"""
    p = runtime_platform()
    if p.system == "Darwin":
        return (
            "当前操作系统：macOS（Darwin）。"
            "shell 须用 macOS/BSD 命令：查磁盘用 `diskutil list`，不要用 Linux 的 lsblk 当主方案；"
            "包管理优先 brew；路径符合 macOS。"
            "列文件树用 `ls` / `find PATH -type f -name '*.html' | head`；"
            "勿用 GNU 专用的 `find -printf`。"
        )
    if p.system == "Linux":
        return (
            "当前操作系统：Linux。"
            "shell 可用 lsblk、df、systemctl 等 Linux 命令；"
            "包管理按发行版用 apt/yum/dnf/pacman；不要用 diskutil / brew 当主方案。"
            "列文件树用 `ls` / `find PATH -type f -name '*.html' | head`；"
            "需要格式化输出时可用 GNU `find -printf`，但优先与 macOS 通用的 `-name` 写法。"
        )
    if p.system == "Windows":
        return (
            "当前操作系统：Windows。"
            "shell 优先 PowerShell/cmd；不要用 lsblk / diskutil。"
        )
    return f"当前操作系统：{p.system}。"


def platform_prompt_block() -> str:
    """注入大脑 system prompt：平台事实 + headless 约束。"""
    p = runtime_platform()
    mode = "无界面（headless）" if p.headless else "有桌面会话"
    lines = [
        shell_os_hint(),
        f"运行模式：{mode}（判定来源：{p.headless_source}）。",
        f"架构：{p.machine or 'unknown'}；Python {p.python}。",
    ]
    if p.display:
        lines.append(f"显示会话：{p.display}。")
    else:
        lines.append("未检测到可用显示会话（无 DISPLAY/WAYLAND_DISPLAY 或等价）。")
    if p.headless or not p.gui_available:
        lines.append(
            "本机无 GUI：禁止规划 click/type/hotkey；禁止依赖截屏（obs_screen）。"
            "完成任务请用 fs_* / code_* / git_* / version_* / skill_* / shell / wait / done。"
        )
    else:
        lines.append(
            "本机可尝试 GUI：仅当目标明确依赖屏幕 App UI 时才用 click/type/hotkey；"
            "文件/编码类目标仍优先后台工具。"
        )
    return "\n".join(lines)


def gateway_capture_default() -> bool:
    """网关默认是否允许截屏候选：显式 FAWAI_GATEWAY_CAPTURE 优先；否则 headless→False。"""
    forced = _env_flag("FAWAI_GATEWAY_CAPTURE")
    if forced == "1":
        return True
    if forced == "0":
        return False
    return not is_headless()
