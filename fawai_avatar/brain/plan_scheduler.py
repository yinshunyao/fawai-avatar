# term:复杂任务计划模式  doc:doc/功能/复杂任务计划模式/说明.md
"""计划模式规则调度：就绪集拉起监管脑任务、终态回写、父计划收束。"""
from __future__ import annotations

import threading
import time
from typing import Any

from fawai_avatar.brain.orchestrator import format_supervisor_goal
from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.task.title import (
    commit_task_title,
    normalize_task_title,
    suggest_task_title,
)

META_PLAN_GRAPH = "plan_graph"
META_PLAN_ROLE = "plan_role"
META_PLAN_ID = "plan_id"
META_PLAN_NODE_ID = "plan_node_id"
META_PLAN_PARENT_ID = "plan_parent_task_id"
META_PLAN_AUTH = "plan_auth"

# 同一父计划上 full_replan / retry / apply_revision 互斥，避免连点双拉同节点
_REVISION_GUARD = threading.RLock()
_REVISION_INFLIGHT: dict[str, str] = {}


def _begin_plan_revision(parent_task_id: str, kind: str) -> str | None:
    """若已有修订进行中返回拒因文案，否则登记并返回 None。"""
    pid = (parent_task_id or "").strip()
    if not pid:
        return "无父计划 id"
    with _REVISION_GUARD:
        cur = _REVISION_INFLIGHT.get(pid)
        if cur:
            return f"计划修订进行中（{cur}），请稍后再试"
        _REVISION_INFLIGHT[pid] = kind
        return None


def _end_plan_revision(parent_task_id: str) -> None:
    pid = (parent_task_id or "").strip()
    if not pid:
        return
    with _REVISION_GUARD:
        _REVISION_INFLIGHT.pop(pid, None)


def is_orchestrator_task(meta: dict[str, Any] | None) -> bool:
    return str((meta or {}).get(META_PLAN_ROLE) or "") == "orchestrator"


def is_supervisor_task(meta: dict[str, Any] | None) -> bool:
    return str((meta or {}).get(META_PLAN_ROLE) or "") == "supervisor"


# 新建已建卡、编排脑判定/出图尚未完成；禁止拉 Worker / 追问续跑抢跑
META_AWAITING_PLAN = "awaiting_plan"


def is_awaiting_plan(meta: dict[str, Any] | None) -> bool:
    return bool((meta or {}).get(META_AWAITING_PLAN))


def session_publish_task_id(
    supervisor: Supervisor, task_id: str
) -> tuple[str, dict[str, Any]]:
    """
    会话可见 task_id：监管脑子任务归并到父计划，避免侧栏出现独立对话。

    返回 (publish_task_id, extra_meta)。
    """
    task = supervisor.get_task(task_id)
    if task is None or not is_supervisor_task(task.meta):
        return task_id, {}
    parent = str((task.meta or {}).get(META_PLAN_PARENT_ID) or "").strip()
    if not parent:
        return task_id, {}
    extra = {
        "plan_child_task_id": task_id,
        "plan_node_id": str((task.meta or {}).get(META_PLAN_NODE_ID) or ""),
        "mirrored_from_plan_child": True,
    }
    return parent, extra


def promote_title_to_plan_parent(
    supervisor: Supervisor,
    child_task_id: str,
    title: str,
    *,
    source: str,
) -> str:
    """
    监管脑 LLM 短标题提升到父计划会话 meta.title。
    父侧仍是空/启发式时写入；已有 llm 正式名则不动。
    """
    task = supervisor.get_task(child_task_id)
    if task is None or not is_supervisor_task(task.meta):
        return ""
    parent_id = str((task.meta or {}).get(META_PLAN_PARENT_ID) or "").strip()
    if not parent_id:
        return ""
    return commit_task_title(supervisor, parent_id, title, source=source)


def best_child_llm_title(supervisor: Supervisor, parent_task_id: str) -> str:
    """父计划仍无正式短标题时，取最早子任务的 llm 标题作会话名。"""
    for cid in list_plan_child_ids(supervisor, parent_task_id):
        child = supervisor.get_task(cid)
        if child is None:
            continue
        meta = dict(child.meta or {})
        if str(meta.get("title_source") or "") != "llm":
            continue
        named = normalize_task_title(str(meta.get("title") or ""))
        if named:
            return named
    return ""


def resolve_session_title(
    supervisor: Supervisor | None,
    task: Any,
) -> tuple[str, str]:
    """
    会话列表/顶栏用的短标题真源：优先本任务 meta.title；
    计划父任务若仍是启发式，回填首个子任务 llm 标题并落盘。
    """
    meta = dict(getattr(task, "meta", None) or {})
    title = normalize_task_title(str(meta.get("title") or ""))
    source = str(meta.get("title_source") or "") or ("heuristic" if title else "")
    goal = str(getattr(task, "goal", None) or "")
    if title and source == "llm":
        return title, source
    if (
        supervisor is not None
        and is_orchestrator_task(meta)
        and (not title or source != "llm")
    ):
        promoted = best_child_llm_title(supervisor, str(getattr(task, "task_id", "") or ""))
        if promoted:
            written = commit_task_title(
                supervisor,
                str(getattr(task, "task_id", "") or ""),
                promoted,
                source="llm",
            )
            if written:
                return written, "llm"
            return promoted, "llm"
    if title:
        return title, source or "heuristic"
    return suggest_task_title(goal), "heuristic"


def plan_graph_progress_meta(graph: PlanGraph | None) -> dict[str, Any]:
    """推给前端画流程图的紧凑 meta。"""
    if graph is None:
        return {}
    return {
        "phase": "plan_graph",
        "plan_graph": graph.to_dict(),
        "plan_id": graph.plan_id,
        "plan_version": graph.version,
    }


def list_plan_child_ids(supervisor: Supervisor, parent_task_id: str) -> list[str]:
    """父计划下监管脑任务 id（图上 task_id + meta 扫描，去重保序）。"""
    parent_id = (parent_task_id or "").strip()
    if not parent_id:
        return []
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(tid: str) -> None:
        tid = (tid or "").strip()
        if not tid or tid in seen:
            return
        seen.add(tid)
        ordered.append(tid)

    parent = supervisor.get_task(parent_id)
    graph = load_graph(parent.meta if parent else None)
    if graph is not None:
        for n in list(graph.nodes) + list(graph.retired):
            if n.task_id:
                _add(n.task_id)
    try:
        for t in supervisor.list_tasks(limit=200, archived=None):
            meta = t.meta or {}
            if not is_supervisor_task(meta):
                continue
            if str(meta.get(META_PLAN_PARENT_ID) or "").strip() != parent_id:
                continue
            _add(t.task_id)
    except Exception:  # noqa: BLE001
        pass
    return ordered


def plan_parent_stats_summary(
    supervisor: Supervisor,
    parent_task_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """
    父计划侧栏/详情用量：子任务 token 等计数之和；耗时为会话创建→结束跨度。

    监管脑节点卡仍用各自 meta.stats；本函数只服务父计划摘要。
    """
    from fawai_avatar.task import stats as task_stats

    parent_id = (parent_task_id or "").strip()
    if not parent_id:
        return None
    parent = supervisor.get_task(parent_id)
    if parent is None or not is_orchestrator_task(parent.meta):
        return None

    now_f = float(now if now is not None else time.time())
    agg = task_stats.empty_stats()
    # 只加监管脑子任务；父 meta.stats 可能是上次聚合快照，再加会重复计数
    for cid in list_plan_child_ids(supervisor, parent_id):
        child = supervisor.get_task(cid)
        if child is None:
            continue
        raw = (child.meta or {}).get(task_stats.STATS_KEY)
        if isinstance(raw, dict):
            task_stats.add_stats_counters(agg, raw)

    elapsed_ms = task_stats.session_span_elapsed_ms(
        created_at=getattr(parent, "created_at", 0),
        updated_at=getattr(parent, "updated_at", 0),
        status=getattr(parent, "status", None),
        now=now_f,
    )
    summary = task_stats.summary_with_elapsed(agg, elapsed_ms=elapsed_ms)
    if (
        summary["total_tokens"]
        or summary["prompt_tokens"]
        or summary["completion_tokens"]
        or summary["rounds"]
        or summary["llm_calls"]
        or summary["tools"]
        or summary["elapsed_ms"]
    ):
        return summary
    return None


def persist_plan_parent_stats(
    supervisor: Supervisor,
    parent_task_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """把聚合摘要写回父任务 meta.stats（墙钟用 session 跨度落成 wall_ms）。"""
    from fawai_avatar.task import stats as task_stats

    summary = plan_parent_stats_summary(supervisor, parent_task_id, now=now)
    if summary is None:
        return None
    parent_id = (parent_task_id or "").strip()
    stored = {
        "prompt_tokens": int(summary.get("prompt_tokens") or 0),
        "completion_tokens": int(summary.get("completion_tokens") or 0),
        "total_tokens": int(summary.get("total_tokens") or 0),
        "wall_ms": int(summary.get("elapsed_ms") or 0),
        "rounds": int(summary.get("rounds") or 0),
        "llm_calls": int(summary.get("llm_calls") or 0),
        "tools": dict(summary.get("tools") or {}),
        "run_started_at": None,
    }
    try:
        supervisor.merge_task_meta(
            parent_id, {task_stats.STATS_KEY: task_stats.persistable(stored)}
        )
    except Exception:  # noqa: BLE001
        return summary
    return summary


def cancel_plan_children(supervisor: Supervisor, parent_task_id: str) -> list[str]:
    """停止父计划下仍在跑的监管脑任务；返回已尝试 cancel 的 id。"""
    cancelled: list[str] = []
    for cid in list_plan_child_ids(supervisor, parent_task_id):
        child = supervisor.get_task(cid)
        if child is None:
            continue
        if child.status in {"completed", "failed", "cancelled", "archived"}:
            continue
        try:
            if supervisor.cancel(cid):
                cancelled.append(cid)
        except Exception:  # noqa: BLE001
            continue
    return cancelled


def load_graph(meta: dict[str, Any] | None) -> PlanGraph | None:
    raw = (meta or {}).get(META_PLAN_GRAPH)
    if not isinstance(raw, dict):
        return None
    try:
        return PlanGraph.from_dict(raw)
    except Exception:  # noqa: BLE001
        return None


def save_graph(supervisor: Supervisor, parent_task_id: str, graph: PlanGraph) -> None:
    supervisor.merge_task_meta(
        parent_task_id,
        {
            META_PLAN_GRAPH: graph.to_dict(),
            "plan_graph_updated_at": time.time(),
        },
    )


def bootstrap_parent_meta(
    *,
    graph: PlanGraph,
    title: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        META_PLAN_ROLE: "orchestrator",
        META_PLAN_ID: graph.plan_id,
        META_PLAN_GRAPH: graph.to_dict(),
        "title": title,
        "title_source": "heuristic",
        "plan_mode": True,
        "source": "session_hub",
    }
    if extra:
        meta.update(extra)
    return meta


def spawn_ready_children(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    graph: PlanGraph,
) -> list[str]:
    """按就绪集 create 监管脑子任务；写回 graph；返回新 task_id 列表。"""
    parent = supervisor.get_task(parent_task_id)
    if parent is None:
        return []
    created: list[str] = []
    for nid in graph.ready_node_ids():
        node = graph.get(nid)
        if node is None:
            continue
        child_id = _create_supervisor_child(
            supervisor,
            parent_task_id=parent_task_id,
            graph=graph,
            node=node,
        )
        graph.mark_running(nid, child_id)
        created.append(child_id)
    if created:
        save_graph(supervisor, parent_task_id, graph)
        # 父计划保持 running 但不跑 Worker
        if parent.status in {"pending", "waiting"}:
            supervisor.set_running(parent_task_id)
    return created


def on_supervisor_finished(
    supervisor: Supervisor,
    *,
    child_task_id: str,
    ok: bool,
    final_thought: str = "",
    error: str = "",
) -> dict[str, Any]:
    """
    监管脑子任务终态后：回写图、规则拉下游或失败阻塞、父计划收束。

    返回 {parent_task_id, spawned, parent_completed, parent_failed, graph}
    """
    child = supervisor.get_task(child_task_id)
    if child is None or not is_supervisor_task(child.meta):
        return {"spawned": [], "parent_completed": False, "parent_failed": False}
    parent_id = str((child.meta or {}).get(META_PLAN_PARENT_ID) or "").strip()
    node_id = str((child.meta or {}).get(META_PLAN_NODE_ID) or "").strip()
    if not parent_id or not node_id:
        return {"spawned": [], "parent_completed": False, "parent_failed": False}

    parent = supervisor.get_task(parent_id)
    if parent is None:
        return {
            "parent_task_id": parent_id,
            "spawned": [],
            "parent_completed": False,
            "parent_failed": False,
        }
    graph = load_graph(parent.meta)
    if graph is None:
        return {
            "parent_task_id": parent_id,
            "spawned": [],
            "parent_completed": False,
            "parent_failed": False,
        }

    # 重排淘汰：子任务已 cancel 且图上该 task 不再归属 → 忽略，不拖垮父计划
    superseded = bool((child.meta or {}).get("plan_superseded"))
    child_cancelled = child.status == "cancelled" or str(error or "") == "cancelled"
    node = graph.get(node_id)
    if superseded or (
        child_cancelled
        and (
            node is None
            or (node.task_id and node.task_id != child_task_id)
            or node.status in {"cancelled", "pending", "ready"}
        )
    ):
        # 图可能已由 apply_plan_revision 更新；仅尝试补拉就绪集
        spawned = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=graph
        )
        save_graph(supervisor, parent_id, graph)
        return {
            "parent_task_id": parent_id,
            "spawned": spawned,
            "parent_completed": False,
            "parent_failed": False,
            "ignored": True,
            "graph": graph.to_dict(),
        }

    if ok:
        thought = (final_thought or "").strip()
        # summary：下游 consumes / 产物键（保持短）
        # user_summary：父结论文用全文，禁止再按固定字数硬切；过长仅图内联落文件，收束时读回
        artifact: dict[str, Any] = {
            "summary": thought[:_ARTIFACT_SUMMARY_MAX],
            "child_task_id": child_task_id,
        }
        artifact.update(
            _store_user_summary(
                thought,
                data_dir=str(supervisor.data_dir),
                task_id=parent_id,
                node_id=node_id,
            )
        )
        if node and node.produces:
            for key in node.produces:
                artifact[key] = artifact["summary"]
        graph.mark_completed(node_id, artifact=artifact)
        from fawai_avatar.brain.shared_facts import fill_shared_facts_from_node

        fill_shared_facts_from_node(
            graph,
            node_id,
            final_thought or "",
            data_dir=str(supervisor.data_dir),
            task_id=parent_id,
        )
    elif child_cancelled:
        graph.mark_cancelled(node_id, reason=error or "cancelled")
        save_graph(supervisor, parent_id, graph)
        # 非 superseded 的取消：不自动 fail 整图，留给编排脑/用户
        spawned = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=graph
        )
        return {
            "parent_task_id": parent_id,
            "spawned": spawned,
            "parent_completed": False,
            "parent_failed": False,
            "node_cancelled": True,
            "graph": graph.to_dict(),
        }
    else:
        graph.mark_failed(node_id, error=error or "failed")

    spawned: list[str] = []
    parent_completed = False
    parent_failed = False

    if not ok:
        save_graph(supervisor, parent_id, graph)
        # 默认阻塞下游；父计划 failed（可后续改图重试）
        supervisor.merge_task_meta(
            parent_id,
            {
                "final_thought": f"节点 {node_id} 失败：{(error or '')[:200]}",
                "ok": False,
                "error": error or f"node {node_id} failed",
            },
        )
        supervisor.fail(parent_id, message=f"plan node {node_id} failed")
        parent_failed = True
    else:
        spawned = spawn_ready_children(
            supervisor, parent_task_id=parent_id, graph=graph
        )
        save_graph(supervisor, parent_id, graph)
        if graph.all_completed():
            summary = _parent_summary(graph)
            supervisor.merge_task_meta(
                parent_id,
                {
                    "final_thought": summary,
                    "ok": True,
                    "error": None,
                },
            )
            supervisor.complete(parent_id)
            parent_completed = True
        elif graph.all_terminal() and not graph.all_completed():
            supervisor.merge_task_meta(
                parent_id,
                {
                    "final_thought": "计划未全部成功",
                    "ok": False,
                    "error": "some nodes failed or cancelled",
                },
            )
            supervisor.fail(parent_id, message="plan incomplete")
            parent_failed = True

    return {
        "parent_task_id": parent_id,
        "spawned": spawned,
        "parent_completed": parent_completed,
        "parent_failed": parent_failed,
        "graph": graph.to_dict(),
    }


def resume_plan_child_node(
    supervisor: Supervisor,
    *,
    child_task_id: str,
) -> dict[str, Any]:
    """
    监管脑「从步骤恢复」配套：复用同一子任务，把图节点标回 running，并重开终态父计划。

    与 ``retry_plan_nodes`` 不同：不重置节点、不 spawn 新监管脑、不 supersede 旧子任务。
    返回 {ok, reason?, parent_task_id, plan_node_id, parent_status, graph, kind}。
    """
    child = supervisor.get_task(child_task_id)
    if child is None or not is_supervisor_task(child.meta):
        return {
            "ok": False,
            "reason": "非监管脑子任务",
            "kind": "resume_from_step",
        }
    if bool((child.meta or {}).get("plan_superseded")):
        return {
            "ok": False,
            "reason": "该监管脑已被替换，请用「重试本节点」",
            "kind": "resume_from_step",
        }
    parent_id = str((child.meta or {}).get(META_PLAN_PARENT_ID) or "").strip()
    node_id = str((child.meta or {}).get(META_PLAN_NODE_ID) or "").strip()
    if not parent_id or not node_id:
        return {
            "ok": False,
            "reason": "缺少父计划或节点 id",
            "kind": "resume_from_step",
        }

    busy = _begin_plan_revision(parent_id, "resume_from_step")
    if busy:
        return {
            "ok": False,
            "reason": busy,
            "parent_task_id": parent_id,
            "plan_node_id": node_id,
            "kind": "resume_from_step",
        }
    try:
        parent = supervisor.get_task(parent_id)
        if parent is None or not is_orchestrator_task(parent.meta):
            return {
                "ok": False,
                "reason": "父计划不存在或非编排器",
                "parent_task_id": parent_id,
                "plan_node_id": node_id,
                "kind": "resume_from_step",
            }
        graph = load_graph(parent.meta)
        if graph is None:
            return {
                "ok": False,
                "reason": "无任务图",
                "parent_task_id": parent_id,
                "plan_node_id": node_id,
                "kind": "resume_from_step",
            }
        node = graph.get(node_id)
        if node is None:
            return {
                "ok": False,
                "reason": f"节点 {node_id} 不在活跃图上",
                "parent_task_id": parent_id,
                "plan_node_id": node_id,
                "kind": "resume_from_step",
                "graph": graph.to_dict(),
            }
        if node.task_id and node.task_id != child_task_id:
            return {
                "ok": False,
                "reason": "节点已绑定其他监管脑，请用「重试本节点」",
                "parent_task_id": parent_id,
                "plan_node_id": node_id,
                "kind": "resume_from_step",
                "graph": graph.to_dict(),
            }
        if node.status not in {
            "failed",
            "completed",
            "cancelled",
            "running",
            "pending",
            "ready",
        }:
            return {
                "ok": False,
                "reason": f"节点状态={node.status}，无法从步骤恢复",
                "parent_task_id": parent_id,
                "plan_node_id": node_id,
                "kind": "resume_from_step",
                "graph": graph.to_dict(),
            }

        # 父计划终态须重开，否则子任务成功后 complete(parent) 会被拒
        parent = supervisor.get_task(parent_id)
        if parent and parent.status in {"failed", "cancelled", "completed"}:
            supervisor.requeue_task(parent_id, reopen=True)
        elif parent and parent.status == "paused":
            supervisor.resume(parent_id)
        parent = supervisor.get_task(parent_id)
        if parent and parent.status in {"pending", "waiting", "paused"}:
            supervisor.set_running(parent_id)

        art = dict(node.artifact or {})
        prev_err = str(art.get("error") or "")
        if prev_err:
            art["resume_from_error"] = prev_err[:500]
        art.pop("error", None)
        art["resumed_from_step"] = True
        node.artifact = art
        graph.mark_running(node_id, child_task_id)
        save_graph(supervisor, parent_id, graph)
        supervisor.merge_task_meta(
            parent_id,
            {
                "ok": None,
                "error": None,
                "mode": "resume_from_step",
                "last_plan_append_kind": "resume_from_step",
                "plan_revision_note": f"节点 {node_id} 从步骤恢复",
                "plan_revision_at": time.time(),
            },
        )

        parent = supervisor.get_task(parent_id)
        parent_status = str(parent.status or "") if parent else ""
        return {
            "ok": True,
            "parent_task_id": parent_id,
            "plan_node_id": node_id,
            "parent_status": parent_status,
            "graph": graph.to_dict(),
            "kind": "resume_from_step",
        }
    finally:
        _end_plan_revision(parent_id)


def apply_append_to_running_supervisor(
    supervisor: Supervisor,
    *,
    child_task_id: str,
    instruction: str,
    inbound_id: str = "",
) -> bool:
    """no_graph_change / keep_running：把编排脑指令写入子任务 pending_appends。"""
    child = supervisor.get_task(child_task_id)
    if child is None:
        return False
    pending = list((child.meta or {}).get("pending_appends") or [])
    pending.append(
        {
            "text": instruction[:500],
            "inbound_id": inbound_id,
            "ts": time.time(),
            "mode": "plan_orchestrator_dispatch",
        }
    )
    return supervisor.merge_task_meta(
        child_task_id, {"pending_appends": pending[-20:]}
    )


def apply_plan_revision(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    kind: str,
    instruction: str,
    inbound_id: str = "",
) -> dict[str, Any]:
    """
    成熟修订：编排脑出修订图 → cancel 过时 running → 写回图 → 按就绪集拉起。

    返回 {
      ok, reason, source, version, cancelled, spawned, kept, sketch, graph
    }
    """
    from fawai_avatar.brain.plan_revise import build_revision

    busy = _begin_plan_revision(parent_task_id, f"revise:{kind}")
    if busy:
        return {
            "ok": False,
            "reason": busy,
            "cancelled": [],
            "spawned": [],
            "kind": kind,
        }
    try:
        parent = supervisor.get_task(parent_task_id)
        if parent is None:
            return {"ok": False, "reason": "父计划不存在", "cancelled": [], "spawned": []}
        graph = load_graph(parent.meta)
        if graph is None:
            return {"ok": False, "reason": "无任务图", "cancelled": [], "spawned": []}

        revision = build_revision(
            graph,
            kind=kind,
            instruction=instruction,
            root_goal=str(parent.goal or graph.root_goal),
        )
        if revision.kind == "noop" or revision.source == "noop":
            return {
                "ok": False,
                "reason": revision.reason or "无需修订",
                "cancelled": [],
                "spawned": [],
            }

        cancelled: list[str] = []
        for tid in revision.cancel_task_ids:
            tid = str(tid).strip()
            if not tid:
                continue
            supervisor.merge_task_meta(
                tid,
                {
                    "plan_superseded": True,
                    "plan_cancel_reason": kind,
                    "plan_revision_instruction": instruction[:200],
                    "inbound_id": inbound_id,
                },
            )
            if supervisor.cancel(tid):
                cancelled.append(tid)

        for tid, note in (revision.dispatch_by_task or {}).items():
            apply_append_to_running_supervisor(
                supervisor,
                child_task_id=str(tid),
                instruction=note or instruction,
                inbound_id=inbound_id,
            )

        # 父计划若曾因节点失败进入 failed，修订后重开
        parent = supervisor.get_task(parent_task_id)
        if parent and parent.status in {"failed", "cancelled", "completed"}:
            supervisor.requeue_task(parent_task_id, reopen=True)
        parent = supervisor.get_task(parent_task_id)
        if parent and parent.status in {"pending", "waiting", "paused"}:
            supervisor.set_running(parent_task_id)

        save_graph(supervisor, parent_task_id, revision.graph)
        supervisor.merge_task_meta(
            parent_task_id,
            {
                "plan_revision_pending": False,
                "last_plan_append_kind": kind,
                "plan_revision_note": instruction[:200],
                "plan_revision_reason": revision.reason,
                "plan_revision_source": revision.source,
                "plan_revision_at": time.time(),
                "ok": None,
                "error": None,
            },
        )

        spawned = spawn_ready_children(
            supervisor, parent_task_id=parent_task_id, graph=revision.graph
        )
        save_graph(supervisor, parent_task_id, revision.graph)

        return {
            "ok": True,
            "reason": revision.reason,
            "source": revision.source,
            "version": revision.graph.version,
            "cancelled": cancelled,
            "spawned": spawned,
            "kept": list(revision.keep_running),
            "sketch": "\n".join(revision.graph.summary_lines()),
            "graph": revision.graph.to_dict(),
            "kind": kind,
        }
    finally:
        _end_plan_revision(parent_task_id)


def retry_plan_nodes(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    node_ids: list[str] | None = None,
    reason: str = "重试失败节点",
    inbound_id: str = "",
) -> dict[str, Any]:
    """
    重试失败节点：保留 completed 上游；仅重置指定（或全部）failed 节点并拉起。

    node_ids 为空时 = 图上所有 failed。不重试 cancelled / 依赖未齐的节点。
    返回 {ok, reason, retried, skipped, spawned, sketch, graph, kind, parent_status}。
    """
    busy = _begin_plan_revision(parent_task_id, "retry_nodes")
    if busy:
        return {
            "ok": False,
            "reason": busy,
            "retried": [],
            "skipped": [],
            "spawned": [],
            "kind": "retry_nodes",
        }
    try:
        return _retry_plan_nodes_unlocked(
            supervisor,
            parent_task_id=parent_task_id,
            node_ids=node_ids,
            reason=reason,
            inbound_id=inbound_id,
        )
    finally:
        _end_plan_revision(parent_task_id)


def _retry_plan_nodes_unlocked(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    node_ids: list[str] | None = None,
    reason: str = "重试失败节点",
    inbound_id: str = "",
) -> dict[str, Any]:
    parent = supervisor.get_task(parent_task_id)
    if parent is None:
        return {
            "ok": False,
            "reason": "父计划不存在",
            "retried": [],
            "skipped": [],
            "spawned": [],
            "kind": "retry_nodes",
        }
    if not is_orchestrator_task(parent.meta):
        return {
            "ok": False,
            "reason": "非编排器父计划",
            "retried": [],
            "skipped": [],
            "spawned": [],
            "kind": "retry_nodes",
        }
    graph = load_graph(parent.meta)
    if graph is None:
        return {
            "ok": False,
            "reason": "无任务图",
            "retried": [],
            "skipped": [],
            "spawned": [],
            "kind": "retry_nodes",
        }

    wanted = [str(x).strip() for x in (node_ids or []) if str(x).strip()]
    if not wanted:
        wanted = [n.id for n in graph.nodes if n.status == "failed"]
    if not wanted:
        return {
            "ok": False,
            "reason": "无失败节点可重试",
            "retried": [],
            "skipped": [],
            "spawned": [],
            "kind": "retry_nodes",
            "graph": graph.to_dict(),
        }

    retried: list[str] = []
    skipped: list[dict[str, str]] = []
    old_child_ids: list[str] = []
    for nid in wanted:
        node = graph.get(nid)
        if node is None:
            skipped.append({"node_id": nid, "reason": "节点不存在"})
            continue
        if node.status != "failed":
            skipped.append({"node_id": nid, "reason": f"状态={node.status}，仅 failed 可重试"})
            continue
        old_tid = graph.reset_failed_for_retry(nid)
        node_after = graph.get(nid)
        if node_after is None or node_after.status == "failed":
            skipped.append({"node_id": nid, "reason": "依赖未完成或产物契约未齐"})
            continue
        retried.append(nid)
        if old_tid:
            old_child_ids.append(old_tid)
            try:
                supervisor.merge_task_meta(
                    old_tid,
                    {
                        "plan_superseded": True,
                        "plan_cancel_reason": "retry_nodes",
                        "plan_revision_instruction": reason[:200],
                        "inbound_id": inbound_id,
                    },
                )
            except Exception:  # noqa: BLE001
                pass

    if not retried:
        return {
            "ok": False,
            "reason": "没有可重置的失败节点",
            "retried": [],
            "skipped": skipped,
            "spawned": [],
            "kind": "retry_nodes",
            "graph": graph.to_dict(),
        }

    # 父计划终态重开后进入 running 调度
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"failed", "cancelled", "completed"}:
        supervisor.requeue_task(parent_task_id, reopen=True)
    elif parent and parent.status == "paused":
        supervisor.resume(parent_task_id)
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"pending", "waiting", "paused"}:
        supervisor.set_running(parent_task_id)

    save_graph(supervisor, parent_task_id, graph)
    supervisor.merge_task_meta(
        parent_task_id,
        {
            "plan_revision_pending": False,
            "last_plan_append_kind": "retry_nodes",
            "plan_revision_note": reason[:200],
            "plan_revision_reason": reason[:200],
            "plan_revision_at": time.time(),
            "ok": None,
            "error": None,
            "mode": "retry_nodes",
            "retried_nodes": list(retried),
        },
    )

    spawned = spawn_ready_children(
        supervisor, parent_task_id=parent_task_id, graph=graph
    )
    save_graph(supervisor, parent_task_id, graph)
    parent = supervisor.get_task(parent_task_id)
    parent_status = str(parent.status or "") if parent else ""

    return {
        "ok": True,
        "reason": reason[:200],
        "retried": retried,
        "skipped": skipped,
        "old_children": old_child_ids,
        "spawned": spawned,
        "sketch": "\n".join(graph.summary_lines()),
        "graph": graph.to_dict(),
        "kind": "retry_nodes",
        "parent_status": parent_status,
    }


def remove_plan_nodes(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    node_ids: list[str],
    reason: str = "手工删除节点",
    inbound_id: str = "",
) -> dict[str, Any]:
    """
    手工删除计划节点：retire 出活跃图、cancel 监管脑、剥离下游依赖；
    若父计划因失败而停且图上已无 failed，则重开并拉就绪集。

    禁止删光全部活跃节点。返回 {ok, removed, skipped, cancelled_children, spawned, graph, ...}。
    """
    busy = _begin_plan_revision(parent_task_id, "remove_nodes")
    if busy:
        return {
            "ok": False,
            "reason": busy,
            "removed": [],
            "skipped": [],
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
        }
    try:
        return _remove_plan_nodes_unlocked(
            supervisor,
            parent_task_id=parent_task_id,
            node_ids=node_ids,
            reason=reason,
            inbound_id=inbound_id,
        )
    finally:
        _end_plan_revision(parent_task_id)


def _remove_plan_nodes_unlocked(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    node_ids: list[str],
    reason: str = "手工删除节点",
    inbound_id: str = "",
) -> dict[str, Any]:
    parent = supervisor.get_task(parent_task_id)
    if parent is None:
        return {
            "ok": False,
            "reason": "父计划不存在",
            "removed": [],
            "skipped": [],
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
        }
    if not is_orchestrator_task(parent.meta):
        return {
            "ok": False,
            "reason": "非编排器父计划",
            "removed": [],
            "skipped": [],
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
        }
    graph = load_graph(parent.meta)
    if graph is None:
        return {
            "ok": False,
            "reason": "无任务图",
            "removed": [],
            "skipped": [],
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
        }

    wanted = [str(x).strip() for x in (node_ids or []) if str(x).strip()]
    # 去重保序
    seen: set[str] = set()
    ordered: list[str] = []
    for nid in wanted:
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(nid)
    if not ordered:
        return {
            "ok": False,
            "reason": "未指定节点",
            "removed": [],
            "skipped": [],
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
            "graph": graph.to_dict(),
        }

    active_ids = {n.id for n in graph.nodes}
    to_remove = [nid for nid in ordered if nid in active_ids]
    skipped: list[dict[str, str]] = []
    for nid in ordered:
        if nid not in active_ids:
            skipped.append({"node_id": nid, "reason": "节点不在活跃图上"})
    if not to_remove:
        return {
            "ok": False,
            "reason": "没有可删除的活跃节点",
            "removed": [],
            "skipped": skipped,
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
            "graph": graph.to_dict(),
        }
    if len(to_remove) >= len(graph.nodes):
        return {
            "ok": False,
            "reason": "不能删光全部活跃节点；请停止任务或整图重规划",
            "removed": [],
            "skipped": skipped,
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
            "graph": graph.to_dict(),
        }

    removed: list[str] = []
    cancelled_children: list[str] = []
    for nid in to_remove:
        node = graph.get(nid)
        old_tid = str(node.task_id or "").strip() if node else ""
        retired = graph.retire_node(nid, reason=reason[:200] or "manual_remove")
        if retired is None:
            skipped.append({"node_id": nid, "reason": "retire 失败"})
            continue
        graph.strip_dependency(nid)
        removed.append(nid)
        if old_tid:
            cancelled_children.append(old_tid)
            try:
                supervisor.merge_task_meta(
                    old_tid,
                    {
                        "plan_superseded": True,
                        "plan_cancel_reason": "remove_nodes",
                        "plan_revision_instruction": reason[:200],
                        "inbound_id": inbound_id,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                supervisor.cancel(old_tid)
            except Exception:  # noqa: BLE001
                pass

    if not removed:
        return {
            "ok": False,
            "reason": "没有成功删除的节点",
            "removed": [],
            "skipped": skipped,
            "cancelled_children": [],
            "spawned": [],
            "kind": "remove_nodes",
            "graph": graph.to_dict(),
        }

    # 父计划终态：无 failed 则可重开续跑未决
    still_failed = any(n.status == "failed" for n in graph.nodes)
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"failed", "cancelled", "completed"} and not still_failed:
        supervisor.requeue_task(parent_task_id, reopen=True)
    elif parent and parent.status == "paused" and not still_failed:
        supervisor.resume(parent_task_id)
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"pending", "waiting", "paused"} and not still_failed:
        supervisor.set_running(parent_task_id)

    save_graph(supervisor, parent_task_id, graph)
    meta_patch: dict[str, Any] = {
        "plan_revision_pending": False,
        "last_plan_append_kind": "remove_nodes",
        "plan_revision_note": reason[:200],
        "plan_revision_reason": reason[:200],
        "plan_revision_at": time.time(),
        "mode": "remove_nodes",
        "removed_nodes": list(removed),
    }
    if not still_failed:
        meta_patch["ok"] = None
        meta_patch["error"] = None
    supervisor.merge_task_meta(parent_task_id, meta_patch)

    spawned: list[str] = []
    if not still_failed:
        spawned = spawn_ready_children(
            supervisor, parent_task_id=parent_task_id, graph=graph
        )
        save_graph(supervisor, parent_task_id, graph)

    parent = supervisor.get_task(parent_task_id)
    parent_status = str(parent.status or "") if parent else ""
    return {
        "ok": True,
        "reason": reason[:200],
        "removed": removed,
        "skipped": skipped,
        "cancelled_children": cancelled_children,
        "spawned": spawned,
        "sketch": "\n".join(graph.summary_lines()),
        "graph": graph.to_dict(),
        "kind": "remove_nodes",
        "parent_status": parent_status,
    }


def wants_full_replan_followup(text: str) -> bool:
    """用户追问是否显式要求整图重规划（否则失败父计划默认重试失败节点）。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    needles = (
        "整图重规划",
        "重新规划",
        "全部重做",
        "全部重来",
        "从头再来",
        "full replan",
        "replan all",
        "redo all",
        "start over",
    )
    return any(n in t for n in needles)


def replan_full_graph(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    root_goal: str | None = None,
    reason: str = "整图重规划",
    inbound_id: str = "",
) -> dict[str, Any]:
    """
    父计划整图重规划（重新执行）：cancel 旧监管脑 → 旧活跃节点进 retired →
    编排脑按根目标重新出图 → version+1 → 按就绪集拉起。

    不保留已完成节点产物；与 reorder（保留 completed）区分。
    返回 {ok, reason, source, version, cancelled, spawned, sketch, graph, kind}。
    """
    busy = _begin_plan_revision(parent_task_id, "full_replan")
    if busy:
        return {
            "ok": False,
            "reason": busy,
            "cancelled": [],
            "spawned": [],
            "kind": "full_replan",
        }
    try:
        return _replan_full_graph_unlocked(
            supervisor,
            parent_task_id=parent_task_id,
            root_goal=root_goal,
            reason=reason,
            inbound_id=inbound_id,
        )
    finally:
        _end_plan_revision(parent_task_id)


def _replan_full_graph_unlocked(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    root_goal: str | None = None,
    reason: str = "整图重规划",
    inbound_id: str = "",
) -> dict[str, Any]:
    from fawai_avatar.brain.orchestrator import build_plan_graph

    parent = supervisor.get_task(parent_task_id)
    if parent is None:
        return {
            "ok": False,
            "reason": "父计划不存在",
            "cancelled": [],
            "spawned": [],
            "kind": "full_replan",
        }
    if not is_orchestrator_task(parent.meta):
        return {
            "ok": False,
            "reason": "非编排器父计划",
            "cancelled": [],
            "spawned": [],
            "kind": "full_replan",
        }

    old = load_graph(parent.meta)
    goal = (root_goal or parent.goal or (old.root_goal if old else "") or "").strip()
    if not goal:
        return {
            "ok": False,
            "reason": "无根目标可出图",
            "cancelled": [],
            "spawned": [],
            "kind": "full_replan",
        }

    cancelled: list[str] = []
    for cid in list_plan_child_ids(supervisor, parent_task_id):
        child = supervisor.get_task(cid)
        if child is None:
            continue
        supervisor.merge_task_meta(
            cid,
            {
                "plan_superseded": True,
                "plan_cancel_reason": "full_replan",
                "plan_revision_instruction": reason[:200],
                "inbound_id": inbound_id,
            },
        )
        if child.status in {"completed", "failed", "cancelled", "archived"}:
            continue
        if supervisor.cancel(cid):
            cancelled.append(cid)

    old_ver = int(old.version) if old else 0
    plan_id = (old.plan_id if old and old.plan_id else "") or None
    new_graph = build_plan_graph(goal, plan_id=plan_id)
    # 有旧图则 bump；无旧图保持出图默认 version（通常 1）
    if old is not None:
        new_graph.version = old_ver + 1

    retired: list[PlanNode] = list(old.retired) if old else []
    if old is not None:
        for n in old.nodes:
            nn = PlanNode.from_dict(n.to_dict())
            if nn.status not in {"cancelled", "failed"}:
                nn.status = "cancelled"
            nn.artifact = {
                **(nn.artifact or {}),
                "cancel_reason": "full_replan_superseded",
            }
            retired.append(nn)
    new_graph.retired = retired

    # 父计划终态 / 卡在 pending 均可 reopen 后进入 running 调度
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"failed", "cancelled", "completed"}:
        supervisor.requeue_task(parent_task_id, reopen=True)
    elif parent and parent.status == "paused":
        supervisor.resume(parent_task_id)
    parent = supervisor.get_task(parent_task_id)
    if parent and parent.status in {"pending", "waiting", "paused"}:
        supervisor.set_running(parent_task_id)

    save_graph(supervisor, parent_task_id, new_graph)
    supervisor.merge_task_meta(
        parent_task_id,
        {
            META_PLAN_ID: new_graph.plan_id,
            "plan_revision_pending": False,
            "last_plan_append_kind": "full_replan",
            "plan_revision_note": reason[:200],
            "plan_revision_reason": reason[:200],
            "plan_revision_source": new_graph.source,
            "plan_revision_at": time.time(),
            "ok": None,
            "error": None,
            "mode": "full_replan",
        },
    )

    spawned = spawn_ready_children(
        supervisor, parent_task_id=parent_task_id, graph=new_graph
    )
    save_graph(supervisor, parent_task_id, new_graph)

    return {
        "ok": True,
        "reason": reason[:200],
        "source": new_graph.source,
        "version": new_graph.version,
        "cancelled": cancelled,
        "spawned": spawned,
        "kept": [],
        "sketch": "\n".join(new_graph.summary_lines()),
        "graph": new_graph.to_dict(),
        "kind": "full_replan",
    }


def _create_supervisor_child(
    supervisor: Supervisor,
    *,
    parent_task_id: str,
    graph: PlanGraph,
    node: PlanNode,
) -> str:
    goal_text = format_supervisor_goal(
        root_goal=graph.root_goal,
        node=node,
        artifacts=graph.artifacts,
        shared_facts=list(graph.shared_facts or []),
    )
    title = suggest_task_title(node.goal)[:16]
    auth = {
        "node_id": node.id,
        "stop_when": node.stop_when,
        "produces": list(node.produces),
        "consumes": list(node.consumes),
        "modality_hint": node.modality_hint,
    }
    meta: dict[str, Any] = {
        META_PLAN_ROLE: "supervisor",
        META_PLAN_ID: graph.plan_id,
        META_PLAN_NODE_ID: node.id,
        META_PLAN_PARENT_ID: parent_task_id,
        META_PLAN_AUTH: auth,
        "plan_mode": True,
        "title": title,
        "title_source": "heuristic",
        "source": "plan_scheduler",
        "parent_task_id": parent_task_id,
    }
    # 父计划追问/追加约束：新监管脑启动时带入 pending_appends，避免整图重做才听得到
    parent = supervisor.get_task(parent_task_id)
    note = str((parent.meta or {}).get("plan_dispatch_note") or "").strip() if parent else ""
    if not note and parent is not None:
        fus = list((parent.meta or {}).get("followups") or [])
        if fus and isinstance(fus[-1], dict):
            note = str(fus[-1].get("text") or "").strip()
    if note:
        meta["pending_appends"] = [
            {
                "text": note[:500],
                "instruction": note[:500],
                "ts": time.time(),
                "mode": "plan_orchestrator_dispatch",
            }
        ]
    return supervisor.create_task(goal_text, meta=meta)


# 产物摘要：注入下游 goal / produces 键（编排脑 consumes 行另有更短截断）
_ARTIFACT_SUMMARY_MAX = 800
# 图内联 user_summary 阈值：超过则落会话文件，结论文仍读回全文（不是内容截断上限）
_PARENT_INLINE_MAX = 8000
# 节点索引行：只取首行预览，超长加省略号（避免半截 Markdown 表）
_PARENT_NODE_PREVIEW_MAX = 80


def _first_line_preview(text: str, max_chars: int) -> str:
    """取首行作短预览；超长以 … 收尾，不在词中无标记硬切。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    limit = max(1, int(max_chars))
    if len(line) <= limit:
        return line
    return line[: limit - 1] + "…"


def _store_user_summary(
    thought: str,
    *,
    data_dir: str,
    task_id: str,
    node_id: str,
) -> dict[str, Any]:
    """结论文全文入库；过长仅把图内联换成文件指针，内容不砍。"""
    body = (thought or "").strip()
    if not body:
        return {"user_summary": "", "user_summary_path": ""}
    if len(body) <= _PARENT_INLINE_MAX:
        return {"user_summary": body, "user_summary_path": ""}
    try:
        from fawai_avatar.workspace.py_session import session_root

        folder = session_root(data_dir, task_id) / "plan_conclusions"
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in node_id)[:40]
        path = folder / f"{safe or 'node'}.md"
        path.write_text(body, encoding="utf-8")
        return {
            "user_summary": _first_line_preview(body, _PARENT_NODE_PREVIEW_MAX),
            "user_summary_path": str(path),
        }
    except Exception:
        # 落盘失败时仍保留全文，避免为控体积而截断用户可见结论
        return {"user_summary": body, "user_summary_path": ""}


def _load_user_summary(art: dict[str, Any] | None) -> str:
    """读回节点面向用户的完整结论文（含落文件的全文）。"""
    data = art or {}
    path = str(data.get("user_summary_path") or "").strip()
    if path:
        try:
            from pathlib import Path

            text = Path(path).read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            pass
    return str(data.get("user_summary") or data.get("summary") or "").strip()


def _parent_summary(graph: PlanGraph) -> str:
    """
    父计划收束结论文（写入 meta.final_thought、会话 plan_conclusion 气泡）。

    索引行只给各节点首行短预览；正文用最后一个有产物节点的完整 user_summary
    （过长时从 user_summary_path 读回）。禁止按固定字数硬切正文。
    """
    bits = [f"计划完成（{len(graph.nodes)} 节点）"]
    last_with_body: PlanNode | None = None
    for n in graph.nodes:
        art = n.artifact or {}
        s = _load_user_summary(art)
        preview = _first_line_preview(s, _PARENT_NODE_PREVIEW_MAX) if s else "ok"
        bits.append(f"- {n.id}: {preview or 'ok'}")
        if s:
            last_with_body = n
    if last_with_body is not None:
        body = _load_user_summary(last_with_body.artifact or {})
        if body:
            bits.append("")
            bits.append(body)
    return "\n".join(bits)
