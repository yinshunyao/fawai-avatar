# term:浏览器操作 tool:browser_* doc:doc/功能/浏览器操作/说明.md
"""浏览器工具冒烟：能力闸门 +（可选）无头打开 example.com。

无 playwright：只断言能力标不可用 / dry_run 路径。
真跑：FAWAI_BROWSER_LIVE=1 且已 pip install -e '.[browser]' && playwright install chromium
"""
from __future__ import annotations

import os

from fawai_avatar.brain.capabilities import known_action_kinds, list_capabilities
from fawai_avatar.brain.prompt_profile import (
    infer_prompt_profile,
    looks_like_browser_goal,
)
from fawai_avatar.control.browser import browser_package_available, close_browser_session
from fawai_avatar.control.executor import Controller
from fawai_avatar.platform_info import clear_platform_cache
from fawai_avatar.types import Action


def main() -> None:
    os.environ["FAWAI_HEADLESS"] = "1"
    clear_platform_cache()

    assert looks_like_browser_goal("打开网页 https://example.com")
    prof = infer_prompt_profile("打开网页 https://example.com")
    assert prof.id == "browser", prof.id

    # dry_run：不依赖 chromium
    ctrl_dry = Controller(dry_run=True)
    dry = ctrl_dry.execute(
        Action(kind="browser_navigate", args={"url": "https://example.com"}),
        task_id="smoke-browser",
    )
    assert dry.ok and "dry_run" in dry.message, dry.message

    has_pw = browser_package_available()
    kinds = known_action_kinds(available_only=True)
    if has_pw:
        assert "browser_navigate" in kinds
        assert "browser_snapshot" in kinds
    else:
        assert "browser_navigate" not in kinds
        caps = list_capabilities(available_only=False)
        browser_caps = [c for c in caps if c.action_kind.startswith("browser_")]
        assert browser_caps and all(not c.available for c in browser_caps)

    live = (os.environ.get("FAWAI_BROWSER_LIVE") or "").strip() in {"1", "true", "yes"}
    if live:
        if not has_pw:
            raise SystemExit("FAWAI_BROWSER_LIVE=1 但未安装 playwright；见 doc/功能/浏览器操作/说明.md")
        os.environ.setdefault("FAWAI_BROWSER_HEADLESS", "1")
        ctrl = Controller(dry_run=False)
        try:
            nav = ctrl.execute(
                Action(kind="browser_navigate", args={"url": "https://example.com"}),
                task_id="smoke-browser-live",
            )
            assert nav.ok, nav.message
            snap = ctrl.execute(Action(kind="browser_snapshot", args={}), task_id="smoke-browser-live")
            assert snap.ok, snap.message
            assert "example" in (snap.message or "").lower() or "Example" in (snap.message or "")
            closed = ctrl.execute(Action(kind="browser_close", args={}), task_id="smoke-browser-live")
            assert closed.ok, closed.message
        finally:
            close_browser_session()

    print(
        "demo_browser ok",
        {"playwright": has_pw, "live": live, "profile": prof.id},
    )
    clear_platform_cache()


if __name__ == "__main__":
    main()
