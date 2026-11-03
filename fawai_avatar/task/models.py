# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TaskStatus = Literal[
    "pending",
    "running",
    "waiting",
    "blocked",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "archived",
]

TASK_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "running",
        "waiting",
        "blocked",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "archived",
    }
)

ResourceMode = Literal["read", "write"]


@dataclass
class ResourceItem:
    kind: str
    key: str
    mode: ResourceMode = "write"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceItem:
        mode = str(data.get("mode") or "write")
        if mode not in ("read", "write"):
            mode = "write"
        return cls(kind=str(data["kind"]), key=str(data["key"]), mode=mode)  # type: ignore[arg-type]


@dataclass
class TaskRecord:
    task_id: str
    goal: str
    status: TaskStatus
    priority: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    write_resources: list[ResourceItem] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "priority": self.priority,
            "meta": self.meta,
            "write_resources": [r.to_dict() for r in self.write_resources],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TaskRecord:
        wr_raw = row.get("write_resources_json") or "[]"
        meta_raw = row.get("meta_json") or "{}"
        resources = [ResourceItem.from_dict(x) for x in json.loads(wr_raw)]
        return cls(
            task_id=str(row["task_id"]),
            goal=str(row["goal"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            priority=int(row.get("priority") or 0),
            meta=json.loads(meta_raw),
            write_resources=resources,
            created_at=float(row.get("created_at") or 0),
            updated_at=float(row.get("updated_at") or 0),
        )


@dataclass
class Event:
    event_id: int
    task_id: str
    event_type: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "event_type": self.event_type,
            "message": self.message,
            "data": self.data,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Event:
        data_raw = row.get("data_json") or "{}"
        return cls(
            event_id=int(row["event_id"]),
            task_id=str(row["task_id"]),
            event_type=str(row["event_type"]),
            message=str(row.get("message") or ""),
            data=json.loads(data_raw),
            created_at=float(row.get("created_at") or 0),
        )
