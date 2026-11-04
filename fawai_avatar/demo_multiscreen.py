# term:多屏观察  doc:doc/功能/多屏与全屏观察/说明.md
"""多屏观察冒烟：displays 枚举、screen_target 解析、click origin、app_focus 登记。"""
from __future__ import annotations

from fawai_avatar.brain.capabilities import known_action_kinds
from fawai_avatar.brain.planner import _normalize_observe_args, _plan_from_payload
from fawai_avatar.control.executor import (
    resolve_ax_click_center,
    resolve_click_global,
    validate_global_click_point,
    validate_image_click_xy,
)
from fawai_avatar.observe.displays import (
    format_displays_block,
    list_displays,
    normalize_screen_target,
    resolve_capture_region,
    screen_target_from_observe_args,
)


def main() -> None:
    assert normalize_screen_target(None) == "frontmost"
    assert normalize_screen_target("") == "frontmost"
    assert normalize_screen_target("main") == "main"
    assert normalize_screen_target("ALL") == "all"
    assert normalize_screen_target("2") == "index:2"
    assert normalize_screen_target("index:3") == "index:3"

    assert screen_target_from_observe_args(None) == "frontmost"
    assert (
        screen_target_from_observe_args(
            {"obs_screen": {"screen_target": "index:2"}}
        )
        == "index:2"
    )

    fake = [
        {
            "index": 1,
            "bounds": {"left": 0, "top": 0, "width": 1920, "height": 1080},
            "is_main": True,
        },
        {
            "index": 2,
            "bounds": {"left": 1920, "top": 0, "width": 2560, "height": 1440},
            "is_main": False,
        },
    ]
    main_r = resolve_capture_region("main", displays=fake)
    assert main_r["origin_x"] == 0 and main_r["monitor_index"] == 1
    idx2 = resolve_capture_region("index:2", displays=fake)
    assert idx2["origin_x"] == 1920 and idx2["monitor_index"] == 2
    # frontmost 落在外接屏
    fm = resolve_capture_region(
        "frontmost", displays=fake, frontmost_point=(2000.0, 100.0)
    )
    assert fm["monitor_index"] == 2
    # 单屏 frontmost ≡ main
    single = resolve_capture_region(
        "frontmost",
        displays=[fake[0]],
        frontmost_point=None,
    )
    assert single["monitor_index"] == 1

    block = format_displays_block(fake)
    assert "index=1" in block and "index=2" in block

    gx, gy, meta = resolve_click_global(
        10.0, 20.0, {"origin_x": 1920, "origin_y": 0}
    )
    assert (gx, gy) == (1930.0, 20.0)
    assert meta["coord_space"] == "image"
    gx2, gy2, meta2 = resolve_click_global(
        10.0, 20.0, {"coord_space": "global", "origin_x": 1920}
    )
    assert (gx2, gy2) == (10.0, 20.0)
    assert meta2["coord_space"] == "global"

    # Retina：PNG 2x → 图内像素压半再加 origin
    gx3, gy3, meta3 = resolve_click_global(
        100.0,
        200.0,
        {
            "origin_x": 1920,
            "origin_y": 0,
            "image_width": 3840,
            "image_height": 2160,
            "capture_width": 1920,
            "capture_height": 1080,
        },
    )
    assert (gx3, gy3) == (1970.0, 100.0)
    assert abs(meta3["coord_scale_x"] - 0.5) < 1e-6

    ok_img, _ = validate_image_click_xy(
        4000.0, 10.0, {"image_width": 1920, "image_height": 1080}
    )
    assert ok_img is False
    ok_ax, msg_ax, _, _ = resolve_ax_click_center(
        {"ref": "3", "x": 0, "y": 0, "w": 100, "h": 1_800_000}
    )
    assert ok_ax is False and ("荒谬" in msg_ax or "无效" in msg_ax)
    # 有显示器清单时：远离所有屏的点应拒
    ok_far, _ = validate_global_click_point(9_000_000.0, 9_000_000.0)
    assert ok_far is False

    plan = _plan_from_payload(
        {
            "thought": "截外接屏",
            "status": "continue",
            "done": False,
            "observe_kinds": ["obs_screen"],
            "observe_args": {"obs_screen": {"screen_target": "index:2"}},
            "actions": [],
        }
    )
    assert plan.observe_kinds == ["obs_screen"]
    assert plan.observe_args["obs_screen"]["screen_target"] == "index:2"

    # 点了截屏但未写 observe_args → 默认 frontmost
    plan2 = _plan_from_payload(
        {
            "thought": "截屏",
            "status": "continue",
            "observe_kinds": ["obs_screen"],
            "actions": [],
        }
    )
    assert plan2.observe_args["obs_screen"]["screen_target"] == "frontmost"

    assert _normalize_observe_args(None) == {}
    assert "app_focus" in known_action_kinds()

    # 真机枚举（有桌面且装了 mss 才非空；headless 允许空）
    try:
        from fawai_avatar.platform_info import is_headless

        headless = is_headless()
    except Exception:  # noqa: BLE001
        headless = True
    displays = list_displays()
    if headless:
        assert displays == []
        print("demo_multiscreen: ok (headless, displays empty)")
    else:
        print(
            f"demo_multiscreen: ok (displays={len(displays)} "
            f"{[d.get('index') for d in displays]})"
        )


if __name__ == "__main__":
    main()
