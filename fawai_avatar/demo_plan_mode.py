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
    _PARENT_INLINE_MAX,
    _parent_summary,
    _store_user_summary,
    apply_plan_revision,
    bootstrap_parent_meta,
    is_orchestrator_task,
    is_supervisor_task,
    load_graph,
    on_supervisor_finished,
    remove_plan_nodes,
    replan_full_graph,
    resume_plan_child_node,
    retry_plan_nodes,
    spawn_ready_children,
    wants_full_replan_followup,
)
from fawai_avatar.runtime.checkpoint import (
    CHECKPOINT_KEY,
    excise_step_from_context,
    load_checkpoint,
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

    print("== shared facts pinned on the graph ==")
    from fawai_avatar.brain.orchestrator import format_supervisor_goal
    from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode
    from fawai_avatar.brain.shared_facts import (
        apply_shared_facts,
        fill_shared_facts_from_node,
    )

    shared = PlanGraph(
        plan_id="shared",
        root_goal="查看训练状态",
        nodes=[
            PlanNode(id="n1", goal="读连接配置", produces=["out_n1"]),
            PlanNode(id="n2", goal="查训练进程", produces=["out_n2"]),
            PlanNode(id="n3", goal="读训练日志", produces=["out_n3"]),
        ],
    )
    apply_shared_facts(
        shared,
        [
            {
                "key": "ssh_endpoint",
                "producer": "n1",
                "consumers": ["n2", "n3"],
                "what": "host 与 port",
            }
        ],
    )
    _assert("ssh_endpoint" in shared.nodes[0].produces, "producer key")
    _assert(shared.nodes[1].depends_on == ["n1"], "n2 waits n1")
    _assert(shared.nodes[2].depends_on == ["n1"], "n3 waits n1")
    _assert("ssh_endpoint" in shared.nodes[2].consumes, "n3 consumes")
    _assert(shared.ready_node_ids() == ["n1"], "only producer ready")
    host_line = "host=10.0.0.8 port=22 " + ("h" * 180)
    fill_shared_facts_from_node(
        shared,
        "n1",
        host_line + "\npassword: hunter2",
        data_dir=str(Path(tempfile.mkdtemp())),
        task_id="parent-shared",
    )
    shared.nodes[0].status = "completed"
    goal_n2 = format_supervisor_goal(
        root_goal=shared.root_goal,
        node=shared.nodes[1],
        artifacts=shared.artifacts,
        shared_facts=list(shared.shared_facts),
    )
    _assert("10.0.0.8" in goal_n2, "host kept")
    _assert("hunter2" not in goal_n2, "password not copied")
    _assert(len(host_line) > 120, "sample longer than old clip")
    _assert("10.0.0.8" in goal_n2 and goal_n2.find("10.0.0.8") >= 0, "not clipped away")
    restored = PlanGraph.from_dict(shared.to_dict())
    _assert(restored.shared_facts[0].key == "ssh_endpoint", "roundtrip key")
    _assert("n1" in restored.nodes[1].depends_on, "roundtrip depends")
    print("[PASS] shared facts")

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
        p_thought = str((parent.meta or {}).get("final_thought") or "")
        _assert("计划完成" in p_thought, p_thought)
        _assert("内容为 one" in p_thought, "末节点结论文须完整进父 final_thought")

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

    print("== soft append session fallback (all completed) ==")
    from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode
    from fawai_avatar.brain.plan_scheduler import soft_append_needs_session_fallback

    g_done = PlanGraph(
        plan_id="done",
        root_goal="已完成图",
        nodes=[
            PlanNode(id="n1", goal="a", status="completed"),
            PlanNode(id="n2", goal="b", status="completed", depends_on=["n1"]),
        ],
    )
    _assert(
        soft_append_needs_session_fallback(
            g_done, running_targets=[], spawned=[]
        ),
        "all completed must fallback",
    )
    _assert(
        not soft_append_needs_session_fallback(
            g_done, running_targets=["child"], spawned=[]
        ),
        "running targets skip fallback",
    )
    g_active = PlanGraph(
        plan_id="act",
        root_goal="未决",
        nodes=[PlanNode(id="n1", goal="a", status="ready")],
    )
    _assert(
        not soft_append_needs_session_fallback(
            g_active, running_targets=[], spawned=[]
        ),
        "active nodes must not empty-fallback",
    )
    # 默认关计划模式
    os.environ["FAWAI_PLAN_MODE"] = "0"
    d_off = decide_complexity("先规划：写 a 然后写 b")
    _assert(d_off.get("enter") is False and d_off.get("source") == "off", d_off)
    # 会话 plan_auto 覆盖环境关：可开
    d_ui_on = decide_complexity(
        "先规划：写 a 然后写 b", meta={"plan_auto": True}
    )
    _assert(d_ui_on.get("source") != "off", d_ui_on)
    os.environ["FAWAI_PLAN_MODE"] = "auto"
    # 会话 plan_auto=false 覆盖环境开：强制关
    d_ui_off = decide_complexity(
        "先规划：写 a 然后写 b", meta={"plan_auto": False}
    )
    _assert(
        d_ui_off.get("enter") is False and d_ui_off.get("source") == "off",
        d_ui_off,
    )

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
        _assert(
            not wants_full_replan_followup(
                "最后一次训练应该使用best.pt，不要使用last.pt"
            ),
            "constraint append != full replan",
        )
        _assert(wants_full_replan_followup("整图重规划"), "explicit full replan")

        # 修订互斥：模拟 in-flight 时后到者须失败
        from fawai_avatar.brain import plan_scheduler as _ps

        _assert(_ps._begin_plan_revision(parent_id, "test_lock") is None, "lock acquire")
        busy = retry_plan_nodes(
            supervisor,
            parent_task_id=parent_id,
            node_ids=[n2_id],
            reason="should be blocked",
        )
        _assert(not busy.get("ok"), busy)
        _assert("进行中" in str(busy.get("reason") or ""), busy.get("reason"))
        _ps._end_plan_revision(parent_id)

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

    print("== resume plan child from step (复用监管脑) ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal_rs = "先规划：写 notes/rs.txt 为 one，然后读取并答复"
        g_rs = build_plan_graph(goal_rs)
        parent_rs = supervisor.create_task(
            goal_rs, meta=bootstrap_parent_meta(graph=g_rs, title="步骤恢复冒烟")
        )
        kids_rs = spawn_ready_children(
            supervisor, parent_task_id=parent_rs, graph=g_rs
        )
        _assert(len(kids_rs) >= 1, kids_rs)
        child_rs = kids_rs[0]
        child_meta = supervisor.get_task(child_rs).meta or {}
        node_rs = str(child_meta.get("plan_node_id") or "")
        _assert(bool(node_rs), "plan_node_id")
        supervisor.set_running(child_rs)
        supervisor.fail(child_rs, message="step fail")
        adv_rs = on_supervisor_finished(
            supervisor,
            child_task_id=child_rs,
            ok=False,
            error="step fail",
        )
        _assert(adv_rs.get("parent_failed"), adv_rs)
        parent = supervisor.get_task(parent_rs)
        _assert(parent is not None and parent.status == "failed", parent.status if parent else None)

        # 复用同一监管脑：图节点 running、父计划重开；不 spawn
        out_rs = resume_plan_child_node(supervisor, child_task_id=child_rs)
        print(
            {
                "ok": out_rs.get("ok"),
                "plan_node_id": out_rs.get("plan_node_id"),
                "parent_status": out_rs.get("parent_status"),
            }
        )
        _assert(out_rs.get("ok"), out_rs)
        _assert(out_rs.get("plan_node_id") == node_rs, out_rs.get("plan_node_id"))
        parent = supervisor.get_task(parent_rs)
        _assert(parent is not None and parent.status == "running", parent.status if parent else None)
        g_live = load_graph(parent.meta)
        _assert(g_live is not None, "graph")
        n_live = g_live.get(node_rs)
        _assert(n_live is not None and n_live.status == "running", n_live.status if n_live else None)
        _assert(n_live.task_id == child_rs, n_live.task_id)
        # 子任务仍须自行 requeue（枢纽 resume_from_step 负责）；此处模拟续跑成功
        _assert(supervisor.requeue_task(child_rs, reopen=True), "requeue child")
        supervisor.set_running(child_rs)
        supervisor.complete(child_rs)
        adv_done = on_supervisor_finished(
            supervisor,
            child_task_id=child_rs,
            ok=True,
            final_thought="ok after step resume",
        )
        cur = (adv_done.get("spawned") or [None])[0]
        while cur:
            supervisor.set_running(cur)
            supervisor.complete(cur)
            nxt = on_supervisor_finished(
                supervisor, child_task_id=cur, ok=True, final_thought=f"done {cur[:6]}"
            )
            if nxt.get("parent_completed"):
                break
            cur = (nxt.get("spawned") or [None])[0]
        parent = supervisor.get_task(parent_rs)
        _assert(
            parent is not None and parent.status == "completed",
            parent.status if parent else None,
        )

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

    print("== remove_plan_nodes ==")
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        supervisor = Supervisor(str(data_dir))
        goal_rm = "先规划：写 notes/rm1.txt 为 one，然后读取并答复，再写 notes/rm2.txt"
        g_rm = build_plan_graph(goal_rm)
        _assert(len(g_rm.nodes) >= 2, "need >=2 nodes to remove one")
        parent_rm = supervisor.create_task(
            goal_rm, meta=bootstrap_parent_meta(graph=g_rm, title="删节点冒烟")
        )
        kids_rm = spawn_ready_children(
            supervisor, parent_task_id=parent_rm, graph=g_rm
        )
        _assert(len(kids_rm) >= 1, kids_rm)
        # 标第二节点可删（未启动或 failed 均可）
        drop_id = g_rm.nodes[-1].id
        keep_ids = {n.id for n in g_rm.nodes if n.id != drop_id}
        out_rm = remove_plan_nodes(
            supervisor,
            parent_task_id=parent_rm,
            node_ids=[drop_id],
            reason="冒烟删除",
        )
        print(
            {
                "ok": out_rm.get("ok"),
                "removed": out_rm.get("removed"),
                "active": [
                    n["id"]
                    for n in ((out_rm.get("graph") or {}).get("nodes") or [])
                ],
            }
        )
        _assert(out_rm.get("ok"), out_rm)
        _assert(drop_id in (out_rm.get("removed") or []), out_rm.get("removed"))
        g_after = load_graph(supervisor.get_task(parent_rm).meta)
        _assert(g_after is not None, "graph after remove")
        _assert(drop_id not in {n.id for n in g_after.nodes}, [n.id for n in g_after.nodes])
        _assert(keep_ids <= {n.id for n in g_after.nodes}, keep_ids)
        _assert(any(n.id == drop_id for n in g_after.retired), "retired")
        # 下游依赖剥离
        for n in g_after.nodes:
            _assert(drop_id not in n.depends_on, n.depends_on)
        # 禁止删光
        deny = remove_plan_nodes(
            supervisor,
            parent_task_id=parent_rm,
            node_ids=[n.id for n in g_after.nodes],
            reason="试图删光",
        )
        _assert(not deny.get("ok"), deny)
        _assert("不能删光" in str(deny.get("reason") or ""), deny.get("reason"))

    print("== excise_step_from_context ==")
    meta_ex = {
        CHECKPOINT_KEY: {
            "version": 1,
            "run_id": "smoke",
            "next_step": 3,
            "history": [],
            "traces": [
                {"step": 0, "plan": {"thought": "a"}, "results": []},
                {"step": 1, "plan": {"thought": "b-noise"}, "results": []},
                {"step": 2, "plan": {"thought": "c"}, "results": []},
            ],
            "replan_count": 0,
            "reflect_count": 0,
            "stagnation_count": 0,
            "fingerprint_fails": {},
            "prior_alternate_fps": [],
            "step_fp_counts": {"1": 2},
            "prior_judged_step_fps": [],
            "verify_results": [],
            "write_set": [],
            "saw_code_check_ok": False,
            "saw_architecture_ok": False,
            "explore_only_streak": 0,
            "explore_mutate_forced": False,
            "explore_behavior_armed": False,
            "remote_idle_streak": 0,
            "remote_method_forced": False,
            "read_path_counts": {},
            "read_windows": {},
            "locate_anchors": [],
            "post_locate_streak": 0,
            "locate_mutate_forced": False,
            "wide_grep_ban_paths": [],
            "wide_grep_ban_patterns": {},
            "awaiting_user_answer": False,
            "pending_observe_kinds": None,
            "last_reflection": None,
        },
        "steps": [
            {"step": 0, "plan": {"thought": "a"}, "results": []},
            {"step": 1, "plan": {"thought": "b-noise"}, "results": []},
            {"step": 2, "plan": {"thought": "c"}, "results": []},
        ],
    }
    patch = excise_step_from_context(meta_ex, step=1)
    _assert(patch.get("excised_step") == 1, patch)
    ck = load_checkpoint({CHECKPOINT_KEY: patch[CHECKPOINT_KEY]})
    _assert(ck is not None, "checkpoint after excise")
    steps_left = [
        int(t.get("step")) for t in (ck.get("traces") or []) if isinstance(t, dict)
    ]
    _assert(steps_left == [0, 2], steps_left)
    meta_steps_left = [
        int(t.get("step")) for t in (patch.get("steps") or []) if isinstance(t, dict)
    ]
    _assert(meta_steps_left == [0, 2], meta_steps_left)
    hist = " ".join(
        str(h.get("content") or "") for h in (ck.get("history") or []) if isinstance(h, dict)
    )
    _assert("b-noise" not in hist, hist)
    _assert("a" in hist and "c" in hist, hist)
    _assert(ck.get("step_fp_counts") == {}, ck.get("step_fp_counts"))

    print("== parent summary keeps last-node conclusion intact ==")
    from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode

    long_n3 = (
        "**结论：执行失败。**\n\n## 校验结果\n\n"
        "| 维度 | 上游草稿报告 | 本次终稿 |\n|---|---|---|\n"
        "| 执行状态 | 称部分成功 | **完全失败** |\n"
        "| 根因 | 笼统环境隔离 | ModuleNotFoundError: paramiko |\n"
    )
    # 旧行为会把各节点 summary[:120] 拼进结论文，在「| 维度 | 上游」处截断
    g_sum = PlanGraph(
        plan_id="sum",
        root_goal="分析任务",
        nodes=[
            PlanNode(
                id="n1",
                goal="取证",
                artifact={
                    "summary": "已完成任务的数据采集。"
                    + ("x" * 200)
                    + "\n\n### detailed_logs\n- 执行环境：缺依赖，但远程 Aut"
                },
            ),
            PlanNode(
                id="n2",
                goal="写稿",
                artifact={"summary": "草稿已落盘。", "user_summary": "草稿已落盘。"},
            ),
            PlanNode(
                id="n3",
                goal="校验",
                artifact={
                    "summary": long_n3[:800],
                    "user_summary": long_n3,
                },
            ),
        ],
    )
    summary = _parent_summary(g_sum)
    _assert("计划完成（3 节点）" in summary, summary)
    _assert("| 维度 | 上游草稿报告 | 本次终稿 |" in summary, summary)
    _assert("ModuleNotFoundError: paramiko" in summary, summary)
    _assert("但远程 Aut" not in summary, "索引行不得残留旧硬切半截")
    # 索引行是首行预览，不应把 n1 长正文硬切拼进结论文主体
    _assert(summary.count("### detailed_logs") == 0, summary)

    print("== parent conclusion spills long body but does not truncate ==")
    with tempfile.TemporaryDirectory() as td:
        huge = ("完整结论文段落。\n" * 1200) + "END_MARKER_NO_TRUNC"
        _assert(len(huge) > _PARENT_INLINE_MAX, len(huge))
        stored = _store_user_summary(
            huge, data_dir=td, task_id="parent-long", node_id="n9"
        )
        _assert(stored.get("user_summary_path"), stored)
        g_long = PlanGraph(
            plan_id="long",
            root_goal="长结论",
            nodes=[
                PlanNode(id="n9", goal="收尾", artifact=stored),
            ],
        )
        out = _parent_summary(g_long)
        _assert("END_MARKER_NO_TRUNC" in out, out[-80:])
        _assert(out.count("完整结论文段落。") >= 1200, "须读回落盘全文，不得按固定字数硬切")

    print("OK demo_plan_mode")


if __name__ == "__main__":
    main()
