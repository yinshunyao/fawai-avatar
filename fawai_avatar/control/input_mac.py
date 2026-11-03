# term:控制 tool:ui_act doc:doc/功能/控制/说明.md
"""GUI 键鼠输入（依赖可选 extras `[gui]` 的 pyautogui）。

文件名沿用 input_mac；实现跨平台，无 GUI / 未装依赖时由调用方先做 headless 闸门。
"""
from __future__ import annotations


def _pyautogui():
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError(
            "未安装 pyautogui。桌面 GUI 操控请：pip install -e '.[gui]'"
        ) from exc
    return pyautogui


def click(x: int | float, y: int | float) -> None:
    _pyautogui().click(x, y)


def type_text(text: str) -> None:
    _pyautogui().write(text, interval=0.02)


def hotkey(keys: list[str]) -> None:
    _pyautogui().hotkey(*keys)
