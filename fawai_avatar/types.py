# term:操作分身  doc:doc/议题/操作分身三层/结论.md
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Observation:
    """观察层输出：LLM 可读界面摘要。"""

    summary: str
    image_path: str | None = None
    width: int | None = None
    height: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    """控制层原子动作。"""

    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    needs_foreground: bool = False
    write_resources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            kind=str(data.get("kind") or data.get("type") or "wait"),
            args=dict(data.get("args") or {}),
            needs_foreground=bool(data.get("needs_foreground", False)),
            write_resources=[str(x) for x in (data.get("write_resources") or [])],
        )


@dataclass
class Plan:
    """大脑输出：下一步计划。"""

    thought: str
    actions: list[Action] = field(default_factory=list)
    done: bool = False
    status: str = "continue"  # continue | success | failed | need_input
    raw: dict[str, Any] = field(default_factory=dict)
    # 下一步环境观察方式（经硬闸门求交）；不含 obs_tool
    observe_kinds: list[str] = field(default_factory=list)
    # 任务短标题（首轮命名；可空）
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought": self.thought,
            "actions": [a.to_dict() for a in self.actions],
            "done": self.done,
            "status": self.status,
            "raw": self.raw,
            "observe_kinds": list(self.observe_kinds),
            "title": self.title,
        }


@dataclass
class ExecResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
