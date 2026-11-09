# term:应用前台  doc:doc/功能/多屏与全屏观察/说明.md
"""app_focus：名称匹配、假成功回证、参数别名（不依赖真机切窗）。"""
from __future__ import annotations

from unittest.mock import patch

from fawai_avatar.control import app_focus as mod
from fawai_avatar.control.app_focus import app_name_matches, focus_app
from fawai_avatar.control.executor import Controller
from fawai_avatar.types import Action


def main() -> None:
    assert app_name_matches("WeChat", "WeChat")
    assert app_name_matches("wechat", "WeChat")
    assert app_name_matches("Chrome", "Google Chrome")
    assert app_name_matches("Google Chrome", "Chrome")
    assert app_name_matches("com.google.Chrome", "Google Chrome")
    assert not app_name_matches("WeChat", "Google Chrome")
    assert not app_name_matches("WeChat", None)

    def _fake_probe(**_kwargs: object) -> dict:
        return {
            "ok": True,
            "app": "Google Chrome",
            "window": "x",
            "reason": None,
            "platform": "Darwin",
        }

    checked = mod._verify_frontmost(
        "WeChat",
        platform="Darwin",
        method="test",
        probe_frontmost=_fake_probe,
    )
    assert checked["ok"] is False, checked
    assert "Google Chrome" in str(checked.get("message") or ""), checked
    assert "全屏" in str(checked.get("message") or ""), checked

    def _ok_probe(**_kwargs: object) -> dict:
        return {
            "ok": True,
            "app": "WeChat",
            "window": "聊天",
            "reason": None,
            "platform": "Darwin",
        }

    ok = mod._verify_frontmost(
        "WeChat",
        platform="Darwin",
        method="test",
        probe_frontmost=_ok_probe,
    )
    assert ok["ok"] is True, ok
    assert ok.get("verified") is True

    empty = focus_app("")
    assert empty["ok"] is False
    assert "args.app" in str(empty.get("message") or "")

    # executor 接受 app_name 别名
    ctl = Controller(
        dry_run=False,
        holding_foreground=lambda *_a, **_k: True,
        allows_writes=lambda *_a, **_k: True,
    )
    with patch.object(ctl, "_ensure_gui", return_value=None):
        with patch(
            "fawai_avatar.control.app_focus.focus_app",
            return_value={
                "ok": True,
                "message": "activated WeChat (frontmost=WeChat)",
                "app": "WeChat",
                "platform": "Darwin",
                "verified": True,
            },
        ) as mocked:
            r = ctl.execute(
                Action(
                    kind="app_focus",
                    args={"app_name": "WeChat"},
                    needs_foreground=True,
                ),
                task_id="t",
            )
            assert r.ok is True, r
            assert mocked.call_args[0][0] == "WeChat", mocked.call_args

    print("demo_app_focus: ok")


if __name__ == "__main__":
    main()
