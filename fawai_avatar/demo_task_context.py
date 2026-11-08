# term:任务内上下文查询  doc:doc/功能/任务内上下文查询/说明.md
"""Smoke: task_context_search / task_context_get（同任务词法 Recall）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        observe = data / "observe"
        observe.mkdir(parents=True)
        dump = observe / "tool_demo_hits.txt"
        dump.write_text(
            "HIT line: secret_port=23456 in config.yaml\nmore lines\n",
            encoding="utf-8",
        )

        history = [
            {"role": "user", "content": "请查一下远程端口并改配置"},
            {
                "role": "user",
                "content": (
                    "执行结果（工具结果观察）\n"
                    "- fs_grep → ok\n"
                    "path=/tmp/demo/config.yaml\n"
                    f"全文落盘 {dump}\n"
                    "hits: secret_port=23456"
                ),
            },
            {
                "role": "assistant",
                "content": "已看到端口线索，下一步改配置",
            },
            {
                "role": "user",
                "content": "追问：刚才端口号是多少来着？",
            },
        ]

        tools = BackgroundTools(
            work_root=data, data_dir=data, auto_version=False, task_id="t-ctx-1"
        )
        tools.history_override = history

        r = tools.execute(
            Action(kind="task_context_search", args={"query": "secret_port 23456"})
        )
        assert r.ok, r
        hits = (r.data or {}).get("hits") or []
        assert hits, r.data
        evidence = next(
            (h for h in hits if "23456" in str(h.get("summary") or "")),
            None,
        )
        assert evidence is not None, hits

        mid = str(evidence.get("msg_id") or "")
        g = tools.execute(Action(kind="task_context_get", args={"msg_id": mid}))
        assert g.ok, g
        assert "23456" in str((g.data or {}).get("text") or ""), g.data

        # observe dump 回灌
        dumps = evidence.get("observe_dumps") or []
        assert dumps, evidence
        og = tools.execute(
            Action(kind="task_context_get", args={"observe_path": dumps[0]})
        )
        assert og.ok, og
        assert "23456" in str((og.data or {}).get("text") or ""), og.data

        # 跨任务拒绝
        bad = tools.execute(
            Action(
                kind="task_context_search",
                args={"query": "端口", "task_id": "other-task"},
            )
        )
        assert not bad.ok, bad

        # 无 override 时空库可空命中
        tools2 = BackgroundTools(
            work_root=data, data_dir=data, auto_version=False, task_id="t-empty"
        )
        empty = tools2.execute(
            Action(kind="task_context_search", args={"query": "端口"})
        )
        assert empty.ok, empty
        assert (empty.data or {}).get("count") == 0

        # 无检查点：prior 结论 + 会话信封可搜
        import json
        import time

        from fawai_avatar.brain.task_context import (
            merge_prior_into_history,
            task_context_search,
        )
        from fawai_avatar.task.models import TaskRecord
        from fawai_avatar.task.store import TaskStore

        tid_prior = "t-prior-1"
        prior_meta = {
            "prior_final_thought": "训练 Epoch 20，完成度 53%，约 1.6 it/s",
            "final_thought": "本任务历史无记录",
        }
        store = TaskStore(str(data))
        now = time.time()
        store.insert_task(
            TaskRecord(
                task_id=tid_prior,
                goal="查看训练进度",
                status="completed",
                priority=0,
                meta=dict(prior_meta),
                write_resources=[],
                created_at=now,
                updated_at=now,
            )
        )
        sh = data / "session_history.jsonl"
        sh.write_text(
            json.dumps(
                {
                    "task_id": tid_prior,
                    "kind": "text",
                    "text": (
                        "4090D 训练任务正在正常运行中。"
                        "训练进度：Epoch 20，完成度 53%，处理速度约 1.6 it/s。"
                    ),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        seeded = merge_prior_into_history(
            [{"role": "user", "content": "【追问】预估什么时候结束"}],
            meta=dict(prior_meta),
            data_dir=data,
            task_id=tid_prior,
        )
        assert any("Epoch 20" in str(m.get("content") or "") for m in seeded), seeded
        assert seeded[-1]["content"].startswith("【追问】"), seeded
        sr = task_context_search(
            query="Epoch 完成度",
            task_id=tid_prior,
            data_dir=data,
            history_override=seeded,
        )
        assert sr.get("count", 0) >= 1, sr

        # live 过薄（只种追问）时仍应并集 prior（读库里的 prior_final）
        thin = task_context_search(
            query="Epoch 完成度",
            task_id=tid_prior,
            data_dir=data,
            history_override=[{"role": "user", "content": "【追问】预估什么时候结束"}],
        )
        assert thin.get("count", 0) >= 1, thin
        assert thin.get("source") in {"prior+live", "checkpoint+live", "prior"}, thin

        # 命中观察不得渲成 ?:?:
        from fawai_avatar.observe.tool_digest import _render_hits

        rendered = _render_hits(
            [
                {
                    "msg_id": "m0",
                    "kind": "assistant",
                    "summary": "Epoch 20，完成度 53%",
                    "score": 2.0,
                },
                {"path": "a.md", "line": 3, "text": "4090 ssh"},
            ]
        )
        assert "?:?:" not in rendered, rendered
        assert "Epoch 20" in rendered, rendered
        assert "a.md:3:" in rendered, rendered

    print("demo_task_context: ok")


if __name__ == "__main__":
    main()
