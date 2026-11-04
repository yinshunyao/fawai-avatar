# term:自主操控  doc:doc/功能/自主操控/说明.md
"""C3 窄场景冒烟：提示档、能力、AX 降级、DRY_RUN 接地动作。

真机 TextEdit（需辅助功能 + .[gui]）：
  FAWAI_C3_LIVE=1 FAWAI_DRY_RUN=0 .venv/bin/python -m fawai_avatar.demo_c3
"""
from __future__ import annotations

import os
import sys


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_prompt_profile_gui_not_fs() -> None:
    from fawai_avatar.brain.prompt_profile import (
        infer_prompt_profile,
        looks_like_gui_goal,
    )

    goals = [
        "操作Cursor界面发送任务",
        "在文本编辑里键入 fawai-c3-ok",
        "点击屏幕上的按钮",
        "用鼠标打开微信",
    ]
    for g in goals:
        _assert(looks_like_gui_goal(g), f"应识别 GUI：{g}")
        prof = infer_prompt_profile(g)
        _assert(prof.id == "gui", f"应为 gui 档，got {prof.id} for {g!r}")
        _assert(prof.kind_allow is not None, "gui 档应有 kind_allow")
        allow = prof.kind_allow or frozenset()
        for kind in ("click", "type", "hotkey", "app_focus", "ui_click", "ui_type"):
            _assert(kind in allow, f"gui 档缺 {kind}")


def test_capabilities_include_ui() -> None:
    from fawai_avatar.brain.capabilities import known_action_kinds

    kinds = known_action_kinds(available_only=False)
    for kind in (
        "ui_click",
        "ui_type",
        "ui_verify",
        "double_click",
        "right_click",
        "scroll",
        "press",
        "app_focus",
    ):
        _assert(kind in kinds, f"能力清单缺 {kind}")


def test_obs_ax_headless_or_platform() -> None:
    from fawai_avatar.observe.ax_tree import clear_ax_registry, observe_ax
    from fawai_avatar.observe.methods import candidate_observe_methods

    clear_ax_registry()
    obs = observe_ax()
    meta = obs.meta or {}
    _assert(meta.get("observe_method") == "obs_ax", "meta.observe_method")
    # headless / 非 Darwin：ok=False；Darwin 有桌面：ok 视权限
    _assert("ok" in meta, "须有 ok 字段")
    _assert(obs.summary, "须有摘要")

    cands = candidate_observe_methods("点一下桌面按钮", capture=True)
    # headless → 无 obs_ax；有桌面 Darwin → 含 obs_ax
    from fawai_avatar.platform_info import is_headless, runtime_platform

    if is_headless():
        _assert("obs_ax" not in cands, "headless 候选不应含 obs_ax")
    elif runtime_platform().system == "Darwin":
        _assert("obs_ax" in cands, "Darwin 桌面候选应含 obs_ax")


def test_dry_run_ui_actions() -> None:
    from fawai_avatar.control.executor import Controller
    from fawai_avatar.types import Action

    ctrl = Controller(dry_run=True)
    for kind, args in (
        ("ui_click", {"ref": "1"}),
        ("ui_type", {"ref": "1", "text": "fawai-c3-ok"}),
        ("double_click", {"x": 10, "y": 20}),
        ("right_click", {"x": 10, "y": 20}),
        ("scroll", {"clicks": -2}),
        ("press", {"key": "enter"}),
        ("app_focus", {"app": "TextEdit"}),
    ):
        r = ctrl.execute(Action(kind=kind, args=args, needs_foreground=True), task_id="c3")
        _assert(r.ok, f"dry_run {kind} 应 ok：{r.message}")
        _assert("dry_run" in (r.message or ""), f"{kind} 应标明 dry_run")


def test_verify_ui_ax_empty_registry() -> None:
    from fawai_avatar.observe.ax_tree import clear_ax_registry
    from fawai_avatar.observe.verify import ui_verify_method_supported, verify_ui_ax
    from fawai_avatar.control.executor import Controller
    from fawai_avatar.types import Action

    clear_ax_registry()
    # refresh=False：空 registry 不可冒充「含稀有串」，也不可写成「未找到」假归因
    r = verify_ui_ax(contains="___unlikely_token_c3___", refresh=False)
    _assert(not r.ok, "空 registry 不应误报含稀有串")
    _assert("不可用" in (r.message or ""), f"空树应报不可用，got {r.message!r}")
    _assert("未找到" not in (r.message or ""), "空树禁止冒充未找到某串")

    _assert(ui_verify_method_supported(None), "默认 method 应支持")
    _assert(ui_verify_method_supported("ax"), "method=ax 应支持")
    _assert(not ui_verify_method_supported("ocr"), "method=ocr 应拒绝")
    # 直调 _exec_ui_verify：dry_run 会短路整类 GUI，测不到 method 闸
    denied = Controller(dry_run=False)._exec_ui_verify(
        Action(kind="ui_verify", args={"contains": "x", "method": "ocr"})
    )
    _assert(not denied.ok, "method=ocr 须失败")
    _assert("不支持" in (denied.message or ""), f"拒因：{denied.message}")


def test_resolve_ax_ref_fuzzy_and_error() -> None:
    from fawai_avatar.control.executor import Controller
    from fawai_avatar.observe import ax_tree

    ax_tree.clear_ax_registry()
    with ax_tree._lock:  # noqa: SLF001 — 冒烟注入
        ax_tree._REF_NODES["3"] = {
            "ref": "3",
            "role": "AXTextField",
            "title": "Message",
            "value": "",
            "x": 10.0,
            "y": 20.0,
            "w": 100.0,
            "h": 30.0,
            "pid": 0,
        }
        ax_tree._LAST_FLAT_TEXT = "Message"
    node, how = ax_tree.resolve_ax_ref("3")
    _assert(how == "exact" and node is not None and node["ref"] == "3", "精确编号")
    node2, how2 = ax_tree.resolve_ax_ref("message-field")
    _assert(how2 == "fuzzy" and node2 is not None and node2["ref"] == "3", "标题模糊")
    _n, err = Controller(dry_run=False)._resolve_ui_ref(  # noqa: SLF001
        {"ref": "chat-input-field"}
    )
    _assert(err is not None and not err.ok, "臆造 id 应失败")
    _assert(
        "可用 ref" in (err.message or "") or "obs_ax" in (err.message or ""),
        err.message,
    )
    ax_tree.clear_ax_registry()


def test_live_textedit_optional() -> None:
    if os.environ.get("FAWAI_C3_LIVE", "").strip() not in {"1", "true", "yes"}:
        print("skip live TextEdit（设 FAWAI_C3_LIVE=1 开启）")
        return
    from fawai_avatar.control.executor import Controller
    from fawai_avatar.observe.ax_tree import find_ref_for_role, observe_ax
    from fawai_avatar.observe.verify import verify_ui_ax
    from fawai_avatar.types import Action

    os.environ["FAWAI_DRY_RUN"] = "0"
    ctrl = Controller(dry_run=False)
    focus = ctrl.execute(
        Action(kind="app_focus", args={"app": "TextEdit"}, needs_foreground=True),
        task_id="c3-live",
    )
    _assert(focus.ok, f"app_focus TextEdit：{focus.message}")
    ctrl.execute(
        Action(kind="hotkey", args={"keys": ["command", "n"]}, needs_foreground=True),
        task_id="c3-live",
    )
    obs = observe_ax()
    _assert((obs.meta or {}).get("ok"), f"obs_ax：{obs.summary[:200]}")
    ref = find_ref_for_role(role_substr="text") or find_ref_for_role(role_substr="area")
    _assert(ref, "未找到文本类 ref")
    marker = "fawai-c3-ok"
    typed = ctrl.execute(
        Action(
            kind="ui_type",
            args={"ref": ref, "text": marker},
            needs_foreground=True,
        ),
        task_id="c3-live",
    )
    _assert(typed.ok, f"ui_type：{typed.message}")
    verified = verify_ui_ax(contains=marker, refresh=True)
    _assert(verified.ok, f"verify_ui_ax：{verified.message}")


def main() -> int:
    tests = [
        test_prompt_profile_gui_not_fs,
        test_capabilities_include_ui,
        test_obs_ax_headless_or_platform,
        test_dry_run_ui_actions,
        test_verify_ui_ax_empty_registry,
        test_resolve_ax_ref_fuzzy_and_error,
        test_live_textedit_optional,
    ]
    failed = 0
    for fn in tests:
        name = fn.__name__
        try:
            fn()
            print(f"ok  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {exc}", file=sys.stderr)
    if failed:
        print(f"{failed} failed", file=sys.stderr)
        return 1
    print("demo_c3 all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
