# term:失败价值台账  doc:doc/功能/失败价值台账/说明.md
"""Smoke: failure_ledger upsert / search / get + avoid lore + 阈值。"""

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

        tid = "t-fail-1"
        tools = BackgroundTools(
            work_root=data, data_dir=data, auto_version=False, task_id=tid
        )

        up = tools.execute(
            Action(
                kind="failure_ledger_upsert",
                args={
                    "symptom": "ss 无监听导致误判已启动",
                    "cause": "看了错误 cwd 下的旧进程表",
                    "do_not_retry": "勿在未 cd 到 /opt/app 时用 ss 验收",
                    "commands_tried": [{"kind": "shell", "cmd": "ss -lntp | grep 23456"}],
                    "targets": [{"cwd": "/tmp"}],
                    "evidence": "ss 空输出但服务未起",
                    "severity": "wasted_loop",
                    "evaluate": False,
                },
            )
        )
        assert up.ok, up
        entry = (up.data or {}).get("entry") or {}
        eid = str(entry.get("id") or "")
        assert eid.startswith("fl_"), entry
        assert (data / "sessions" / tid / "failure_ledger.jsonl").is_file()

        sr = tools.execute(
            Action(kind="failure_ledger_search", args={"query": "ss cwd 勿再试"})
        )
        assert sr.ok and (sr.data or {}).get("hits"), sr
        gr = tools.execute(Action(kind="failure_ledger_get", args={"id": eid}))
        assert gr.ok and "勿在未 cd" in str((gr.data or {}).get("entry")), gr

        bad = tools.execute(
            Action(
                kind="failure_ledger_search",
                args={"query": "x", "task_id": "other"},
            )
        )
        assert not bad.ok, bad

        from fawai_avatar.brain.failure_ledger import apply_gate_sync_for_tests, get_entry
        from fawai_avatar.brain.ledger_gate import GateNotify
        from fawai_avatar.config import resolve_lore_dir
        from fawai_lore import open_lore

        progress: list[tuple[str, dict]] = []
        notify = GateNotify(
            on_progress=lambda t, m: progress.append((t, dict(m))),
            phase="failure_ledger_gate",
        )
        # 分不够阈值 → 不写 lore
        rej = apply_gate_sync_for_tests(
            data,
            tid,
            eid,
            gate={
                "status": "rejected",
                "pass": False,
                "completeness": 3,
                "generality": 3,
                "efficiency": 3,
                "threshold_pct": 80,
                "rationale": "demo below threshold",
            },
            notify=notify,
        )
        assert rej.get("ok"), rej
        assert not (get_entry(data, tid, eid) or {}).get("lore_id")

        # 过门 → avoid lore
        up2 = tools.execute(
            Action(
                kind="failure_ledger_upsert",
                args={
                    "symptom": "假成功：仅 ok 无端口证据",
                    "cause": "把 shell exit0 当业务验收",
                    "do_not_retry": "禁止无监听证据就标完成",
                    "evaluate": False,
                },
            )
        )
        eid2 = str(((up2.data or {}).get("entry") or {}).get("id") or "")
        ok = apply_gate_sync_for_tests(
            data,
            tid,
            eid2,
            gate={
                "status": "passed",
                "pass": True,
                "completeness": 4,
                "generality": 4,
                "efficiency": 5,
                "threshold_pct": 80,
                "rationale": "demo pass",
                "elapsed_ms": 50,
                "usage": {"total_tokens": 30, "prompt_tokens": 20, "completion_tokens": 10},
            },
            notify=notify,
        )
        assert ok.get("ok"), ok
        after = get_entry(data, tid, eid2)
        assert after and after.get("lore_id"), after
        note = open_lore(resolve_lore_dir()).notes.get(str(after["lore_id"]))
        assert note is not None
        assert "avoid" in (note.tags or []) or "[avoid]" in (note.title or "")
        done = [p for p in progress if p[1].get("status") == "done"]
        assert done and done[-1][1].get("phase") == "failure_ledger_gate", progress

        print("demo_failure_ledger: ok")


if __name__ == "__main__":
    main()
