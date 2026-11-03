# term:控制 tool:ui_act doc:doc/功能/控制/说明.md
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.control import input_mac
from fawai_avatar.control.background import BACKGROUND_KINDS, BackgroundTools
from fawai_avatar.types import Action, ExecResult

HoldingForegroundFn = Callable[[str | None], bool]
AllowsWritesFn = Callable[[str | None, list[str]], bool]

_GUI_KINDS = frozenset({"click", "type", "hotkey"})


class Controller:
    def __init__(
        self,
        *,
        dry_run: bool | None = None,
        supervisor: Any | None = None,
        holding_foreground: HoldingForegroundFn | None = None,
        allows_writes: AllowsWritesFn | None = None,
        background: BackgroundTools | None = None,
        system_log: Any | None = None,
    ) -> None:
        cfg = RuntimeConfig.from_env()
        self.dry_run = cfg.dry_run if dry_run is None else dry_run
        self._supervisor = supervisor
        self._holding_foreground = holding_foreground or _resolve_fn(
            supervisor, "holding_foreground"
        )
        self._allows_writes = allows_writes or _resolve_fn(supervisor, "allows_writes")
        self._background = background or BackgroundTools()
        self.system_log = system_log

    def execute(self, action: Action, *, task_id: str | None = None) -> ExecResult:
        kind = (action.kind or "").strip().lower()

        # 后台文件/版本：默认补 write_resources，再过闸门；不受 GUI dry_run 影响
        if kind in BACKGROUND_KINDS:
            action = _ensure_write_resources(action)
            gate = self._check_gates(action, task_id)
            if gate is not None:
                self._log_system(action, gate, task_id=task_id)
                return gate
            result = self._background.execute(action)
            self._log_system(action, result, task_id=task_id)
            return result

        gate = self._check_gates(action, task_id)
        if gate is not None:
            self._log_system(action, gate, task_id=task_id)
            return gate

        if self.dry_run and kind in _GUI_KINDS:
            result = ExecResult(ok=True, message=f"dry_run: {kind} {action.args}")
            self._log_system(action, result, task_id=task_id)
            return result
        if self.dry_run and kind == "shell":
            result = ExecResult(ok=True, message=f"dry_run: {kind} {action.args}")
            self._log_system(action, result, task_id=task_id)
            return result

        try:
            if kind == "click":
                result = self._exec_click(action)
            elif kind == "type":
                result = self._exec_type(action)
            elif kind == "hotkey":
                result = self._exec_hotkey(action)
            elif kind == "shell":
                result = self._exec_shell(action)
            elif kind == "wait":
                result = self._exec_wait(action)
            elif kind == "done":
                result = ExecResult(ok=True, message="done", data={"kind": "done"})
            else:
                result = ExecResult(ok=False, message=f"unknown action kind: {kind!r}")
        except Exception as exc:  # noqa: BLE001 — surface to caller
            result = ExecResult(ok=False, message=str(exc))
        self._log_system(action, result, task_id=task_id)
        return result

    def _log_system(
        self, action: Action, result: ExecResult, *, task_id: str | None
    ) -> None:
        store = self.system_log
        if store is None:
            return
        log_action = getattr(store, "log_action", None)
        if not callable(log_action):
            return
        try:
            log_action(action, result, task_id=task_id, actor="runtime")
        except Exception:  # noqa: BLE001 — 日志失败不影响执行
            return

    def _check_gates(self, action: Action, task_id: str | None) -> ExecResult | None:
        if action.needs_foreground and self._holding_foreground is not None:
            if not self._holding_foreground(task_id):
                return ExecResult(
                    ok=False,
                    message="rejected: foreground lease not held",
                    data={"task_id": task_id},
                )
        if action.write_resources and self._allows_writes is not None:
            if not self._allows_writes(task_id, list(action.write_resources)):
                return ExecResult(
                    ok=False,
                    message="rejected: write resources not allowed",
                    data={"task_id": task_id, "resources": list(action.write_resources)},
                )
        return None

    def _ensure_gui(self) -> ExecResult | None:
        from fawai_avatar.platform_info import gui_available, runtime_platform

        if not gui_available():
            p = runtime_platform()
            return ExecResult(
                ok=False,
                message=(
                    f"当前运行平台无 GUI（{p.system}/{p.headless_source}），"
                    "无法执行 click/type/hotkey；请改用 fs_* / shell 等后台能力"
                ),
                data={"permission": "headless", "platform": p.to_dict()},
            )
        from fawai_avatar.control.permissions_mac import ensure_gui_permissions

        ok, message = ensure_gui_permissions()
        if ok:
            return None
        return ExecResult(ok=False, message=message, data={"permission": "accessibility"})

    def _exec_click(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        args = action.args
        x = args.get("x")
        y = args.get("y")
        if x is None or y is None:
            return ExecResult(ok=False, message="click requires x and y")
        input_mac.click(float(x), float(y))
        return ExecResult(ok=True, message=f"click ({x}, {y})", data={"x": x, "y": y})

    def _exec_type(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        text = action.args.get("text")
        if text is None:
            return ExecResult(ok=False, message="type requires text")
        input_mac.type_text(str(text))
        return ExecResult(ok=True, message=f"type {len(str(text))} chars")

    def _exec_hotkey(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        key_list = normalize_hotkey_keys(action.args)
        if not key_list:
            return ExecResult(
                ok=False,
                message=(
                    "hotkey requires args.keys: list[str]，"
                    "例 [\"cmd\",\"tab\"]；也兼容 key/combo=\"cmd+tab\""
                ),
            )
        input_mac.hotkey(key_list)
        return ExecResult(ok=True, message=f"hotkey {'+'.join(key_list)}", data={"keys": key_list})

    def _exec_shell(self, action: Action) -> ExecResult:
        cmd = _shell_cmd(action.args)
        if not cmd:
            return ExecResult(
                ok=False,
                message="shell requires args.cmd（或 command / argv）",
                data={"args": dict(action.args)},
            )
        cwd = action.args.get("cwd")
        proc = subprocess.run(
            cmd if isinstance(cmd, list) else str(cmd),
            shell=not isinstance(cmd, list),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        cmd_text = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
        if ok:
            message = out or f"exit {proc.returncode}"
        else:
            message = err or out or f"exit {proc.returncode}"
            if "command not found" in message.lower() or proc.returncode == 127:
                from fawai_avatar.platform_info import runtime_platform

                p = runtime_platform()
                if p.system == "Darwin":
                    tip = "macOS 请用 diskutil 等，不要用 lsblk"
                elif p.system == "Linux":
                    tip = "Linux 请用 lsblk/df 等，不要用 diskutil"
                else:
                    tip = f"请按当前操作系统（{p.system}）选用命令"
                message = f"{message}（{tip}）"
            elif _looks_like_which_probe(cmd_text) and out:
                message = (
                    f"{out}\n（提示：which/command -v 任一参数不存在即非零退出；"
                    "探测多工具请用 `command -v a b c || true`，勿因缺一个判整步失败）"
                )
            elif "AppleEvent" in message or "-1712" in message:
                message = (
                    f"{message}（提示：osascript 激活 GUI 易超时；"
                    "优先 hotkey/click，或确认目标 App 已响应后再试）"
                )
        return ExecResult(
            ok=ok,
            message=message,
            data={
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "cmd": cmd_text,
            },
        )

    def _exec_wait(self, action: Action) -> ExecResult:
        seconds = float(action.args.get("seconds", 0))
        if seconds > 0:
            time.sleep(seconds)
        return ExecResult(ok=True, message=f"wait {seconds}s", data={"seconds": seconds})


def normalize_hotkey_keys(args: dict[str, Any]) -> list[str]:
    """规范化热键参数：keys 列表为主；兼容 key/combo/hotkey 与 'cmd+tab' 字符串。"""
    raw = args.get("keys")
    if raw is None:
        raw = args.get("key")
    if raw is None:
        raw = args.get("combo")
    if raw is None:
        raw = args.get("hotkey")
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        if len(parts) == 1 and ("+" in parts[0] or " " in parts[0]):
            return _split_hotkey_combo(parts[0])
        return parts
    if isinstance(raw, str) and raw.strip():
        return _split_hotkey_combo(raw)
    return []


def _split_hotkey_combo(combo: str) -> list[str]:
    text = combo.strip()
    if not text:
        return []
    if "+" in text:
        return [p.strip() for p in text.split("+") if p.strip()]
    if " " in text:
        return [p.strip() for p in text.split() if p.strip()]
    return [text]


def _looks_like_which_probe(cmd: str) -> bool:
    text = (cmd or "").strip()
    return text.startswith("which ") or text.startswith("command -v") or text.startswith(
        "command -V"
    )


def _shell_cmd(args: dict[str, Any]) -> str | list[str] | None:
    """兼容大脑常见字段：cmd / command / argv。"""
    if args.get("cmd"):
        return str(args["cmd"])
    if args.get("command"):
        return str(args["command"])
    argv = args.get("argv")
    if isinstance(argv, list) and argv:
        return [str(x) for x in argv]
    return None


def _ensure_write_resources(action: Action) -> Action:
    """写类后台动作自动申报路径，便于总管 resource_lease。"""
    kind = (action.kind or "").strip().lower()
    args = action.args or {}
    resources = list(action.write_resources)
    if kind in {"fs_write", "fs_replace", "fs_patch", "diff_confirm"} and args.get("path"):
        path = str(args["path"])
        if path not in resources:
            resources.append(path)
    if kind in {"version_rollback", "git_commit", "fs_read_many", "code_check"}:
        for p in args.get("paths") or []:
            sp = str(p)
            if sp not in resources and kind in {"version_rollback", "git_commit"}:
                resources.append(sp)
    if resources == action.write_resources:
        return action
    return Action(
        kind=action.kind,
        args=dict(args),
        needs_foreground=False,
        write_resources=resources,
    )


def _resolve_fn(supervisor: Any | None, name: str) -> Callable[..., bool] | None:
    if supervisor is None:
        return None
    attr = getattr(supervisor, name, None)
    if callable(attr):
        return attr  # type: ignore[return-value]
    return None


if __name__ == "__main__":
    DRY_RUN = True
    ctrl = Controller(dry_run=DRY_RUN)
    samples = [
        Action(kind="click", args={"x": 10, "y": 20}, needs_foreground=True),
        Action(kind="wait", args={"seconds": 0.1}),
        Action(kind="done", args={}),
    ]
    for act in samples:
        result = ctrl.execute(act, task_id="smoke")
        print(act.kind, result.ok, result.message)
