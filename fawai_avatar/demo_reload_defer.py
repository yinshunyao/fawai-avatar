# term:服务保活  doc:doc/测试/系统/热重载延后/说明.md
"""回归：热重载闸门 — running/blocked 挡重载；waiting/取消后放行。"""
from __future__ import annotations

import tempfile

from fawai_avatar.runtime.reload_gate import (
    EXECUTING_STATUSES,
    has_active_executing_tasks,
    should_apply_code_reload,
)
from fawai_avatar.task.supervisor import Supervisor


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_gate_idle_and_busy(data_dir: str) -> None:
    _assert(EXECUTING_STATUSES == frozenset({"running", "blocked"}), "executing set")
    _assert(not has_active_executing_tasks(data_dir), "empty dir idle")
    _assert(should_apply_code_reload(data_dir), "empty dir apply")

    # 无库文件路径
    missing = str(tempfile.mkdtemp(prefix="fawai-reload-missing-"))
    _assert(not has_active_executing_tasks(missing), "missing db idle")

    sup = Supervisor(data_dir)
    pending_id = sup.create_task("reload-defer-pending")
    _assert(not has_active_executing_tasks(data_dir), "pending does not block")

    run_id = sup.create_task("reload-defer-running")
    _assert(sup.set_running(run_id), "set running")
    _assert(has_active_executing_tasks(data_dir), "running blocks")
    _assert(not should_apply_code_reload(data_dir), "running no apply")

    wait_id = sup.create_task("reload-defer-waiting")
    _assert(sup.set_waiting(wait_id), "set waiting")
    # 仍有 running → 继续挡
    _assert(has_active_executing_tasks(data_dir), "running still blocks with waiting peer")

    _assert(sup.cancel(run_id), "cancel running")
    # 仅 waiting / pending → 不挡
    _assert(not has_active_executing_tasks(data_dir), "waiting/pending do not block")
    _assert(should_apply_code_reload(data_dir), "idle apply after cancel")

    # blocked 挡重载
    a = sup.create_task("reload-defer-fg-a")
    b = sup.create_task("reload-defer-fg-b")
    _assert(sup.set_running(a) and sup.set_running(b), "two running")
    _assert(sup.claim_foreground(a), "a holds foreground")
    _assert(not sup.claim_foreground(b), "b blocked on foreground")
    _assert(sup.get_task(b) is not None and sup.get_task(b).status == "blocked", "b blocked")
    _assert(has_active_executing_tasks(data_dir), "blocked blocks reload")

    _assert(sup.cancel(a) and sup.cancel(b), "cancel blocked pair")
    _assert(sup.cancel(wait_id) and sup.cancel(pending_id), "cancel rest")
    _assert(not has_active_executing_tasks(data_dir), "all cancelled idle")
    _assert(should_apply_code_reload(data_dir), "final apply ok")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fawai-reload-defer-") as tmp:
        test_gate_idle_and_busy(tmp)
    print("demo_reload_defer ok")


if __name__ == "__main__":
    main()
