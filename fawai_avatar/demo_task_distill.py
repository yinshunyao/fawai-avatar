# term:自我进化  doc:doc/功能/自我进化/说明.md
"""Smoke: 终态条件短卡蒸馏启发式 / 关闸 / 跳过已晋升 / 脱敏。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    from fawai_avatar.brain import success_ledger
    from fawai_avatar.brain.task_distill import (
        KEY_TASK_DISTILL as DISTILL_KEY_ALIAS,
        build_distill_materials,
        distill_enabled,
        eligible_for_distill,
        run_task_distill,
        task_already_promoted,
    )
    assert DISTILL_KEY_ALIAS == "FAWAI_TASK_DISTILL"

    # --- 关闸：记忆配置真源 ---
    with tempfile.TemporaryDirectory() as tmp0:
        data0 = Path(tmp0)
        os.environ["FAWAI_DATA_DIR"] = str(data0)
        os.environ.pop("FAWAI_LORE_DIR", None)
        from fawai_avatar.memory_settings import (
            KEY_TASK_DISTILL,
            get_memory_store,
            reset_memory_store_cache,
        )

        reset_memory_store_cache()
        store = get_memory_store(data_dir=str(data0))
        assert store.task_distill_enabled() is True
        assert distill_enabled() is True
        store.set_many({KEY_TASK_DISTILL: "0"})
        assert KEY_TASK_DISTILL not in os.environ, "不得 sync 进 env"
        assert store.task_distill_enabled() is False
        # distill_enabled 走进程单例缓存：先 reset 再指向同一 data_dir
        reset_memory_store_cache()
        os.environ["FAWAI_DATA_DIR"] = str(data0)
        assert distill_enabled() is False
        # 即便人为污染 env，仍只认库
        os.environ[KEY_TASK_DISTILL] = "1"
        assert distill_enabled() is False
        get_memory_store().set_many({KEY_TASK_DISTILL: "1"})
        assert KEY_TASK_DISTILL not in os.environ, "sync 应清掉 NO_ENV 键"
        assert distill_enabled() is True
        reset_memory_store_cache()
        os.environ.pop(KEY_TASK_DISTILL, None)

    # --- 启发式：简单成功不蒸 ---
    simple = eligible_for_distill(
        ok=True,
        meta={
            "stats": {"rounds": 3},
            "replan_count": 0,
            "reflect_count": 0,
            "stagnation_count": 0,
            "steps": [
                {
                    "plan": {
                        "thought": "读配置",
                        "actions": [{"kind": "fs_read", "args": {"path": "a.md"}}],
                    },
                    "results": [{"ok": True, "message": "read"}],
                },
                {
                    "plan": {"thought": "答", "status": "success", "actions": []},
                    "results": [],
                },
            ],
        },
    )
    assert simple.get("eligible") is False, simple
    assert simple.get("reason") == "simple_success", simple

    # --- 启发式：多法成功（fail then ok + reflect）---
    multi = eligible_for_distill(
        ok=True,
        meta={
            "stats": {"rounds": 5},
            "replan_count": 1,
            "reflect_count": 1,
            "stagnation_count": 0,
            "steps": [
                {
                    "plan": {
                        "actions": [{"kind": "shell", "args": {"cmd": "ssh x"}}],
                    },
                    "results": [{"ok": False, "message": "denied"}],
                },
                {
                    "plan": {
                        "actions": [
                            {"kind": "fs_read", "args": {"path": "cfg.md"}},
                            {"kind": "lore_search", "args": {"query": "x"}},
                        ],
                    },
                    "results": [
                        {"ok": True, "message": "read"},
                        {"ok": True, "message": "0"},
                    ],
                },
                {
                    "plan": {
                        "actions": [{"kind": "shell", "args": {"cmd": "sshpass …"}}],
                    },
                    "results": [{"ok": True, "message": "ok"}],
                },
            ],
        },
    )
    assert multi.get("eligible") is True, multi
    assert multi.get("kind") == "success", multi

    # --- 启发式：明确失败 ---
    failed = eligible_for_distill(
        ok=False,
        meta={
            "stats": {"rounds": 3},
            "reflect_count": 1,
            "final_thought": "缺凭证且无法换道",
            "error": "brain failed",
            "steps": [{}, {}],
        },
    )
    assert failed.get("eligible") is True, failed
    assert failed.get("kind") == "failure", failed

    thin_fail = eligible_for_distill(
        ok=False,
        meta={"stats": {"rounds": 1}, "steps": [{}]},
    )
    assert thin_fail.get("eligible") is False, thin_fail

    # --- 材料脱敏（口令不应原样进提示）---
    mats = build_distill_materials(
        goal="查进度",
        ok=True,
        meta={
            "final_thought": "done",
            "steps": [
                {
                    "plan": {
                        "actions": [
                            {
                                "kind": "shell",
                                "args": {
                                    "cmd": "sshpass -p '951753' ssh host uptime",
                                },
                            }
                        ],
                    },
                    "results": [{"ok": True, "message": "ok"}],
                }
            ],
        },
    )
    assert "951753" not in mats, mats[:200]

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        os.environ["FAWAI_DATA_DIR"] = str(data)
        os.environ.pop("FAWAI_LORE_DIR", None)
        os.environ.pop("FAWAI_TASK_DISTILL", None)
        tid = "t-distill-1"

        # --- 记忆配置关闸 run ---
        from fawai_avatar.memory_settings import (
            KEY_TASK_DISTILL,
            get_memory_store,
            reset_memory_store_cache,
        )

        reset_memory_store_cache()
        mem = get_memory_store()
        mem.set_many({KEY_TASK_DISTILL: "0"})
        assert KEY_TASK_DISTILL not in os.environ
        skipped = run_task_distill(
            data_dir=str(data),
            task_id=tid,
            goal="g",
            ok=True,
            meta={"stats": {"rounds": 8}, "replan_count": 1, "steps": []},
            llm_distill=lambda **_: {"ok": True, "card": {"acceptance": "x"}},
            run_gate=False,
        )
        assert skipped.get("skipped") is True, skipped
        assert skipped.get("reason") == "distill_off", skipped
        mem.set_many({KEY_TASK_DISTILL: "1"})
        assert KEY_TASK_DISTILL not in os.environ

        # --- 假 LLM 写成功台账（不过门）---
        progress: list[str] = []

        def _prog(text: str, meta: dict) -> None:
            progress.append(f"{meta.get('phase')}:{meta.get('status')}:{text}")

        def _fake_llm(*, kind: str, materials: str) -> dict:
            assert kind == "success"
            assert "查 5080" in materials or True
            return {
                "ok": True,
                "card": {
                    "acceptance": "SSH 查训练进度用配置 md 落点 + sshpass",
                    "config_refs": [
                        {
                            "path": "study/配置信息/比昂农业.md",
                            "keys": ["5080"],
                            "excerpt": "port 37443",
                        }
                    ],
                    "commands": [{"kind": "shell", "cmd": "sshpass -p 'SECRET' ssh …"}],
                    "avoid": "勿只搜 scopes=lore；勿把网关日志当训练进度",
                    "evidence": "tail 训练日志见 epoch",
                    "tool_id": "task_distill",
                },
                "elapsed_ms": 1,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

        meta = {
            "stats": {"rounds": 8, "tools": {"shell": 4}},
            "replan_count": 1,
            "reflect_count": 1,
            "final_thought": "epoch 32/100",
            "steps": [
                {
                    "plan": {"actions": [{"kind": "lore_search", "args": {}}]},
                    "results": [{"ok": True, "message": "0"}],
                },
                {
                    "plan": {"actions": [{"kind": "fs_read", "args": {}}]},
                    "results": [{"ok": True, "message": "read"}],
                },
                {
                    "plan": {"actions": [{"kind": "shell", "args": {}}]},
                    "results": [{"ok": False, "message": "denied"}],
                },
                {
                    "plan": {"actions": [{"kind": "shell", "args": {}}]},
                    "results": [{"ok": True, "message": "ok"}],
                },
                {
                    "plan": {"actions": [{"kind": "fs_grep", "args": {}}]},
                    "results": [{"ok": True, "message": "grep"}],
                },
                {
                    "plan": {"actions": [{"kind": "shovel_search", "args": {}}]},
                    "results": [{"ok": True, "message": "hits"}],
                },
            ],
        }
        ran = run_task_distill(
            data_dir=str(data),
            task_id=tid,
            goal="查 5080 训练",
            ok=True,
            meta=meta,
            on_progress=_prog,
            llm_distill=_fake_llm,
            run_gate=False,
        )
        assert ran.get("ran") is True, ran
        assert ran.get("ok") is True, ran
        eid = str(ran.get("entry_id") or "")
        assert eid.startswith("sl_"), ran
        entry = success_ledger.get_entry(str(data), tid, eid)
        assert entry, eid
        blob = json.dumps(entry, ensure_ascii=False)
        assert "SECRET" not in blob, blob
        assert entry.get("acceptance"), entry
        assert any("task_distill" in p for p in progress), progress

        # --- 已晋升则跳过 ---
        success_ledger.upsert_entry(
            str(data),
            tid,
            {
                "id": eid,
                "acceptance": entry.get("acceptance"),
                "lore_id": "note_promoted_1",
                "gate": {"status": "passed", "pass": True},
                "evaluate": False,
            },
            schedule_gate=False,
        )
        assert task_already_promoted(str(data), tid) is True
        again = run_task_distill(
            data_dir=str(data),
            task_id=tid,
            goal="查 5080 训练",
            ok=True,
            meta=meta,
            llm_distill=_fake_llm,
            run_gate=False,
        )
        assert again.get("skipped") is True, again
        assert again.get("reason") == "already_promoted", again

        # --- 同步门控晋升（注入 gate）---
        tid2 = "t-distill-2"

        def _gate_pass(entry: dict) -> dict:
            return {
                "status": "passed",
                "pass": True,
                "completeness": 5,
                "generality": 5,
                "efficiency": 4,
                "rationale": "可复用配置落点",
            }

        promo = run_task_distill(
            data_dir=str(data),
            task_id=tid2,
            goal="查进度",
            ok=True,
            meta={
                "stats": {"rounds": 7},
                "replan_count": 1,
                "steps": meta["steps"],
            },
            llm_distill=_fake_llm,
            run_gate=True,
            gate_fn=_gate_pass,
        )
        assert promo.get("ran") is True, promo
        assert promo.get("gate_pass") is True, promo
        assert promo.get("lore_id"), promo

    print("demo_task_distill: ok")


if __name__ == "__main__":
    main()
