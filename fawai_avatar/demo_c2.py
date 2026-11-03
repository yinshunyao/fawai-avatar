# term:完成任务  doc:doc/功能/完成任务/说明.md
"""C2 验收：用例 A 写入 / B 读取；默认 mock 大脑（可 FAWAI_C2_LIVE=1 走真 LLM）。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.capabilities import looks_like_file_goal, validate_plan
from fawai_avatar.brain.planner import Brain
from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action, Observation, Plan


def _assert_no_gui(plan: Plan, goal: str) -> None:
    err = validate_plan(goal, plan)
    assert err is None, err
    for action in plan.actions:
        assert action.kind not in {"click", "type", "hotkey"}, action


def run_case(
    *,
    label: str,
    goal: str,
    work_root: Path,
    data_dir: Path,
    expect_kinds: set[str],
    live: bool,
) -> dict:
    print(f"== {label} ==")
    print({"goal": goal, "live": live})
    if not live:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"
    else:
        os.environ.pop("FAWAI_BRAIN_MOCK", None)

    assert looks_like_file_goal(goal)

    supervisor = Supervisor(str(data_dir))
    task_id = supervisor.create_task(goal)
    brain = Brain()
    # 先看首步规划是否选型正确
    first = brain.plan(goal, Observation(summary="capture disabled for c2 file task"))
    print("first_plan", json.dumps(first.to_dict(), ensure_ascii=False))
    _assert_no_gui(first, goal)
    kinds = {a.kind for a in first.actions}
    assert kinds & expect_kinds, f"expected one of {expect_kinds}, got {kinds}"

    loop = WorkerLoop(
        supervisor,
        brain=brain,
        controller=Controller(dry_run=False, supervisor=supervisor),
        max_steps=4,
        capture=False,
    )
    # 让后台工具落到本用例 work_root
    loop.controller._background.fs  # noqa: B018 — touch
    from fawai_avatar.control.background import BackgroundTools

    loop.controller._background = BackgroundTools(
        work_root=work_root,
        data_dir=data_dir,
        auto_version=False,
    )
    out = loop.run(task_id)
    print("result", json.dumps({k: out[k] for k in ("ok", "error", "task_id") if k in out}, ensure_ascii=False))
    assert out.get("ok"), out
    return out


def main(*, live: bool) -> None:
    # 校验：文件目标 + GUI → 违规
    bad = Plan(
        thought="click instead",
        actions=[Action(kind="click", args={"x": 1, "y": 2}, needs_foreground=True)],
        done=False,
        status="continue",
    )
    assert validate_plan("写入 notes/c2_hello.txt 内容 hello", bad)

    with tempfile.TemporaryDirectory(prefix="fawai-c2-") as tmp:
        root = Path(tmp)
        work = root / "work"
        data = root / "data"
        work.mkdir()
        data.mkdir()
        os.environ["FAWAI_WORK_ROOT"] = str(work)
        os.environ["FAWAI_DATA_DIR"] = str(data)

        run_case(
            label="A write",
            goal="在工作区写入 notes/c2_hello.txt 内容 hello",
            work_root=work,
            data_dir=data,
            expect_kinds={"fs_write"},
            live=live,
        )
        written = work / "notes" / "c2_hello.txt"
        assert written.is_file(), written
        assert "hello" in written.read_text(encoding="utf-8")

        run_case(
            label="B read",
            goal="读取 notes/c2_hello.txt 并告诉我内容",
            work_root=work,
            data_dir=data,
            expect_kinds={"fs_read"},
            live=live,
        )

        # 用例 C：误导 GUI，仍须 fs_write
        run_case(
            label="C mislead GUI",
            goal="用鼠标在桌面画个圈完成写文件 notes/c2_mislead.txt 内容 hello",
            work_root=work,
            data_dir=data,
            expect_kinds={"fs_write"},
            live=live,
        )
        assert "hello" in (work / "notes" / "c2_mislead.txt").read_text(encoding="utf-8")

        # 超清单：须 failed，不得假 success
        from fawai_avatar.brain.capabilities import is_beyond_capability, mock_plan_for_goal

        beyond_goal = "安装未登记的神秘 App 并完成银行实名认证登录"
        assert is_beyond_capability(beyond_goal)
        if live:
            os.environ.pop("FAWAI_BRAIN_MOCK", None)
            beyond_plan = Brain().plan(
                beyond_goal, Observation(summary="capture disabled for beyond")
            )
        else:
            beyond_plan = mock_plan_for_goal(beyond_goal)
        assert beyond_plan.status == "failed", beyond_plan.to_dict()
        assert validate_plan(beyond_goal, beyond_plan) is None
        print("beyond", json.dumps(beyond_plan.to_dict(), ensure_ascii=False))

    print("C2 acceptance A/B/C/beyond ok")


if __name__ == "__main__":
    LIVE = os.environ.get("FAWAI_C2_LIVE", "").strip() in ("1", "true", "yes")
    main(live=LIVE)
