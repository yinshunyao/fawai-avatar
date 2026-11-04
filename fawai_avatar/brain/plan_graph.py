# term:任务图  doc:doc/功能/复杂任务计划模式/说明.md
"""计划模式任务图：节点、依赖、产物契约与状态。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

NodeStatus = Literal[
    "pending",
    "ready",
    "running",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class PlanNode:
    id: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str | None = None
    modality_hint: str = "background"
    produces: list[str] = field(default_factory=list)
    consumes: list[str] = field(default_factory=list)
    stop_when: str = "子目标完成或无法继续"
    status: NodeStatus = "pending"
    task_id: str | None = None
    artifact: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanNode:
        status = str(data.get("status") or "pending")
        if status not in {
            "pending",
            "ready",
            "running",
            "completed",
            "failed",
            "cancelled",
        }:
            status = "pending"
        return cls(
            id=str(data.get("id") or "").strip() or "n0",
            goal=str(data.get("goal") or "").strip(),
            depends_on=[str(x) for x in (data.get("depends_on") or []) if str(x).strip()],
            parallel_group=(
                str(data["parallel_group"]).strip()
                if data.get("parallel_group")
                else None
            ),
            modality_hint=str(data.get("modality_hint") or "background"),
            produces=[str(x) for x in (data.get("produces") or []) if str(x).strip()],
            consumes=[str(x) for x in (data.get("consumes") or []) if str(x).strip()],
            stop_when=str(data.get("stop_when") or "子目标完成或无法继续"),
            status=status,  # type: ignore[arg-type]
            task_id=(str(data["task_id"]) if data.get("task_id") else None),
            artifact=dict(data.get("artifact") or {}),
        )


@dataclass
class PlanGraph:
    plan_id: str
    root_goal: str
    nodes: list[PlanNode] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    source: str = "heuristic"
    # 被重排/租约修订淘汰的节点（审计用，不参与就绪集与完成判定）
    retired: list[PlanNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "root_goal": self.root_goal,
            "nodes": [n.to_dict() for n in self.nodes],
            "retired": [n.to_dict() for n in self.retired],
            "artifacts": dict(self.artifacts),
            "version": int(self.version),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanGraph:
        nodes = [PlanNode.from_dict(x) for x in (data.get("nodes") or []) if isinstance(x, dict)]
        retired = [
            PlanNode.from_dict(x) for x in (data.get("retired") or []) if isinstance(x, dict)
        ]
        return cls(
            plan_id=str(data.get("plan_id") or "").strip(),
            root_goal=str(data.get("root_goal") or ""),
            nodes=nodes,
            artifacts=dict(data.get("artifacts") or {}),
            version=int(data.get("version") or 1),
            source=str(data.get("source") or "heuristic"),
            retired=retired,
        )

    def node_map(self) -> dict[str, PlanNode]:
        return {n.id: n for n in self.nodes}

    def get(self, node_id: str) -> PlanNode | None:
        return self.node_map().get(node_id)

    def ready_node_ids(self) -> list[str]:
        """依赖均 completed 且产物键已在 artifacts（若声明 consumes）→ 可启动。"""
        done = {n.id for n in self.nodes if n.status == "completed"}
        out: list[str] = []
        for n in self.nodes:
            if n.status not in {"pending", "ready"}:
                continue
            if n.task_id:
                continue
            if any(d not in done for d in n.depends_on):
                continue
            if any(c not in self.artifacts for c in n.consumes):
                continue
            out.append(n.id)
        return out

    def active_nodes(self) -> list[PlanNode]:
        """参与调度的节点（不含已 retired）。"""
        return list(self.nodes)

    def all_terminal(self) -> bool:
        return bool(self.nodes) and all(
            n.status in {"completed", "failed", "cancelled"} for n in self.nodes
        )

    def all_completed(self) -> bool:
        """活跃节点全部 completed 才算计划成功（retired 不计入）。"""
        return bool(self.nodes) and all(n.status == "completed" for n in self.nodes)

    def mark_running(self, node_id: str, task_id: str) -> None:
        n = self.get(node_id)
        if n is None:
            return
        n.status = "running"
        n.task_id = task_id

    def mark_completed(
        self,
        node_id: str,
        *,
        artifact: dict[str, Any] | None = None,
    ) -> None:
        n = self.get(node_id)
        if n is None:
            return
        n.status = "completed"
        if artifact:
            n.artifact = dict(artifact)
            for key in n.produces:
                if key in artifact:
                    self.artifacts[key] = artifact[key]
            if "summary" in artifact and n.produces:
                # 默认把 summary 填到第一个 produce 键（若尚未写）
                first = n.produces[0]
                if first not in self.artifacts:
                    self.artifacts[first] = artifact.get("summary")

    def mark_failed(self, node_id: str, *, error: str = "") -> None:
        n = self.get(node_id)
        if n is None:
            return
        n.status = "failed"
        if error:
            n.artifact = {**(n.artifact or {}), "error": error[:500]}

    def reset_failed_for_retry(self, node_id: str) -> str | None:
        """
        将 failed 节点重置为 pending 以便重新调度。

        依赖须均已 completed，且 consumes 产物键已在 artifacts。
        返回旧 task_id（若有），失败时返回 None 且不改图。
        """
        n = self.get(node_id)
        if n is None or n.status != "failed":
            return None
        done = {x.id for x in self.nodes if x.status == "completed"}
        if any(d not in done for d in n.depends_on):
            return None
        if any(c not in self.artifacts for c in n.consumes):
            return None
        old_tid = n.task_id
        prev_err = str((n.artifact or {}).get("error") or "")
        n.status = "pending"
        n.task_id = None
        art = dict(n.artifact or {})
        if prev_err:
            art["retry_from_error"] = prev_err[:500]
        art.pop("error", None)
        art["retried"] = True
        n.artifact = art
        return old_tid

    def mark_cancelled(self, node_id: str, *, reason: str = "") -> None:
        n = self.get(node_id)
        if n is None:
            return
        n.status = "cancelled"
        if reason:
            n.artifact = {**(n.artifact or {}), "cancel_reason": reason[:200]}

    def retire_node(self, node_id: str, *, reason: str = "") -> PlanNode | None:
        """从活跃列表移入 retired（重排淘汰）。"""
        n = self.get(node_id)
        if n is None:
            return None
        self.mark_cancelled(node_id, reason=reason or "retired")
        self.nodes = [x for x in self.nodes if x.id != node_id]
        self.retired.append(n)
        return n

    def summary_lines(self) -> list[str]:
        lines = [
            f"计划 {self.plan_id[:8]}… · 活跃 {len(self.nodes)} · "
            f"retired {len(self.retired)} · v{self.version} · source={self.source}"
        ]
        for n in self.nodes:
            dep = f" ←{','.join(n.depends_on)}" if n.depends_on else ""
            lines.append(f"- [{n.status}] {n.id}{dep}: {n.goal[:60]}")
        return lines
