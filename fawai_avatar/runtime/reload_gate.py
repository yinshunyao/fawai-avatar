# term:服务保活  doc:doc/功能/服务保活/说明.md
"""热重载闸门：有执行中任务时延后杀进程重启。"""
from __future__ import annotations

from pathlib import Path

# 挡自动热重载的状态（与「正在跑步骤 / 租约卡住」一致）
# waiting / paused / pending 不挡：用户已停手或仅排队时可生效
EXECUTING_STATUSES: frozenset[str] = frozenset({"running", "blocked"})


def has_active_executing_tasks(data_dir: str | Path) -> bool:
    """SQLite 中是否存在未归档的 running/blocked 任务。

    保活外层跨进程只读任务库；库不存在或读失败视为空闲（不永久挡住重载）。
    """
    root = Path(data_dir).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return False
    db = root / "tasks.sqlite"
    if not db.is_file():
        return False
    try:
        from fawai_avatar.task.store import TaskStore

        store = TaskStore(str(root))
        for status in sorted(EXECUTING_STATUSES):
            if store.list_tasks(status=status, limit=1, archived=False):
                return True
        return False
    except Exception:  # noqa: BLE001 — 闸门失败不得卡死热重载
        return False


def should_apply_code_reload(data_dir: str | Path) -> bool:
    """当前是否可立刻应用代码热重载（无执行中任务）。"""
    return not has_active_executing_tasks(data_dir)
