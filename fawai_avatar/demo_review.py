# term:任务复盘  doc:doc/功能/任务复盘/说明.md
"""任务复盘验收：分析不落盘 → 勾选备份执行；终态可删；单/联合。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from fawai_avatar.brain.planner import Brain
from fawai_avatar.channel.hub import SessionHub
from fawai_avatar.channel.message import Message
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.skills.review import (
    analyze_review_tasks,
    apply_review_plan,
    is_review_awaiting_apply,
    is_review_awaiting_approval,
    parse_review_approval,
    parse_review_apply_reply,
    propose_review_session,
    review_task,
    review_tasks,
)
from fawai_avatar.skills.store import SkillStore
from fawai_avatar.task.supervisor import Supervisor


def main() -> None:
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    with tempfile.TemporaryDirectory(prefix="fawai-review-") as tmp:
        root = Path(tmp)
        work = root / "work"
        data = root / "data"
        work.mkdir()
        data.mkdir()
        os.environ["FAWAI_WORK_ROOT"] = str(work)
        os.environ["FAWAI_DATA_DIR"] = str(data)

        supervisor = Supervisor(str(data))
        task_id = supervisor.create_task("在工作区写入 notes/review_hello.txt 内容 hello")
        loop = WorkerLoop(
            supervisor,
            brain=Brain(),
            controller=Controller(dry_run=False, supervisor=supervisor),
            max_steps=4,
            capture=False,
        )
        loop.controller._background = BackgroundTools(
            work_root=work, data_dir=data, auto_version=False
        )
        out = loop.run(task_id)
        assert out.get("ok"), out

        task = supervisor.get_task(task_id)
        assert task is not None and task.meta.get("steps")

        # 运行中不可删
        tid_run = supervisor.create_task("占位进行中")
        assert supervisor.set_running(tid_run)
        ok, msg = supervisor.delete_task(tid_run)
        assert not ok and "进行中" in msg
        assert supervisor.cancel(tid_run)
        ok, msg = supervisor.delete_task(tid_run)
        assert ok, msg

        # 完成态可直接硬删（无需先 cancel）
        ok, msg = supervisor.delete_task(task_id)
        assert ok, msg

        # 成功任务 → 分析不落盘
        tid2 = supervisor.create_task("在工作区写入 notes/review2.txt 内容 world")
        loop2 = WorkerLoop(
            supervisor,
            brain=Brain(),
            controller=Controller(dry_run=False, supervisor=supervisor),
            max_steps=4,
            capture=False,
        )
        loop2.controller._background = BackgroundTools(
            work_root=work, data_dir=data, auto_version=False
        )
        assert loop2.run(tid2).get("ok")

        analyzed = analyze_review_tasks(
            supervisor, [tid2], distill=True, use_llm=False
        )
        assert analyzed.get("ok"), analyzed
        assert "【分析】" in (analyzed.get("message") or "")
        assert "【优化清单】" in (analyzed.get("message") or "")
        assert analyzed.get("skill") is None
        assert analyzed.get("items"), analyzed
        dims = analyzed.get("dimensions") or {}
        for key in (
            "planning",
            "execution",
            "scheduling",
            "tool_results",
            "outcome_judgment",
        ):
            assert dims.get(key), f"missing dimension {key}: {dims}"
        print(
            "analyze-no-write",
            json.dumps(
                {
                    "items": [it.get("id") for it in analyzed.get("items") or []],
                    "outcome": analyzed.get("outcome"),
                    "detail_fetched": analyzed.get("detail_fetched"),
                },
                ensure_ascii=False,
            ),
        )

        # 全选 apply → 写 skill + 有 release 备份
        applied = apply_review_plan(
            supervisor,
            task_ids=[tid2],
            items=list(analyzed.get("items") or []),
            selected_ids=[str(it["id"]) for it in (analyzed.get("items") or [])],
            work_root=work,
            releases_dir=data / "runtime_releases",
            dimensions=dims,
            analysis=str(analyzed.get("analysis") or ""),
            outcome=str(analyzed.get("outcome") or ""),
        )
        assert applied.get("ok"), applied
        assert applied.get("skill"), applied
        assert (applied.get("backup") or {}).get("release_id"), applied.get("backup")
        print(
            "apply-all",
            json.dumps(
                {
                    "skill": applied["skill"]["id"],
                    "release": (applied.get("backup") or {}).get("release_id"),
                },
                ensure_ascii=False,
            ),
        )

        # 兼容入口 auto_apply
        ok_rev = review_task(
            supervisor, tid2, distill=True, use_llm=False, work_root=work
        )
        assert ok_rev.get("ok"), ok_rev
        assert ok_rev.get("skill"), ok_rev

        # 失败任务 → lesson
        tid3 = supervisor.create_task("故意失败任务")
        supervisor.set_running(tid3)
        supervisor.fail(tid3, message="定位失败：找不到按钮")
        supervisor.merge_task_meta(
            tid3,
            {"ok": False, "error": "定位失败：找不到按钮", "final_thought": "无法完成点击"},
        )
        fail_rev = review_task(
            supervisor, tid3, distill=True, use_llm=False, work_root=work
        )
        assert fail_rev.get("ok"), fail_rev
        assert fail_rev.get("outcome") == "failure"
        assert "lesson" in (fail_rev.get("decisions") or [])
        skill = fail_rev.get("skill") or {}
        assert "lesson" in (skill.get("tags") or []) or str(skill.get("id", "")).startswith(
            "lesson_"
        )
        stored = SkillStore(data / "skills").get(skill["id"])
        assert stored is not None
        assert "失败" in stored.get("steps", "") or "教训" in stored.get("title", "")
        print(
            "failure-lesson",
            json.dumps(
                {"decisions": fail_rev.get("decisions"), "skill": skill.get("id")},
                ensure_ascii=False,
            ),
        )

        ratchet = review_task(supervisor, tid2, ratchet=True)
        assert not ratchet.get("ok")
        assert ratchet.get("ratchet_status") == "disabled"

        # 联合复盘引擎
        tid4 = supervisor.create_task("现在在播放什么音乐?")
        supervisor.set_running(tid4)
        supervisor.complete(tid4)
        supervisor.merge_task_meta(
            tid4,
            {
                "ok": True,
                "final_thought": "当前在播某首歌",
                "steps": [
                    {
                        "plan": {
                            "status": "done",
                            "thought": "读了播放器标题",
                            "actions": [],
                        }
                    }
                ],
            },
        )
        tid5 = supervisor.create_task("切换到左上角播放器页面再看一眼（承接）")
        supervisor.set_running(tid5)
        supervisor.complete(tid5)
        supervisor.merge_task_meta(
            tid5,
            {
                "ok": True,
                "final_thought": "已打开播放器页并确认曲目",
                "steps": [
                    {
                        "plan": {
                            "status": "done",
                            "thought": "点了播放器入口",
                            "actions": [],
                        }
                    }
                ],
            },
        )
        joint = review_tasks(
            supervisor, [tid4, tid5], distill=True, use_llm=False, work_root=work
        )
        assert joint.get("ok"), joint
        assert joint.get("joint") is True
        assert joint.get("task_ids") == [tid4, tid5]
        assert joint.get("skill"), joint
        assert "joint" in (joint["skill"].get("tags") or [])
        print(
            "joint-review",
            json.dumps(
                {
                    "decisions": joint.get("decisions"),
                    "skill": joint["skill"]["id"],
                    "task_ids": [x[:8] for x in joint.get("task_ids") or []],
                },
                ensure_ascii=False,
            ),
        )

        # 勾选子集 / 取消解析
        items = list(analyzed.get("items") or [])
        assert parse_review_apply_reply("取消", items).get("cancel") is True
        subset = parse_review_apply_reply("执行 1", items)
        assert subset.get("mode") == "apply"
        assert subset.get("selected_ids") == ["1"]
        cancel_apply = apply_review_plan(
            supervisor,
            task_ids=[tid2],
            items=items,
            selected_ids=[],
            work_root=work,
            releases_dir=data / "runtime_releases",
        )
        assert cancel_apply.get("ok")
        assert not (cancel_apply.get("backup") or {}).get("release_id")
        assert cancel_apply.get("skill") is None

        # 提议会话：先指令、waiting
        prop = propose_review_session(supervisor, [tid2, tid3])
        assert prop.get("ok"), prop
        assert prop.get("phase") == "awaiting_approval"
        assert "【复盘指令】" in (prop.get("message") or "")
        review_tid = prop["task_id"]
        rt = supervisor.get_task(review_tid)
        assert rt is not None and rt.status == "waiting"
        assert is_review_awaiting_approval(rt)
        assert parse_review_approval("放行").get("guidance") == ""
        assert "沉淀" in (
            parse_review_approval("请重点沉淀失败教训").get("guidance") or ""
        )

        # 枢纽：放行分析 → 全部执行
        async def _hub_approve() -> None:
            hub = SessionHub(supervisor, auto_run_worker=False)
            card = await hub.start_review([tid4, tid5], distill=True, ratchet=False)
            assert card.kind == "card", card
            assert "【复盘指令】" in (card.text or ""), card.text
            assert (card.meta or {}).get("phase") == "awaiting_approval"
            rid = card.task_id
            assert rid
            waiting = supervisor.get_task(rid)
            assert waiting is not None and waiting.status == "waiting"
            assert is_review_awaiting_approval(waiting)
            out1 = await hub.handle_inbound(Message.text_user("放行", task_id=rid))
            assert out1 is not None and out1.kind != "error", out1
            deadline = time.time() + 5
            while time.time() < deadline:
                cur = supervisor.get_task(rid)
                if cur and is_review_awaiting_apply(cur):
                    break
                if cur and cur.status in {"failed"}:
                    break
                await asyncio.sleep(0.05)
            cur = supervisor.get_task(rid)
            assert cur is not None and is_review_awaiting_apply(cur), cur
            assert (cur.meta or {}).get("review_items")
            from fawai_avatar.skills.review import items_for_ui

            ui_items = items_for_ui((cur.meta or {}).get("review_items"))
            assert ui_items and all("id" in x and "title" in x for x in ui_items)
            assert all(
                str(x.get("change_summary") or "").strip()
                and str(x.get("preview") or "").strip()
                and str(x.get("impact") or "").strip()
                for x in ui_items
            ), ui_items

            out2 = await hub.handle_inbound(Message.text_user("全部", task_id=rid))
            assert out2 is not None and out2.kind != "error", out2
            deadline = time.time() + 8
            while time.time() < deadline:
                cur = supervisor.get_task(rid)
                if cur and cur.status in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.05)
            cur = supervisor.get_task(rid)
            assert cur is not None and cur.status == "completed", cur
            assert (cur.meta or {}).get("review_phase") == "completed"
            last = (cur.meta or {}).get("last_review") or {}
            assert last.get("skill_id") or last.get("selected_ids") is not None
            assert (last.get("backup") or {}).get("release_id")
            print(
                "propose-approve-apply",
                json.dumps(
                    {
                        "review_task": rid[:8],
                        "phase": cur.meta.get("review_phase"),
                        "skill": last.get("skill_id"),
                        "release": (last.get("backup") or {}).get("release_id"),
                    },
                    ensure_ascii=False,
                ),
            )

        asyncio.run(_hub_approve())

        # 勾选闸「取消」：不得被总管 control 抢走；须 publish 终态供 WS 侧栏刷新
        async def _hub_cancel_apply() -> None:
            hub = SessionHub(supervisor, auto_run_worker=False)
            published: list[Message] = []

            async def _capture(m: Message) -> None:
                published.append(m)

            hub.add_broadcaster(_capture)
            card = await hub.start_review([tid2], distill=True, ratchet=False)
            rid = card.task_id
            assert rid
            await hub.handle_inbound(Message.text_user("放行", task_id=rid))
            deadline = time.time() + 5
            while time.time() < deadline:
                cur = supervisor.get_task(rid)
                if cur and is_review_awaiting_apply(cur):
                    break
                await asyncio.sleep(0.05)
            cur = supervisor.get_task(rid)
            assert cur is not None and is_review_awaiting_apply(cur), cur
            published.clear()
            out = await hub.handle_inbound(Message.text_user("取消", task_id=rid))
            assert out is not None and out.kind != "error", out
            assert (out.meta or {}).get("control") == "review_cancel", out.meta
            cur = supervisor.get_task(rid)
            assert cur is not None and cur.status == "completed", cur
            assert (cur.meta or {}).get("review_phase") == "completed"
            assert ((cur.meta or {}).get("last_review") or {}).get("cancelled") is True
            assert any(
                (m.meta or {}).get("control") == "review_cancel"
                and (m.meta or {}).get("phase") == "completed"
                for m in published
            ), [m.to_dict() for m in published]
            print("review-apply-cancel", rid[:8], "completed+published")

        asyncio.run(_hub_cancel_apply())

        # 普通 waiting 任务：文本「取消」走控制口令，成功卡必须 publish（WS 侧栏依赖）
        async def _hub_control_cancel_publish() -> None:
            hub = SessionHub(supervisor, auto_run_worker=False)
            published: list[Message] = []

            async def _capture(m: Message) -> None:
                published.append(m)

            hub.add_broadcaster(_capture)
            tid = supervisor.create_task("control-cancel-publish")
            supervisor.set_waiting(tid)
            published.clear()
            out = await hub.handle_inbound(Message.text_user("取消", task_id=tid))
            assert out is not None and out.kind == "card", out
            assert (out.meta or {}).get("control") == "cancel", out.meta
            assert (out.meta or {}).get("status") == "cancelled", out.meta
            assert supervisor.get_task(tid).status == "cancelled"
            assert any(
                (m.meta or {}).get("control") == "cancel"
                and (m.meta or {}).get("status") == "cancelled"
                for m in published
            ), [m.to_dict() for m in published]
            print("control-cancel-publish", tid[:8], "cancelled+published")

        asyncio.run(_hub_control_cancel_publish())

    print("review acceptance ok")


if __name__ == "__main__":
    main()
