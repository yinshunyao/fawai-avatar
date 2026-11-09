# term:成功价值台账  doc:doc/功能/成功价值台账/说明.md
"""Smoke: success_ledger upsert / search / get + gate→lore。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        os.environ["FAWAI_DATA_DIR"] = str(data)
        os.environ.pop("FAWAI_LORE_DIR", None)

        tid = "t-ledger-1"
        tools = BackgroundTools(
            work_root=data, data_dir=data, auto_version=False, task_id=tid
        )

        up = tools.execute(
            Action(
                kind="success_ledger_upsert",
                args={
                    "acceptance": "远端端口写入 config.yaml 且进程监听成功",
                    "commands": [
                        {"kind": "shell", "cmd": "ss -lntp | grep 23456"},
                    ],
                    "config_refs": [
                        {
                            "path": "deploy/config.yaml",
                            "keys": ["secret_port"],
                            "excerpt": "23456",
                        }
                    ],
                    "code_refs": [{"path": "scripts/apply_port.py"}],
                    "targets": [{"host": "lab-01", "cwd": "/opt/app"}],
                    "evidence": "ss 输出含 :23456",
                    "tool_id": "shell",
                    "avoid": "勿用 which 多参因缺一失败",
                    "evaluate": False,
                },
            )
        )
        assert up.ok, up
        entry = (up.data or {}).get("entry") or {}
        eid = str(entry.get("id") or "")
        assert eid.startswith("sl_"), entry
        assert (entry.get("gate") or {}).get("status") == "skipped"

        path = data / "sessions" / tid / "success_ledger.jsonl"
        assert path.is_file(), path
        assert "23456" in path.read_text(encoding="utf-8")

        sr = tools.execute(
            Action(
                kind="success_ledger_search",
                args={"query": "secret_port 23456"},
            )
        )
        assert sr.ok, sr
        hits = (sr.data or {}).get("hits") or []
        assert hits, sr.data
        assert any(h.get("id") == eid for h in hits), hits

        gr = tools.execute(Action(kind="success_ledger_get", args={"id": eid}))
        assert gr.ok, gr
        got = (gr.data or {}).get("entry") or {}
        assert got.get("acceptance"), got
        assert "23456" in str(got.get("config_refs")), got

        # 跨任务拒绝
        bad = tools.execute(
            Action(
                kind="success_ledger_search",
                args={"query": "端口", "task_id": "other-task"},
            )
        )
        assert not bad.ok, bad

        # 注入门控通过 → 实写 lore；旁路进度/用量回调
        from fawai_avatar.brain.ledger_gate import (
            GateNotify,
            decide_gate_from_scores,
        )
        from fawai_avatar.brain.success_ledger import (
            apply_gate_sync_for_tests,
            get_entry,
        )
        from fawai_avatar.memory_settings import (
            KEY_LEDGER_GATE_THRESHOLD,
            get_memory_store,
            reset_memory_store_cache,
        )

        reset_memory_store_cache()
        get_memory_store(data_dir=str(data)).set_many(
            {KEY_LEDGER_GATE_THRESHOLD: "80"}
        )
        # 阈值：4/5=80% 过门；3/5=60% 拒绝
        low = decide_gate_from_scores(
            {"completeness": 3, "generality": 4, "efficiency": 5, "pass": True},
            threshold_pct=80,
        )
        assert not low["pass"], low
        high = decide_gate_from_scores(
            {"completeness": 4, "generality": 4, "efficiency": 4, "pass": True},
            threshold_pct=80,
        )
        assert high["pass"], high
        from fawai_lore import open_lore
        from fawai_avatar.config import resolve_lore_dir

        progress_log: list[tuple[str, dict]] = []
        usage_log: list[tuple[dict | None, int]] = []

        def _on_progress(text: str, meta: dict) -> None:
            progress_log.append((text, dict(meta)))

        def _on_usage(usage: dict | None, elapsed_ms: int) -> None:
            usage_log.append((usage, int(elapsed_ms)))

        notify = GateNotify(
            on_progress=_on_progress,
            on_usage=_on_usage,
            step=3,
            run_id="run-demo",
        )
        promoted = apply_gate_sync_for_tests(
            data,
            tid,
            eid,
            gate={
                "status": "passed",
                "pass": True,
                "completeness": 4,
                "generality": 4,
                "efficiency": 5,
                "rationale": "demo inject pass",
                "elapsed_ms": 120,
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 40,
                    "total_tokens": 120,
                },
                "model": "demo-fast",
            },
            notify=notify,
        )
        assert promoted.get("ok"), promoted
        after = get_entry(data, tid, eid)
        assert after is not None
        lore_id = after.get("lore_id")
        assert lore_id, after
        assert (after.get("gate") or {}).get("status") == "passed"
        assert usage_log and usage_log[0][1] == 120, usage_log
        assert usage_log[0][0] and usage_log[0][0].get("total_tokens") == 120
        assert progress_log, progress_log
        done = progress_log[-1]
        assert done[1].get("phase") == "success_ledger_gate", done
        assert done[1].get("status") == "done", done
        assert done[1].get("tokens", {}).get("total_tokens") == 120, done
        assert done[1].get("elapsed_ms") == 120, done
        assert "晋升" in done[0] or "lore" in done[0].lower(), done[0]

        lore = open_lore(resolve_lore_dir())
        note = lore.notes.get(str(lore_id))
        assert note is not None, lore_id
        assert "23456" in (note.body or "") or "secret_port" in (note.body or "")

        # 拒绝门：不写 lore
        up2 = tools.execute(
            Action(
                kind="success_ledger_upsert",
                args={
                    "acceptance": "仅本机一次性口令粘贴",
                    "commands": [],
                    "evidence": "ok",
                    "evaluate": False,
                },
            )
        )
        eid2 = str(((up2.data or {}).get("entry") or {}).get("id") or "")
        rej = apply_gate_sync_for_tests(
            data,
            tid,
            eid2,
            gate={
                "status": "rejected",
                "pass": False,
                "rationale": "含密文风险且无通用价值",
            },
        )
        assert rej.get("ok"), rej
        after2 = get_entry(data, tid, eid2)
        assert after2 is not None
        assert not after2.get("lore_id"), after2
        assert (after2.get("gate") or {}).get("status") == "rejected"

        print("demo_success_ledger: ok")


if __name__ == "__main__":
    main()
