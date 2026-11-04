# term:编排脑  doc:doc/功能/复杂任务计划模式/说明.md
"""计划修订：reorder / lease_adjust 出修订图（保留已完成，淘汰未决并重建）。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode


@dataclass
class PlanRevisionPlan:
    """编排脑修订方案（尚未落库/cancel）。"""

    kind: str
    graph: PlanGraph
    cancel_task_ids: list[str] = field(default_factory=list)
    keep_running: list[str] = field(default_factory=list)  # node ids still running
    dispatch_by_task: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    source: str = "heuristic"


def _mock_enabled() -> bool:
    return os.environ.get("FAWAI_BRAIN_MOCK", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def build_revision(
    graph: PlanGraph,
    *,
    kind: str,
    instruction: str,
    root_goal: str = "",
) -> PlanRevisionPlan:
    """根据追加意图生成修订方案。reorder 重建未决子图；lease_adjust 调 modality。"""
    text = (instruction or "").strip()
    if kind == "lease_adjust":
        return _revise_lease(graph, instruction=text)
    if kind == "reorder":
        if _mock_enabled():
            return _heuristic_reorder(graph, instruction=text)
        try:
            out = _llm_reorder(graph, instruction=text, root_goal=root_goal)
            if out is not None:
                return out
        except Exception:  # noqa: BLE001
            pass
        plan = _heuristic_reorder(graph, instruction=text)
        plan.source = "fallback"
        return plan
    return PlanRevisionPlan(
        kind=kind,
        graph=graph,
        reason="无需改图",
        source="noop",
    )


def _heuristic_reorder(graph: PlanGraph, *, instruction: str) -> PlanRevisionPlan:
    """
    稳定启发式：
    - 已 completed 原样保留；
    - 未决（pending/ready/running/failed）按目标列表重排；
    - 「反过来/倒序/换顺序」→ 反转未决目标链；
    - 「先做 X」→ 把含 X 的未决节点提前；
    - 默认：反转未决链（用户说了重排）。
    所有原 running 一律 cancel 后按新图拉起（避免旧依赖边残留）。
    """
    completed = [n for n in graph.nodes if n.status == "completed"]
    open_nodes = [n for n in graph.nodes if n.status != "completed"]
    cancel_ids = [
        str(n.task_id)
        for n in open_nodes
        if n.status == "running" and n.task_id
    ]

    goals = [n.goal for n in open_nodes]
    if not goals:
        return PlanRevisionPlan(
            kind="reorder",
            graph=graph,
            reason="无未决节点可重排",
            source="heuristic",
        )

    if any(k in instruction for k in ("反过来", "倒序", "反过来做", "顺序反过来")):
        goals = list(reversed(goals))
        reason = "未决节点倒序"
    elif "先做" in instruction or "优先" in instruction:
        goals = _prefer_goal_first(goals, instruction)
        reason = "按「先做/优先」调整未决顺序"
    elif any(k in instruction for k in ("重排", "换顺序", "调整顺序")):
        goals = list(reversed(goals))
        reason = "重排：未决倒序"
    else:
        goals = list(reversed(goals))
        reason = "重排：默认未决倒序"

    ver = int(graph.version) + 1
    new_open: list[PlanNode] = []
    for i, g in enumerate(goals):
        nid = f"r{ver}_{i+1}"
        if i > 0:
            deps = [f"r{ver}_{i}"]
            consumes = [f"out_r{ver}_{i}"]
        elif completed:
            deps = [completed[-1].id]
            consumes = list(completed[-1].produces[:1])
        else:
            deps = []
            consumes = []
        new_open.append(
            PlanNode(
                id=nid,
                goal=g,
                depends_on=deps,
                produces=[f"out_{nid}"],
                consumes=consumes,
                stop_when=f"完成：{g[:40]}",
                status="pending",
            )
        )

    retired = list(graph.retired)
    for n in open_nodes:
        nn = PlanNode.from_dict(n.to_dict())
        nn.status = "cancelled"
        nn.artifact = {**(nn.artifact or {}), "cancel_reason": "reorder_superseded"}
        retired.append(nn)

    new_graph = PlanGraph(
        plan_id=graph.plan_id,
        root_goal=graph.root_goal,
        nodes=[PlanNode.from_dict(n.to_dict()) for n in completed] + new_open,
        artifacts=dict(graph.artifacts),
        version=ver,
        source="revise_heuristic",
        retired=retired,
    )
    return PlanRevisionPlan(
        kind="reorder",
        graph=new_graph,
        cancel_task_ids=cancel_ids,
        keep_running=[],
        reason=reason,
        source="heuristic" if _mock_enabled() else "fallback",
    )


def _prefer_goal_first(goals: list[str], instruction: str) -> list[str]:
    """把指令里点名的片段对应目标提前。"""
    # 「先做 XXX」取后文关键词
    m = re.search(r"先做\s*[：:]?\s*(.+)$", instruction)
    key = (m.group(1) if m else instruction).strip()
    key = re.split(r"[，。；;\n]", key)[0].strip()[:40]
    if not key:
        return list(reversed(goals))
    hit = [g for g in goals if key in g]
    rest = [g for g in goals if key not in g]
    if not hit:
        # 模糊：任一目标含指令中较长 token
        tokens = [t for t in re.split(r"\s+", key) if len(t) >= 2]
        for t in tokens:
            hit = [g for g in goals if t in g]
            rest = [g for g in goals if t not in g]
            if hit:
                break
    if not hit:
        return list(reversed(goals))
    return hit + rest


def _revise_lease(graph: PlanGraph, *, instruction: str) -> PlanRevisionPlan:
    """租约调整：更新未决/运行中节点 modality_hint，必要时取消抢前台的节点后重建同目标。"""
    text = instruction
    want_bg = any(k in text for k in ("别抢前台", "不要前台", "后台", "取消前台"))
    want_fg = any(k in text for k in ("需要前台", "用界面", "GUI", "键鼠"))
    hint = "background" if want_bg else ("foreground" if want_fg else "")
    cancel_ids: list[str] = []
    keep_running: list[str] = []
    dispatch: dict[str, str] = {}
    ver = int(graph.version) + 1

    nodes: list[PlanNode] = []
    for n in graph.nodes:
        nn = PlanNode.from_dict(n.to_dict())
        if nn.status == "completed":
            nodes.append(nn)
            continue
        if hint:
            nn.modality_hint = hint
        if nn.status == "running" and nn.task_id:
            # 前台→后台：取消后以同目标 pending 重拉，避免持有 foreground_lease 空转
            if want_bg and n.modality_hint in {"foreground", "mixed"}:
                cancel_ids.append(str(nn.task_id))
                nn.status = "pending"
                nn.task_id = None
                nn.artifact = {
                    **(nn.artifact or {}),
                    "cancel_reason": "lease_adjust_requeue",
                }
            else:
                keep_running.append(nn.id)
                dispatch[str(nn.task_id)] = (
                    f"【租约调整】{text[:120]}；modality_hint={nn.modality_hint}"
                )
        nodes.append(nn)

    new_graph = PlanGraph(
        plan_id=graph.plan_id,
        root_goal=graph.root_goal,
        nodes=nodes,
        artifacts=dict(graph.artifacts),
        version=ver,
        source="revise_lease",
        retired=list(graph.retired),
    )
    return PlanRevisionPlan(
        kind="lease_adjust",
        graph=new_graph,
        cancel_task_ids=cancel_ids,
        keep_running=keep_running,
        dispatch_by_task=dispatch,
        reason=f"租约调整 hint={hint or 'unchanged'}",
        source="heuristic" if _mock_enabled() else "heuristic",
    )


def _llm_reorder(
    graph: PlanGraph,
    *,
    instruction: str,
    root_goal: str,
) -> PlanRevisionPlan | None:
    from fawai_avatar.llm.client import chat

    completed = [n.to_dict() for n in graph.nodes if n.status == "completed"]
    open_nodes = [
        {"id": n.id, "goal": n.goal, "status": n.status, "task_id": n.task_id}
        for n in graph.nodes
        if n.status != "completed"
    ]
    system = (
        "你是操作分身的编排脑，负责重排未决子任务。"
        "已完成节点必须保留且不要改 goal。"
        "只输出 JSON："
        '{"reason":"≤40字","open_nodes":[{"id":"r1","goal":"...","depends_on":[],'
        '"produces":["out_r1"],"consumes":[],"stop_when":"...","modality_hint":"background"}],'
        '"cancel_running_ids":["旧节点id"],"keep_running_ids":[]}。'
        "规则：open_nodes 覆盖全部未决工作（可换 id）；depends_on 可引用已完成 id 或本列表 id；"
        "原 running 若不在 keep_running_ids 则列入 cancel_running_ids；"
        "不要输出 markdown。"
    )
    user = (
        f"总目标:\n{(root_goal or graph.root_goal)[:800]}\n"
        f"用户重排要求:\n{instruction[:800]}\n"
        f"已完成节点:\n{json.dumps(completed, ensure_ascii=False)[:1200]}\n"
        f"未决节点:\n{json.dumps(open_nodes, ensure_ascii=False)[:1200]}\n"
    )
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=None,
        tool_choice=None,
    )
    if not resp.get("ok"):
        return None
    from fawai_avatar.brain.orchestrator import _parse_json_object

    parsed = _parse_json_object(str(resp.get("content") or ""))
    if not parsed or not isinstance(parsed.get("open_nodes"), list):
        return None

    ver = int(graph.version) + 1
    frozen = [PlanNode.from_dict(n.to_dict()) for n in graph.nodes if n.status == "completed"]
    frozen_ids = {n.id for n in frozen}
    new_open: list[PlanNode] = []
    for i, raw in enumerate(parsed["open_nodes"]):
        if not isinstance(raw, dict):
            continue
        g = str(raw.get("goal") or "").strip()
        if not g:
            continue
        nid = str(raw.get("id") or f"r{ver}_{i+1}").strip()
        deps = [str(x) for x in (raw.get("depends_on") or []) if str(x).strip()]
        produces = [str(x) for x in (raw.get("produces") or []) if str(x).strip()]
        if not produces:
            produces = [f"out_{nid}"]
        new_open.append(
            PlanNode(
                id=nid,
                goal=g,
                depends_on=deps,
                produces=produces,
                consumes=[
                    str(x) for x in (raw.get("consumes") or []) if str(x).strip()
                ],
                stop_when=str(raw.get("stop_when") or f"完成：{g[:40]}"),
                modality_hint=str(raw.get("modality_hint") or "background"),
                status="pending",
            )
        )
    if not new_open and open_nodes:
        return None

    keep_ids = {
        str(x) for x in (parsed.get("keep_running_ids") or []) if str(x).strip()
    }
    cancel_node_ids = {
        str(x) for x in (parsed.get("cancel_running_ids") or []) if str(x).strip()
    }
    # 校验 keep：仅当新图仍有同 id 且 goal 兼容时才真 keep
    new_ids = {n.id for n in new_open}
    cancel_task_ids: list[str] = []
    keep_running: list[str] = []
    for n in graph.nodes:
        if n.status != "running" or not n.task_id:
            continue
        if n.id in keep_ids and n.id in new_ids:
            # 把新图中该节点标为 running 继承 task
            keep_running.append(n.id)
        else:
            cancel_task_ids.append(str(n.task_id))
            cancel_node_ids.add(n.id)

    # 继承 keep 的 running 状态
    for nn in new_open:
        if nn.id in keep_running:
            old = graph.get(nn.id)
            if old and old.task_id:
                nn.status = "running"
                nn.task_id = old.task_id

    retired = list(graph.retired)
    for n in graph.nodes:
        if n.status == "completed":
            continue
        if n.id in new_ids and n.id in keep_running:
            continue
        if n.id in new_ids and n.status != "running":
            # 被同 id 新定义替换的 pending — 不进 retired 重复
            if n.status in {"pending", "ready"}:
                continue
        nn = PlanNode.from_dict(n.to_dict())
        nn.status = "cancelled"
        nn.artifact = {**(nn.artifact or {}), "cancel_reason": "reorder_superseded"}
        retired.append(nn)

    # 去重 retired by id+task
    new_graph = PlanGraph(
        plan_id=graph.plan_id,
        root_goal=graph.root_goal,
        nodes=frozen + new_open,
        artifacts=dict(graph.artifacts),
        version=ver,
        source="revise_llm",
        retired=retired,
    )
    # 校验 depends 不指向未知 id
    known = {n.id for n in new_graph.nodes}
    for n in new_graph.nodes:
        n.depends_on = [d for d in n.depends_on if d in known]
        if n.status == "completed":
            continue
        # consumes 宽松：缺产物时仍可 pending，就绪集再卡
        _ = frozen_ids

    return PlanRevisionPlan(
        kind="reorder",
        graph=new_graph,
        cancel_task_ids=list(dict.fromkeys(cancel_task_ids)),
        keep_running=keep_running,
        reason=str(parsed.get("reason") or "LLM 重排")[:80],
        source="llm",
    )
