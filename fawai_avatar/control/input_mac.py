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
            "未安装 pyautogui。桌面 GUI 操控请用当前解释器："
            f"{__import__('sys').executable} -m pip install -e '.[gui]'"
        ) from exc
    return pyautogui


def click(x: int | float, y: int | float) -> None:
    _pyautogui().click(x, y)


def double_click(x: int | float, y: int | float) -> None:
    _pyautogui().doubleClick(x, y)


def right_click(x: int | float, y: int | float) -> None:
    _pyautogui().rightClick(x, y)


def scroll(clicks: int, *, x: int | float | None = None, y: int | float | None = None) -> None:
    """clicks>0 向上，<0 向下。可选移到 (x,y) 再滚。"""
    pag = _pyautogui()
    if x is not None and y is not None:
        pag.moveTo(x, y)
    pag.scroll(int(clicks))


def press(key: str) -> None:
    _pyautogui().press(str(key))


def type_text(text: str) -> None:
    """键入文本。含非 ASCII 时走剪贴板粘贴（pyautogui.write 对中文不可靠）。"""
    s = str(text)
    pag = _pyautogui()
    if s.isascii():
        pag.write(s, interval=0.02)
        return
    try:
        import subprocess

        # macOS pbcopy；其它平台尽量用 pyperclip 若已随 pyautogui 可用
        proc = subprocess.run(
            ["pbcopy"],
            input=s.encode("utf-8"),
            check=False,
            capture_output=True,
        )
        if proc.returncode == 0:
            pag.hotkey("command", "v")
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        pag.write(s, interval=0.02)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法键入非 ASCII 文本：{exc}") from exc


def hotkey(keys: list[str]) -> None:
    _pyautogui().hotkey(*keys)
