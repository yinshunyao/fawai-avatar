# term:运行平台  doc:doc/功能/运行平台/说明.md
"""无界面 / 平台闸门冒烟：不依赖显示与 GUI extras。"""
from __future__ import annotations

import os
from pathlib import Path

from fawai_avatar.brain.capabilities import known_action_kinds, list_capabilities
from fawai_avatar.control.executor import Controller
from fawai_avatar.observe.methods import (
    candidate_observe_methods,
    choose_observe_methods,
    collect_observation,
)
from fawai_avatar.platform_info import clear_platform_cache, runtime_platform
from fawai_avatar.types import Action


def main() -> None:
    # 强制无界面，验证闸门（测完清缓存）
    os.environ["FAWAI_HEADLESS"] = "1"
    clear_platform_cache()
    plat = runtime_platform()
    assert plat.headless, plat
    assert not plat.gui_available

    kinds = known_action_kinds()
    assert "fs_write" in kinds
    assert "shell" in kinds
    assert "click" not in kinds
    assert "type" not in kinds
    assert "hotkey" not in kinds
    assert all(not c.action_kind.startswith("click") for c in list_capabilities())

    cands = candidate_observe_methods("看一下桌面计算器", capture=True)
    assert "obs_screen" not in cands
    assert "obs_fs" in cands or "none" in cands

    obs = collect_observation(["obs_screen"], goal="点击按钮", capture=True)
    assert "无法截屏" in obs.summary or "无界面" in obs.summary
    assert obs.meta.get("frontmost_ok") is False
    assert obs.meta.get("frontmost_reason") == "headless"
    assert "前台窗摘要" in obs.summary

    gui_default = choose_observe_methods("点击桌面计算器上的按钮", capture=True)
    assert gui_default == ["none"], gui_default

    ctrl = Controller(dry_run=False)
    denied = ctrl.execute(
        Action(kind="click", args={"x": 1, "y": 2}, needs_foreground=True),
        task_id="smoke-headless",
    )
    assert not denied.ok
    assert "无 GUI" in denied.message or "headless" in str(denied.data)

    # shell 在 headless 仍可用（dry 路径也行）；这里 dry_run 避免真跑
    ctrl_dry = Controller(dry_run=True)
    shell = ctrl_dry.execute(
        Action(kind="shell", args={"cmd": "echo ok"}),
        task_id="smoke-headless",
    )
    assert shell.ok

    # 后台文件路径不依赖 GUI
    from fawai_avatar.control.background import BackgroundTools

    root = Path(".").resolve()
    bg = BackgroundTools()
    listed = bg.execute(Action(kind="fs_glob", args={"pattern": "pyproject.toml"}))
    assert listed.ok, listed.message

    print("demo_platform ok", plat.to_dict())
    print("work_root", root)
    clear_platform_cache()


if __name__ == "__main__":
    main()
