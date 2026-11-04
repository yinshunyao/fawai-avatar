# term:复杂任务计划模式  doc:doc/功能/复杂任务计划模式/说明.md
"""计划模式冒烟：判定、出图、调度、终态衔接、reorder/lease_adjust 真修订。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.orchestrator import (
    analyze_plan_append,
    build_plan_graph,
    decide_complexity,
    should_enter_plan_mode,
)
from fawai_avatar.brain.plan_scheduler import (
    apply_plan_revision,
    bootstrap_parent_meta,
    is_orchestrator_task,
    is_supervisor_task,
    load_graph,
    on_supervisor_finished,
    replan_full_graph,
    retry_plan_nodes,
    spawn_ready_children,
    wants_full_replan_followup,
)
from fawai_avatar.task.supervisor import Supervisor


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    os.environ["FAWAI_PLAN_MODE"] = "auto"

    print("== decide_complexity (编排脑，无人审) ==")
    d0 = decide_complexity("读一下 readme")
    _assert(d0.get("enter") is False, d0)
    _assert(not should_enter_plan_mode("读一下 readme"), "simple goal")
    d1 = decide_complexity("先规划：写 notes/a.txt 内容 hello，然后读取并回复内容")
    _assert(d1.get("enter") is True, d1)
    _assert(d1.get("reason"), "need reason")
    d2 = decide_complexity(
        "在工作区写入 notes/plan_a.txt 内容 alpha，然后读取该文件并总结内容"
    )
    _assert(d2.get("enter") is True, d2)

    print("== build_plan_graph sequential ==")
    goal = "先规划：写入 notes/p1.txt 为 one，然后读取 notes/p1.txt 并答复内容"
    graph = build_plan_graph(goal)
    print({"nodes": len(graph.nodes), "source": graph.source})
    _assert(len(graph.nodes) >= 2, "expect >=2 nodes")
    _assert(graph.nodes[1].depends_on == [graph.nodes[0].id], "serial depends")

    print("== scheduler spawn + advance ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        meta = bootstrap_parent_meta(graph=graph, title="计划冒烟")
        parent_id = supervisor.create_task(goal, meta=meta)
        _assert(is_orchestrator_task(supervisor.get_task(parent_id).meta), "parent role")

        children = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=graph
        )
        print({"first_children": children})
        _assert(len(children) == 1, "serial: only first ready")
        child0 = children[0]
        c0 = supervisor.get_task(child0)
        _assert(c0 is not None and is_supervisor_task(c0.meta), "child supervisor")
        supervisor.set_running(child0)
        supervisor.complete(child0)
        supervisor.merge_task_meta(
            child0, {"final_thought": "已写入 notes/p1.txt", "ok": True}
        )

        adv = on_supervisor_finished(
            supervisor,
            child_task_id=child0,
            ok=True,
            final_thought="已写入 notes/p1.txt",
        )
        print({"spawned": adv.get("spawned"), "parent_done": adv.get("parent_completed")})
        _assert(len(adv.get("spawned") or []) == 1, "second node spawned")
        _assert(not adv.get("parent_completed"), "parent not done yet")

        child1 = adv["spawned"][0]
        supervisor.set_running(child1)
        supervisor.complete(child1)
        adv2 = on_supervisor_finished(
            supervisor,
            child_task_id=child1,
            ok=True,
            final_thought="内容为 one",
        )
        print({"parent_completed": adv2.get("parent_completed")})
        _assert(adv2.get("parent_completed"), "parent completed")
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "completed", "parent status")
        g2 = load_graph(parent.meta)
        _assert(g2 is not None and g2.all_completed(), "graph all completed")

    print("== append analyze ==")
    g = build_plan_graph("写 a 然后写 b")
    a1 = analyze_plan_append(
        root_goal="写 a 然后写 b", graph=g, user_text="补充：编码用 utf-8"
    )
    _assert(a1.get("kind") == "no_graph_change", a1)
    a2 = analyze_plan_append(
        root_goal="写 a 然后写 b", graph=g, user_text="请重排顺序"
    )
    _assert(a2.get("kind") == "reorder", a2)

    print("== reorder revision (cancel + reschedule) ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal2 = "先规划：步骤甲写文件，然后步骤乙读取"
        g0 = build_plan_graph(goal2)
        parent_id = supervisor.create_task(
            goal2, meta=bootstrap_parent_meta(graph=g0, title="重排冒烟")
        )
        first = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=g0
        )
        _assert(len(first) == 1, first)
        running = first[0]
        supervisor.set_running(running)
        # 重排：应取消 running，倒序重建未决，再拉起新首节点
        rev = apply_plan_revision(
            supervisor,
            parent_task_id=parent_id,
            kind="reorder",
            instruction="请重排顺序，反过来做",
        )
        print(
            {
                "ok": rev.get("ok"),
                "cancelled": rev.get("cancelled"),
                "spawned": rev.get("spawned"),
                "version": rev.get("version"),
                "reason": rev.get("reason"),
            }
        )
        _assert(rev.get("ok"), rev)
        _assert(running in (rev.get("cancelled") or []), "must cancel old running")
        _assert(len(rev.get("spawned") or []) >= 1, "must spawn new ready")
        old = supervisor.get_task(running)
        _assert(old is not None and old.status == "cancelled", old.status if old else None)
        _assert(bool((old.meta or {}).get("plan_superseded")), "superseded flag")

        # 被淘汰任务收束不得 fail 父计划
        ign = on_supervisor_finished(
            supervisor,
            child_task_id=running,
            ok=False,
            error="cancelled",
        )
        _assert(ign.get("ignored") or ign.get("node_cancelled"), ign)
        _assert(not ign.get("parent_failed"), "reorder cancel must not fail parent")
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "running", parent.status if parent else None)

        g_new = load_graph(parent.meta)
        _assert(g_new is not None and g_new.version >= 2, g_new.version if g_new else None)
        _assert(g_new is not None and len(g_new.retired) >= 1, "retired open nodes")
        # 新图目标顺序应为原未决倒序：乙在前
        goals = [n.goal for n in g_new.nodes if n.status != "completed"]
        _assert(len(goals) >= 2, goals)
        _assert("乙" in goals[0] or "读取" in goals[0], goals[0])

        # 跑完修订后的链
        cur = (rev.get("spawned") or [None])[0]
        while cur:
            supervisor.set_running(cur)
            supervisor.complete(cur)
            out = on_supervisor_finished(
                supervisor, child_task_id=cur, ok=True, final_thought=f"done {cur[:6]}"
            )
            nxt = (out.get("spawned") or [None])[0]
            if out.get("parent_completed"):
                break
            cur = nxt
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "completed", parent.status if parent else None)

    print("== lease_adjust revision ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal3 = "先规划：前台点一下，然后写结果文件"
        g3 = build_plan_graph(goal3)
        # 人为标首节点 foreground + running
        g3.nodes[0].modality_hint = "foreground"
        parent_id = supervisor.create_task(
            goal3, meta=bootstrap_parent_meta(graph=g3, title="租约冒烟")
        )
        kids = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=g3
        )
        _assert(len(kids) == 1, kids)
        supervisor.set_running(kids[0])
        rev = apply_plan_revision(
            supervisor,
            parent_task_id=parent_id,
            kind="lease_adjust",
            instruction="别抢前台，改走后台",
        )
        print({"lease_ok": rev.get("ok"), "cancelled": rev.get("cancelled")})
        _assert(rev.get("ok"), rev)
        _assert(kids[0] in (rev.get("cancelled") or []), "fg→bg should cancel")
        g4 = load_graph(supervisor.get_task(parent_id).meta)
        _assert(g4 is not None, "graph")
        hints = {n.id: n.modality_hint for n in g4.nodes if n.status != "completed"}
        _assert(any(h == "background" for h in hints.values()), hints)

    print("== retry failed node (保留上游 completed) ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal_r = "先规划：写 notes/retry_a.txt 为 one，然后读取并答复"
        gr = build_plan_graph(goal_r)
        _assert(len(gr.nodes) >= 2, "need >=2 nodes")
        n1_id, n2_id = gr.nodes[0].id, gr.nodes[1].id
        parent_id = supervisor.create_task(
            goal_r, meta=bootstrap_parent_meta(graph=gr, title="重试失败节点冒烟")
        )
        kids = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=gr
        )
        _assert(len(kids) == 1, kids)
        child1 = kids[0]
        supervisor.set_running(child1)
        supervisor.complete(child1)
        adv1 = on_supervisor_finished(
            supervisor,
            child_task_id=child1,
            ok=True,
            final_thought="n1 done",
        )
        _assert(len(adv1.get("spawned") or []) == 1, adv1)
        child2 = adv1["spawned"][0]
        supervisor.set_running(child2)
        supervisor.fail(child2, message="Connection error.")
        adv_fail = on_supervisor_finished(
            supervisor,
            child_task_id=child2,
            ok=False,
            error="Connection error.",
        )
        _assert(adv_fail.get("parent_failed"), adv_fail)
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "failed", parent.status if parent else None)
        g_fail = load_graph(parent.meta)
        _assert(g_fail is not None, "graph")
        _assert(g_fail.get(n1_id) is not None and g_fail.get(n1_id).status == "completed", "n1 kept")
        _assert(g_fail.get(n2_id) is not None and g_fail.get(n2_id).status == "failed", "n2 failed")

        _assert(not wants_full_replan_followup("继续"), "continue != full replan")
        _assert(wants_full_replan_followup("整图重规划"), "explicit full replan")

        out = retry_plan_nodes(
            supervisor,
            parent_task_id=parent_id,
            node_ids=[n2_id],
            reason="重试 n2",
        )
        print(
            {
                "ok": out.get("ok"),
                "retried": out.get("retried"),
                "spawned": out.get("spawned"),
                "parent_status": out.get("parent_status"),
            }
        )
        _assert(out.get("ok"), out)
        _assert(n2_id in (out.get("retried") or []), out.get("retried"))
        _assert(len(out.get("spawned") or []) == 1, out.get("spawned"))
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "running", parent.status if parent else None)
        g_retry = load_graph(parent.meta)
        _assert(g_retry is not None, "graph after retry")
        _assert(
            g_retry.get(n1_id) is not None and g_retry.get(n1_id).status == "completed",
            "n1 still completed after retry",
        )
        _assert(
            g_retry.get(n2_id) is not None
            and g_retry.get(n2_id).status == "running",
            g_retry.get(n2_id).status if g_retry.get(n2_id) else None,
        )
        child2b = out["spawned"][0]
        supervisor.set_running(child2b)
        supervisor.complete(child2b)
        adv_ok = on_supervisor_finished(
            supervisor,
            child_task_id=child2b,
            ok=True,
            final_thought="n2 ok after retry",
        )
        cur = (adv_ok.get("spawned") or [None])[0]
        while cur:
            supervisor.set_running(cur)
            supervisor.complete(cur)
            nxt = on_supervisor_finished(
                supervisor, child_task_id=cur, ok=True, final_thought=f"done {cur[:6]}"
            )
            if nxt.get("parent_completed"):
                break
            cur = (nxt.get("spawned") or [None])[0]
        parent = supervisor.get_task(parent_id)
        _assert(
            parent is not None and parent.status == "completed",
            parent.status if parent else None,
        )
        g_end = load_graph(parent.meta)
        _assert(g_end is not None and g_end.get(n1_id).status == "completed", "n1 preserved")
        _assert(g_end is not None and g_end.all_completed(), "all completed after retry")

    print("== full replan (重新执行) ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal4 = "先规划：写 notes/r1.txt 为 one，然后读取并答复"
        g4 = build_plan_graph(goal4)
        parent_id = supervisor.create_task(
            goal4, meta=bootstrap_parent_meta(graph=g4, title="整图重规划冒烟")
        )
        kids = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=g4
        )
        _assert(len(kids) >= 1, kids)
        old_child = kids[0]
        supervisor.set_running(old_child)
        # 模拟取消父计划后「重新执行」
        supervisor.cancel(old_child)
        supervisor.cancel(parent_id)
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "cancelled", parent.status if parent else None)

        out = replan_full_graph(
            supervisor,
            parent_task_id=parent_id,
            root_goal=goal4,
            reason="重新执行",
        )
        print(
            {
                "ok": out.get("ok"),
                "version": out.get("version"),
                "cancelled": out.get("cancelled"),
                "spawned": out.get("spawned"),
            }
        )
        _assert(out.get("ok"), out)
        _assert(int(out.get("version") or 0) >= 2, out.get("version"))
        _assert(len(out.get("spawned") or []) >= 1, "must spawn after full replan")
        old = supervisor.get_task(old_child)
        _assert(old is not None and old.status == "cancelled", old.status if old else None)
        _assert(bool((old.meta or {}).get("plan_superseded")), "old child superseded")
        parent = supervisor.get_task(parent_id)
        _assert(parent is not None and parent.status == "running", parent.status if parent else None)
        g_new = load_graph(parent.meta)
        _assert(g_new is not None and g_new.version >= 2, g_new.version if g_new else None)
        _assert(g_new is not None and len(g_new.retired) >= 1, "old nodes retired")
        # 新图活跃节点无 cancelled 残留，且可就绪/已 running
        _assert(
            all(n.status in {"pending", "ready", "running"} for n in g_new.nodes),
            [n.status for n in g_new.nodes],
        )

    print("OK demo_plan_mode")


if __name__ == "__main__":
    main()
