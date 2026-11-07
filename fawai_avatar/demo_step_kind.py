# term:步骤分类与执行流  doc:doc/功能/步骤分类与执行流/说明.md
"""步型：跳过浅观察、启发式回填、并行准入。"""
from __future__ import annotations

from fawai_avatar.brain.planner import _plan_from_payload
from fawai_avatar.brain.step_kind import (
    batch_allows_parallel,
    coerce_observe_kinds_skip_shallow,
    infer_step_kind,
    normalize_step_kind,
    resolve_step_kind,
    should_execute_parallel,
    should_skip_shallow_env_observe,
)
from fawai_avatar.types import Action


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name} failed" + (f": {detail}" if detail else ""))
    print(f"ok: {name}")


def main() -> None:
    _ok("norm_serial", normalize_step_kind("serial") == "act_serial")
    _ok("norm_parallel", normalize_step_kind("parallel") == "act_parallel")
    _ok("norm_sense", normalize_step_kind("observe") == "sense")

    reads = [
        Action(kind="fs_read", args={"path": "a.md"}),
        Action(kind="fs_grep", args={"pattern": "x"}),
    ]
    _ok("batch_parallel_ok", batch_allows_parallel(reads))
    _ok(
        "infer_parallel",
        infer_step_kind(status="continue", actions=reads) == "act_parallel",
    )
    write = [Action(kind="fs_patch", args={"path": "a"}, write_resources=["a"])]
    _ok("batch_write_no_parallel", not batch_allows_parallel(write))
    two_writes = [
        Action(kind="fs_patch", args={"path": "a.md"}, write_resources=["a.md"]),
        Action(kind="fs_patch", args={"path": "b.md"}, write_resources=["b.md"]),
    ]
    _ok("batch_two_writes_parallel", batch_allows_parallel(two_writes))
    same_path = [
        Action(kind="fs_read", args={"path": "a.md"}),
        Action(kind="fs_patch", args={"path": "a.md"}, write_resources=["a.md"]),
    ]
    _ok("batch_same_path_no_parallel", not batch_allows_parallel(same_path))
    with_shell = [
        Action(kind="fs_grep", args={"pattern": "x"}),
        Action(kind="shell", args={"cmd": "echo hi"}),
    ]
    _ok("batch_shell_no_parallel", not batch_allows_parallel(with_shell))
    _ok(
        "infer_serial_write",
        infer_step_kind(status="continue", actions=write) == "act_serial",
    )
    _ok(
        "explicit_parallel_downgrade",
        resolve_step_kind(
            "act_parallel", status="continue", actions=write
        )
        == "act_serial",
    )
    _ok(
        "should_parallel",
        should_execute_parallel("act_parallel", reads),
    )
    _ok(
        "should_not_parallel_serial_kind",
        not should_execute_parallel("act_serial", reads),
    )

    _ok(
        "skip_after_act",
        should_skip_shallow_env_observe(
            ["obs_fs"], prev_step_kind="act_serial"
        ),
    )
    _ok(
        "coerce_none",
        coerce_observe_kinds_skip_shallow(
            ["obs_fs"], prev_step_kind="act_serial"
        )
        == ["none"],
    )
    _ok(
        "keep_screen",
        not should_skip_shallow_env_observe(
            ["obs_screen"], prev_step_kind="act_serial"
        ),
    )
    _ok(
        "no_skip_first",
        not should_skip_shallow_env_observe(None, prev_step_kind=None),
    )

    plan = _plan_from_payload(
        {
            "thought": "读配置",
            "status": "continue",
            "done": False,
            "step_kind": "act_serial",
            "actions": [
                {"kind": "fs_read", "args": {"path": "x.md"}},
            ],
        }
    )
    _ok("plan_step_kind", plan.step_kind == "act_serial")
    _ok("plan_default_obs_none", plan.observe_kinds == ["none"], str(plan.observe_kinds))

    plan_par = _plan_from_payload(
        {
            "thought": "并行搜",
            "status": "continue",
            "actions": [
                {"kind": "fs_grep", "args": {"pattern": "a"}},
                {"kind": "fs_glob", "args": {"glob": "*.py"}},
            ],
        }
    )
    _ok("infer_plan_parallel", plan_par.step_kind == "act_parallel")
    _ok(
        "infer_plan_obs_none",
        plan_par.observe_kinds == ["none"],
        str(plan_par.observe_kinds),
    )

    plan_fin = _plan_from_payload(
        {"thought": "答用户", "status": "success", "done": True, "actions": []}
    )
    _ok("finalize", plan_fin.step_kind == "finalize")

    print("demo_step_kind: all ok")


if __name__ == "__main__":
    main()
