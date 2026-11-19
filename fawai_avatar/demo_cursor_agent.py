# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""Cursor Agent 冒烟：设置库 + 诊断 + auth 解析；不强制联网登录。"""
from __future__ import annotations

import os
import tempfile


def main() -> None:
    from fawai_avatar.cursor_agent import auth as ca_auth
    from fawai_avatar.cursor_agent import manager as ca_mgr
    from fawai_avatar.cursor_agent import runtime as ca_rt
    from fawai_avatar.cursor_agent.settings_store import CursorAgentSettingsStore

    with tempfile.TemporaryDirectory(prefix="fawai_ca_") as tmp:
        store = CursorAgentSettingsStore(data_dir=tmp)
        assert store.is_enabled() is False
        store.update_settings(
            enabled=True,
            model_id="composer-2.5",
            mode="plan",
            auto_accept=True,
        )
        assert store.is_enabled() is True
        assert store.model_id() == "composer-2.5"
        assert store.mode() == "plan"
        assert store.auto_accept() is True
        store.update_settings(mode="agent", auto_accept=False)
        assert store.mode() == "agent"
        assert store.auto_accept() is False
        store.set_model_catalog(
            [
                {"id": "auto", "label": "Auto (default)"},
                {"id": "composer-2.5", "label": "Composer 2.5"},
            ]
        )
        cat = store.model_catalog()
        assert cat["count"] == 2
        store.upsert_agent("agt_demo", task_id="task1", cwd=tmp, model_id="composer-2.5", title="demo")
        row = store.get_agent("agt_demo")
        assert row and row["task_id"] == "task1"
        assert store.find_agent_by_task("task1")
        store.archive_agent("agt_demo")
        assert store.get_agent("agt_demo")["archived"] is True
        payload = store.settings_payload()
        assert payload["enabled"] is True
        assert payload["mode"] == "agent"
        assert payload["auto_accept"] is False
        assert "auth" in payload
        print("settings_store: ok")

    entry = ca_rt.normalize_model_entry({"id": "composer-2.5", "display_name": "Composer 2.5"})
    assert entry and entry["id"] == "composer-2.5"
    assert ca_rt.normalize_mode("PLAN") == "plan"
    assert ca_rt.normalize_mode("nope") == "agent"
    fb = ca_rt.fallback_models()
    assert any(m.get("id") == "auto" for m in fb)
    print("runtime.normalize: ok")
    print(f"sdk_available={ca_rt.sdk_available()}")

    from fawai_avatar.cursor_agent.session_progress import (
        CursorSessionProgress,
        cursor_tool_label,
    )

    assert cursor_tool_label("shell") == "Shell"
    assert cursor_tool_label("grep") == "Search"
    progress_rows: list[dict] = []

    def _emit(text: str, *, task_id: str, meta: dict) -> None:
        progress_rows.append({"text": text, "meta": dict(meta or {})})

    prog = CursorSessionProgress(_emit, task_id="demo-ca")
    prog.handle({"type": "run_start", "run_id": "r-demo", "agent_id": "a-demo"})
    prog.handle({"type": "thinking_done", "text": "先读配置"})
    prog.handle(
        {
            "type": "tool_done",
            "call_id": "t1",
            "name": "read_file",
            "display_name": "Read",
            "detail": "cfg.md",
            "args": {"path": "cfg.md"},
            "result_preview": "ok",
            "ok": True,
        }
    )
    tail = prog.finalize()
    assert tail.get("steps"), tail
    assert (tail.get("cursor_tools") or {}).get("Read") == 1
    assert any((r["meta"] or {}).get("run_id") == "r-demo" for r in progress_rows)
    assert any((r["meta"] or {}).get("phase") == "acted" for r in progress_rows)
    print("session_progress.steps: ok")

    snap = ca_auth.auth_snapshot()
    assert "logged_in" in snap and "auth_path" in snap
    print(
        f"auth: status={snap.get('status')} source={snap.get('source') or '-'} "
        f"node={snap.get('node_available')} sdk_node={snap.get('sdk_node_available')}"
    )

    d = ca_mgr.diagnose()
    assert "status" in d and "sdk_installed" in d and "auth" in d
    print(f"diagnose: status={d['status']} sdk={d['sdk_installed']} has_auth={d.get('has_auth')}")
    print("demo_cursor_agent: OK")


if __name__ == "__main__":
    os.environ.setdefault("FAWAI_DATA_DIR", os.environ.get("FAWAI_DATA_DIR") or "data")
    main()
