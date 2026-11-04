# term:控制 tool:ui_act doc:doc/功能/控制/说明.md
# term:浏览器操作 tool:browser_* doc:doc/功能/浏览器操作/说明.md
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.control import input_mac
from fawai_avatar.control.background import BACKGROUND_KINDS, BackgroundTools
from fawai_avatar.control.browser import BROWSER_KINDS, BrowserTools
from fawai_avatar.types import Action, ExecResult

HoldingForegroundFn = Callable[[str | None], bool]
AllowsWritesFn = Callable[[str | None, list[str]], bool]

_GUI_KINDS = frozenset(
    {
        "click",
        "type",
        "hotkey",
        "app_focus",
        "ui_click",
        "ui_type",
        "ui_verify",
        "double_click",
        "right_click",
        "scroll",
        "press",
    }
)


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
        self._browser = BrowserTools()
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

        # 浏览器：不占 foreground_lease；无头可用；受 FAWAI_DRY_RUN
        if kind in BROWSER_KINDS:
            gate = self._check_gates(action, task_id)
            if gate is not None:
                self._log_system(action, gate, task_id=task_id)
                return gate
            if self.dry_run:
                result = ExecResult(ok=True, message=f"dry_run: {kind} {action.args}")
                self._log_system(action, result, task_id=task_id)
                return result
            result = self._browser.execute(action)
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
            elif kind == "double_click":
                result = self._exec_double_click(action)
            elif kind == "right_click":
                result = self._exec_right_click(action)
            elif kind == "scroll":
                result = self._exec_scroll(action)
            elif kind == "press":
                result = self._exec_press(action)
            elif kind == "type":
                result = self._exec_type(action)
            elif kind == "hotkey":
                result = self._exec_hotkey(action)
            elif kind == "app_focus":
                result = self._exec_app_focus(action)
            elif kind == "ui_click":
                result = self._exec_ui_click(action)
            elif kind == "ui_type":
                result = self._exec_ui_type(action)
            elif kind == "ui_verify":
                result = self._exec_ui_verify(action)
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
                    "无法执行 click/type/hotkey/app_focus；请改用 fs_* / shell 等后台能力"
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
        return self._exec_point_click(action, mode="click")

    def _exec_double_click(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        return self._exec_point_click(action, mode="double_click")

    def _exec_right_click(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        return self._exec_point_click(action, mode="right_click")

    def _exec_point_click(self, action: Action, *, mode: str) -> ExecResult:
        args = dict(action.args or {})
        x = args.get("x")
        y = args.get("y")
        if x is None or y is None:
            return ExecResult(ok=False, message=f"{mode} requires x and y")
        local_x, local_y = float(x), float(y)
        ok_img, img_err = validate_image_click_xy(local_x, local_y, args)
        if not ok_img:
            return ExecResult(
                ok=False,
                message=img_err,
                data={"x": local_x, "y": local_y, "blocked": "click_image_bounds"},
            )
        gx, gy, meta = resolve_click_global(local_x, local_y, args)
        ok_pt, pt_err = validate_global_click_point(gx, gy)
        if not ok_pt:
            return ExecResult(
                ok=False,
                message=pt_err,
                data={"x": gx, "y": gy, **meta, "blocked": "click_global_bounds"},
            )
        if mode == "double_click":
            input_mac.double_click(gx, gy)
        elif mode == "right_click":
            input_mac.right_click(gx, gy)
        else:
            input_mac.click(gx, gy)
        from_img = (
            f" from image ({local_x}, {local_y})"
            if meta.get("coord_space") == "image"
            else ""
        )
        return ExecResult(
            ok=True,
            message=f"{mode} ({gx}, {gy}){from_img}",
            data={"x": gx, "y": gy, **meta},
        )

    def _exec_scroll(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        args = action.args or {}
        clicks = args.get("clicks", args.get("dy", args.get("amount")))
        if clicks is None:
            return ExecResult(ok=False, message="scroll requires clicks（>0 上 / <0 下）")
        x, y = args.get("x"), args.get("y")
        input_mac.scroll(
            int(clicks),
            x=float(x) if x is not None else None,
            y=float(y) if y is not None else None,
        )
        return ExecResult(
            ok=True,
            message=f"scroll {clicks}",
            data={"clicks": int(clicks), "x": x, "y": y},
        )

    def _exec_press(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        args = action.args or {}
        key = args.get("key") or args.get("name")
        if not key:
            return ExecResult(ok=False, message="press requires args.key")
        input_mac.press(str(key))
        return ExecResult(ok=True, message=f"press {key}", data={"key": str(key)})

    def _resolve_ui_ref(self, args: dict[str, Any]) -> tuple[dict[str, Any] | None, ExecResult | None]:
        ref = str(args.get("ref") or args.get("id") or "").strip()
        if not ref:
            return None, ExecResult(
                ok=False,
                message="ui_* requires args.ref：须为最近 obs_ax 表中的编号（如 \"3\"），禁止臆造 chat-input 等 id",
            )
        from fawai_avatar.observe.ax_tree import format_available_refs, resolve_ax_ref

        node, how = resolve_ax_ref(ref)
        if node is not None:
            # 把解析结果挂到 args 便于日志（不改 Action 本体）
            if how == "fuzzy":
                node = dict(node)
                node["_resolved_how"] = "fuzzy"
                node["_requested_ref"] = ref
            return node, None

        avail = format_available_refs()
        looks_invented = not ref.isdigit()
        tip = (
            "ref 须抄 obs_ax 摘要里的编号，禁止用 DOM/无障碍臆造名（如 chat-input-field）。"
            if looks_invented
            else "该编号不在缓存中。"
        )
        return None, ExecResult(
            ok=False,
            message=f"未知 ref={ref!r}；{tip} 请先 obs_ax 再 ui_click/ui_type。{avail}",
            data={"ref": ref, "available_hint": avail},
        )

    def _exec_ui_click(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        args = action.args or {}
        node, err = self._resolve_ui_ref(args)
        if err is not None:
            return err
        assert node is not None
        ok_ax, ax_err, gx, gy = resolve_ax_click_center(node)
        if not ok_ax:
            return ExecResult(
                ok=False,
                message=ax_err,
                data={
                    "ref": node.get("ref"),
                    "x": gx,
                    "y": gy,
                    "role": node.get("role"),
                    "blocked": "ax_bounds_invalid",
                },
            )
        button = str(args.get("button") or "left").lower()
        if button in {"right", "secondary"}:
            input_mac.right_click(gx, gy)
        elif button in {"double", "double_click"}:
            input_mac.double_click(gx, gy)
        else:
            input_mac.click(gx, gy)
        how = str(node.get("_resolved_how") or "exact")
        req = node.get("_requested_ref")
        msg = f"ui_click ref={node['ref']} @ ({gx:.0f},{gy:.0f})"
        if how == "fuzzy" and req:
            msg += f"（由 {req!r} 模糊匹配）"
        return ExecResult(
            ok=True,
            message=msg,
            data={
                "ref": node["ref"],
                "x": gx,
                "y": gy,
                "role": node.get("role"),
                "resolved_how": how,
                "requested_ref": req,
            },
        )

    def _exec_ui_type(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        args = action.args or {}
        text = args.get("text")
        if text is None:
            return ExecResult(ok=False, message="ui_type requires text")
        node, err = self._resolve_ui_ref(args)
        if err is not None:
            # 允许无 ref：仅向前台键入（与 type 等价）
            if not str(args.get("ref") or "").strip():
                input_mac.type_text(str(text))
                return ExecResult(
                    ok=True,
                    message=f"ui_type {len(str(text))} chars（无 ref，前台键入）",
                    data={"text_len": len(str(text)), "focused": False},
                )
            return err
        assert node is not None
        ok_ax, ax_err, gx, gy = resolve_ax_click_center(node)
        if not ok_ax:
            return ExecResult(
                ok=False,
                message=ax_err,
                data={
                    "ref": node.get("ref"),
                    "x": gx,
                    "y": gy,
                    "blocked": "ax_bounds_invalid",
                },
            )
        input_mac.click(gx, gy)
        input_mac.type_text(str(text))
        return ExecResult(
            ok=True,
            message=f"ui_type ref={node['ref']} {len(str(text))} chars",
            data={
                "ref": node["ref"],
                "text": str(text),
                "text_len": len(str(text)),
                "x": gx,
                "y": gy,
            },
        )

    def _exec_ui_verify(self, action: Action) -> ExecResult:
        from fawai_avatar.observe.verify import ui_verify_method_supported, verify_ui_ax

        args = action.args or {}
        method = args.get("method")
        method_s = str(method).strip().lower() if method is not None else ""
        if not ui_verify_method_supported(method_s):
            return ExecResult(
                ok=False,
                message=(
                    f"ui_verify 不支持 method={method_s!r}（仅 ax）。"
                    "本产品不做 OCR/VLM 界面回证；请省略 method 或 method=ax，并先 obs_ax。"
                    "AX 不可用时用截屏目视后直接 success/failed，禁止再传 method=ocr。"
                ),
                data={"method": method_s, "supported": ["ax"]},
            )
        contains = args.get("contains") or args.get("text") or args.get("expect")
        return verify_ui_ax(contains=str(contains) if contains is not None else None)

    def _exec_app_focus(self, action: Action) -> ExecResult:
        denied = self._ensure_gui()
        if denied is not None:
            return denied
        from fawai_avatar.control.app_focus import focus_app

        args = action.args or {}
        name = (
            args.get("app")
            or args.get("name")
            or args.get("bundle")
            or args.get("application")
        )
        info = focus_app(str(name or ""))
        return ExecResult(
            ok=bool(info.get("ok")),
            message=str(info.get("message") or ""),
            data=dict(info),
        )

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
        from fawai_avatar.control.shell_guard import shell_local_fs_bypass_error

        cfg = RuntimeConfig.from_env()
        cmd_text = cmd if isinstance(cmd, str) else " ".join(str(x) for x in cmd)
        bypass = shell_local_fs_bypass_error(
            cmd_text,
            work_root=cfg.work_root,
            read_roots=list(cfg.read_roots),
        )
        if bypass:
            return ExecResult(
                ok=False,
                message=bypass,
                data={"cmd": cmd_text, "blocked": "shell_local_fs_bypass"},
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


def resolve_click_global(
    x: float,
    y: float,
    args: dict[str, Any],
) -> tuple[float, float, dict[str, Any]]:
    """图内坐标 + origin → 虚拟桌面全局坐标。

    约定：默认 coord_space=image（相对最近截图左上角）；
    coord_space=global 则原样使用。origin 可来自 args.origin_x/y。
    若截图像素与采集区逻辑尺寸不一致（Retina），用 coord_scale_* 把图内像素压到逻辑坐标。
    """
    space = str(args.get("coord_space") or args.get("coords") or "image").strip().lower()
    meta: dict[str, Any] = {"coord_space": space, "image_x": x, "image_y": y}
    if space in {"global", "screen", "absolute", "desktop"}:
        meta["coord_space"] = "global"
        return x, y, meta
    ox = args.get("origin_x")
    oy = args.get("origin_y")
    if ox is None and oy is None:
        # 无 origin：单屏时常等价全局；仍按 image 语义（origin=0）
        ox, oy = 0, 0
        meta["origin_assumed_zero"] = True
    ox_f, oy_f = float(ox or 0), float(oy or 0)
    sx, sy = _coord_scales(args)
    lx, ly = x * sx, y * sy
    meta["origin_x"] = ox_f
    meta["origin_y"] = oy_f
    meta["coord_space"] = "image"
    meta["coord_scale_x"] = sx
    meta["coord_scale_y"] = sy
    meta["logical_x"] = lx
    meta["logical_y"] = ly
    return lx + ox_f, ly + oy_f, meta


def _coord_scales(args: dict[str, Any]) -> tuple[float, float]:
    sx = args.get("coord_scale_x")
    sy = args.get("coord_scale_y")
    if sx is not None and sy is not None:
        try:
            return max(float(sx), 1e-6), max(float(sy), 1e-6)
        except (TypeError, ValueError):
            pass
    # 由 PNG 尺寸与采集区逻辑尺寸推导
    iw = args.get("image_width")
    ih = args.get("image_height")
    cw = args.get("capture_width")
    ch = args.get("capture_height")
    try:
        if iw and ih and cw and ch and float(iw) > 0 and float(ih) > 0:
            return float(cw) / float(iw), float(ch) / float(ih)
    except (TypeError, ValueError):
        pass
    return 1.0, 1.0


def validate_image_click_xy(
    x: float, y: float, args: dict[str, Any], *, margin: float = 8.0
) -> tuple[bool, str]:
    """图内坐标须落在最近截图像素范围内（允许少量越界容差）。"""
    space = str(args.get("coord_space") or args.get("coords") or "image").strip().lower()
    if space in {"global", "screen", "absolute", "desktop"}:
        return True, ""
    iw = args.get("image_width")
    ih = args.get("image_height")
    try:
        if iw is None or ih is None:
            return True, ""
        w, h = float(iw), float(ih)
    except (TypeError, ValueError):
        return True, ""
    if w <= 0 or h <= 0:
        return True, ""
    if x < -margin or y < -margin or x > w + margin or y > h + margin:
        return (
            False,
            f"click 图内坐标 ({x:.0f},{y:.0f}) 超出最近截图 {w:.0f}x{h:.0f}；"
            "请按画面尺寸重估，或改 press/hotkey / obs_ax+ui_click",
        )
    return True, ""


def validate_global_click_point(
    gx: float, gy: float, *, margin: float = 64.0
) -> tuple[bool, str]:
    """全局坐标须落在某块显示器内；荒谬值直接拒绝（禁止假成功）。"""
    if abs(gx) > 100_000 or abs(gy) > 100_000:
        return (
            False,
            f"click 全局坐标 ({gx:.0f},{gy:.0f}) 荒谬；"
            "请改 obs_screen 图内坐标或 press/hotkey，勿盲点",
        )
    try:
        from fawai_avatar.observe.displays import list_displays

        displays = list_displays()
    except Exception:  # noqa: BLE001
        displays = []
    if not displays:
        return True, ""
    for d in displays:
        b = d.get("bounds") or {}
        try:
            left = float(b.get("left") or 0)
            top = float(b.get("top") or 0)
            width = float(b.get("width") or 0)
            height = float(b.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        if (
            left - margin <= gx <= left + width + margin
            and top - margin <= gy <= top + height + margin
        ):
            return True, ""
    return (
        False,
        f"click 全局坐标 ({gx:.0f},{gy:.0f}) 不在任何显示器范围内；"
        "请核对 screen_target/origin，或改用 press/hotkey",
    )


def resolve_ax_click_center(
    node: dict[str, Any],
) -> tuple[bool, str, float, float]:
    """由 AX 节点算点击中心；bounds 无效则拒绝（Electron 等常给出荒谬坐标）。"""
    try:
        x = float(node.get("x") or 0)
        y = float(node.get("y") or 0)
        w = float(node.get("w") or 0)
        h = float(node.get("h") or 0)
    except (TypeError, ValueError):
        return False, "ui_* AX bounds 无法解析；请改 obs_screen+click 或 press/hotkey", 0.0, 0.0
    gx = x + w / 2.0
    gy = y + h / 2.0
    if w <= 0 or h <= 0:
        return (
            False,
            f"ui_* ref={node.get('ref')!r} bounds 宽高无效 w={w:.0f} h={h:.0f}；"
            "请改 obs_screen+click 或 press/hotkey",
            gx,
            gy,
        )
    if abs(gx) > 100_000 or abs(gy) > 100_000:
        return (
            False,
            f"ui_* ref={node.get('ref')!r} 中心 ({gx:.0f},{gy:.0f}) 荒谬；"
            "AX 对该控件不可靠，请改 obs_screen+click 或 press/hotkey",
            gx,
            gy,
        )
    ok_pt, pt_err = validate_global_click_point(gx, gy, margin=128.0)
    if not ok_pt:
        return (
            False,
            f"ui_* ref={node.get('ref')!r} {pt_err}；请改 obs_screen+click 或 press/hotkey",
            gx,
            gy,
        )
    return True, "", gx, gy


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
