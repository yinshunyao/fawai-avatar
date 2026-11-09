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
    # 可选机读预期（动作段预期闸）；见 brain/action_segment.py
    expect: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if not out.get("expect"):
            out.pop("expect", None)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        raw_expect = data.get("expect")
        expect: dict[str, Any] | None = None
        if isinstance(raw_expect, dict) and raw_expect:
            expect = dict(raw_expect)
        args = dict(data.get("args") or {})
        # 兼容把 expect 写在 args 里
        if expect is None and isinstance(args.get("expect"), dict) and args.get("expect"):
            expect = dict(args.pop("expect"))
        elif "expect" in args:
            args.pop("expect", None)
        return cls(
            kind=str(data.get("kind") or data.get("type") or "wait"),
            args=args,
            needs_foreground=bool(data.get("needs_foreground", False)),
            write_resources=[str(x) for x in (data.get("write_resources") or [])],
            expect=expect,
        )

    def evolve(self, **kwargs: Any) -> "Action":
        """复制并覆盖字段（保留 expect）。"""
        return Action(
            kind=str(kwargs["kind"]) if "kind" in kwargs else self.kind,
            args=dict(kwargs["args"]) if "args" in kwargs else dict(self.args or {}),
            needs_foreground=(
                bool(kwargs["needs_foreground"])
                if "needs_foreground" in kwargs
                else self.needs_foreground
            ),
            write_resources=(
                [str(x) for x in kwargs["write_resources"]]
                if "write_resources" in kwargs
                else list(self.write_resources or [])
            ),
            expect=(
                dict(kwargs["expect"])
                if "expect" in kwargs and isinstance(kwargs.get("expect"), dict)
                else (dict(self.expect) if self.expect else None)
            ),
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
    # 观察参数：如 {"obs_screen": {"screen_target": "frontmost"|"main"|"index:2"|"all"}}
    observe_args: dict[str, Any] = field(default_factory=dict)
    # 任务短标题（首轮命名；可空）
    title: str = ""
    # 本步业务意图（步骤簇头；≠ title）
    step_label: str = ""
    # 步型：sense | act_serial | act_parallel | analyze | finalize（见步骤分类与执行流）
    step_kind: str = ""
    # 成功后观察反省档：reflect | brief | act（空=跟现网默认）；见步级执行档
    step_run: str = ""
    # 主脑委派：{"tier":"fast","goal":"...","stop_when":"...","max_steps":N}；空=不委派
    delegate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "thought": self.thought,
            "actions": [a.to_dict() for a in self.actions],
            "done": self.done,
            "status": self.status,
            "raw": self.raw,
            "observe_kinds": list(self.observe_kinds),
            "title": self.title,
            "step_label": self.step_label,
            "step_kind": self.step_kind,
        }
        if self.step_run:
            out["step_run"] = self.step_run
        if self.observe_args:
            out["observe_args"] = dict(self.observe_args)
        if self.delegate:
            out["delegate"] = dict(self.delegate)
        return out


@dataclass
class ExecResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
