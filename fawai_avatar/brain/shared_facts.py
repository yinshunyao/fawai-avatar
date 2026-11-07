# term:任务图  doc:doc/功能/复杂任务计划模式/说明.md
"""出图时标出的多节点共用事实：只生产一次，原文注入消费节点。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fawai_avatar.brain.durable_memory import durable_memory_text
from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode
from fawai_avatar.workspace.py_session import session_root

_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,39}$")
_INLINE_MAX = 1500
_HINTS = ("host", "port", "路径", "path", "任务", "日志", "log")


@dataclass
class SharedFact:
    key: str
    producer: str
    consumers: list[str] = field(default_factory=list)
    what: str = ""
    text: str = ""
    path: str = ""
    line: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "producer": self.producer,
            "consumers": list(self.consumers),
            "what": self.what,
            "text": self.text,
            "path": self.path,
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SharedFact | None:
        key = str(data.get("key") or "").strip()
        producer = str(data.get("producer") or "").strip()
        if not _KEY_RE.match(key) or not producer:
            return None
        consumers = [
            str(x).strip()
            for x in (data.get("consumers") or [])
            if str(x).strip() and str(x).strip() != producer
        ]
        seen: list[str] = []
        for cid in consumers:
            if cid not in seen:
                seen.append(cid)
        return cls(
            key=key,
            producer=producer,
            consumers=seen,
            what=str(data.get("what") or "").strip()[:80],
            text=str(data.get("text") or ""),
            path=str(data.get("path") or ""),
            line=str(data.get("line") or "")[:160],
        )


def shared_facts_from_raw(raw: Any) -> list[SharedFact]:
    if not isinstance(raw, list):
        return []
    out: list[SharedFact] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        fact = SharedFact.from_dict(item)
        if fact is None or fact.key in seen:
            continue
        seen.add(fact.key)
        out.append(fact)
    return out


def _depends_on_node(nodes: dict[str, PlanNode], start: str, target: str) -> bool:
    """沿 depends_on 从 start 能否走到 target。"""
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        node = nodes.get(cur)
        if node is not None:
            stack.extend(node.depends_on)
    return False


def copy_shared_facts(src: PlanGraph, dst: PlanGraph) -> None:
    """修订后的图沿用仍对得上节点的共用事实。"""
    dst.shared_facts = shared_facts_from_raw(
        [fact.to_dict() for fact in (src.shared_facts or [])]
    )
    apply_shared_facts(dst)


def apply_shared_facts(graph: PlanGraph, raw: Any | None = None) -> None:
    """把共用事实钉到图上，并让消费节点依赖唯一生产节点。"""
    facts = graph.shared_facts if raw is None else shared_facts_from_raw(raw)
    nodes = {n.id: n for n in graph.nodes}
    kept: list[SharedFact] = []
    for fact in facts:
        producer = nodes.get(fact.producer)
        if producer is None:
            continue
        consumers = [cid for cid in fact.consumers if cid in nodes]
        fact.consumers = consumers
        if fact.key not in producer.produces:
            producer.produces.append(fact.key)
        for cid in consumers:
            if _depends_on_node(nodes, fact.producer, cid):
                continue
            node = nodes[cid]
            if fact.producer not in node.depends_on:
                node.depends_on.append(fact.producer)
            if fact.key not in node.consumes:
                node.consumes.append(fact.key)
        kept.append(fact)
    graph.shared_facts = kept


def clear_shared_facts_produced_by(graph: PlanGraph, node_id: str) -> None:
    for fact in graph.shared_facts:
        if fact.producer != node_id:
            continue
        fact.text = ""
        fact.path = ""
        fact.line = ""
        graph.artifacts.pop(fact.key, None)


def _one_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in lines:
        low = line.lower()
        if any(hint in low or hint in line for hint in _HINTS):
            return line[:160]
    if lines:
        return lines[0][:160]
    return (text or "").strip()[:160]


def _spill(data_dir: str, task_id: str, key: str, text: str) -> str:
    root = session_root(data_dir, task_id)
    folder = root / "shared"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", key)[:40]
    path = folder / f"{safe}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def fill_shared_facts_from_node(
    graph: PlanGraph,
    node_id: str,
    final_thought: str,
    *,
    data_dir: str,
    task_id: str,
) -> None:
    """生产节点完成后，把结论原文写入共用键。过长则落文件。"""
    body = durable_memory_text(final_thought or "").strip()
    if not body:
        body = "生产节点已完成，但没有留下结论。下一步若需要具体事实，按原路径定向再读。"
    for fact in graph.shared_facts:
        if fact.producer != node_id:
            continue
        if len(body) > _INLINE_MAX:
            try:
                fact.path = _spill(data_dir, task_id, fact.key, body)
                fact.line = _one_line(body)
                fact.text = ""
            except Exception:
                fact.path = ""
                fact.line = ""
                fact.text = body[:_INLINE_MAX]
        else:
            fact.path = ""
            fact.line = ""
            fact.text = body
        injected = inject_text(fact)
        if injected:
            graph.artifacts[fact.key] = injected


def inject_text(fact: SharedFact) -> str:
    if fact.path:
        line = fact.line or "见文件"
        return f"{line}\n全文 path={fact.path}"
    return fact.text


def inject_for(facts: list[SharedFact] | None, key: str) -> str:
    for fact in facts or []:
        if fact.key == key:
            return inject_text(fact)
    return ""
