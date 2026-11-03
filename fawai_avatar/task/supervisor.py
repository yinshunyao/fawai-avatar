# term:总管  tool:Supervisor  doc:doc/功能/任务与事件管理/说明.md
from __future__ import annotations

import time
import uuid
from typing import Any

from fawai_avatar.task import events as ev
from fawai_avatar.task.leases import ForegroundLeaseManager, ResourceLeaseManager
from fawai_avatar.task.models import Event, ResourceItem, TaskRecord, TaskStatus
from fawai_avatar.task.store import TaskStore

_CLAIMABLE: frozenset[str] = frozenset({"running", "waiting"})
_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled", "archived"})
_RESTORABLE: frozenset[str] = frozenset(
    {"pending", "waiting", "blocked", "paused", "completed", "failed", "cancelled"}
)


class Supervisor:
    """总管：工作任务生命周期与设备租约仲裁。"""

    def __init__(self, data_dir: str) -> None:
        self._store = TaskStore(data_dir)
        self._foreground = ForegroundLeaseManager(self._store)
        self._resources = ResourceLeaseManager(self._store)

    @property
    def data_dir(self) -> str:
        return self._store.data_dir

    def create_task(
        self,
        goal: str,
        *,
        priority: int = 0,
        write_resources: list[ResourceItem] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        now = time.time()
        record = TaskRecord(
            task_id=task_id,
            goal=goal,
            status="pending",
            priority=priority,
            meta=dict(meta or {}),
            write_resources=list(write_resources or []),
            created_at=now,
            updated_at=now,
        )
        self._store.insert_task(record)
        self._store.append_event(
            task_id,
            ev.TASK_CREATED,
            f"created: {goal[:120]}",
            {"priority": priority},
        )
        return task_id

    def pause(self, task_id: str) -> bool:
        task = self._require_active(task_id)
        if task is None:
            return False
        if task.status in _TERMINAL or task.status == "paused":
            return False
        self._release_all_leases(task_id)
        return self._set_status(task_id, "paused", ev.TASK_PAUSED, "paused")

    def resume(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None or task.status != "paused":
            return False
        return self._set_status(task_id, "running", ev.TASK_RESUMED, "resumed")

    def cancel(self, task_id: str) -> bool:
        """取消任务。非终态 → cancelled；已是 completed/failed/cancelled 则保持/记为 cancelled。"""
        task = self.get_task(task_id)
        if task is None:
            return False
        if task.status == "cancelled":
            return True
        if task.status == "archived":
            return False
        if task.status in {"completed", "failed"}:
            return self._set_status(task_id, "cancelled", ev.TASK_CANCELLED, "cancelled")
        self._release_all_leases(task_id)
        return self._set_status(task_id, "cancelled", ev.TASK_CANCELLED, "cancelled")

    def delete_task(self, task_id: str) -> tuple[bool, str]:
        """硬删：终态 completed/failed/cancelled/archived 可直接删；运行中须先取消。返回 (ok, message)。"""
        task = self.get_task(task_id)
        if task is None:
            return False, "unknown task"
        if task.status not in _TERMINAL:
            return False, "任务仍在进行中，请先取消后再删除"
        self._release_all_leases(task_id)
        ok = self._store.delete_task(task_id)
        if ok:
            return True, "deleted"
        return False, "delete failed"

    def archive_task(self, task_id: str) -> tuple[bool, str]:
        """从主对话收束为归档记录。执行中不可归档。"""
        task = self.get_task(task_id)
        if task is None:
            return False, "unknown task"
        if task.status == "archived":
            return True, "already archived"
        if task.status == "running":
            return False, "执行中请先取消或等结束后再归档"
        self._release_all_leases(task_id)
        meta = dict(task.meta or {})
        meta["status_before_archive"] = task.status
        meta["archived_at"] = time.time()
        self._store.update_task_meta(task_id, meta)
        ok = self._set_status(task_id, "archived", ev.TASK_ARCHIVED, "archived")
        return (True, "archived") if ok else (False, "archive failed")

    def unarchive_task(self, task_id: str) -> tuple[bool, str]:
        """取消归档，恢复归档前状态（缺省 completed）。"""
        task = self.get_task(task_id)
        if task is None:
            return False, "unknown task"
        if task.status != "archived":
            return False, "not archived"
        meta = dict(task.meta or {})
        prev = str(meta.get("status_before_archive") or "completed")
        if prev not in _RESTORABLE:
            prev = "completed"
        meta.pop("status_before_archive", None)
        meta.pop("archived_at", None)
        self._store.update_task_meta(task_id, meta)
        ok = self._set_status(task_id, prev, ev.TASK_UNARCHIVED, f"unarchived → {prev}")  # type: ignore[arg-type]
        return (True, f"unarchived:{prev}") if ok else (False, "unarchive failed")

    def merge_task_meta(self, task_id: str, patch: dict[str, Any]) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        meta = dict(task.meta)
        meta.update(patch)
        return self._store.update_task_meta(task_id, meta)

    def replace_task_meta(self, task_id: str, meta: dict[str, Any]) -> bool:
        """整表替换 meta（可删键，如清 run_checkpoint）。"""
        if self.get_task(task_id) is None:
            return False
        return self._store.update_task_meta(task_id, dict(meta))

    def list_recoverable_task_ids(self, *, limit: int = 200) -> list[str]:
        """进程重启后应自动拉 Worker 的任务（running/waiting/blocked/pending）。

        澄清等待 / 复盘 waiting 跳过，须等用户回复后再拉。
        """
        from fawai_avatar.brain.clarification import should_skip_auto_recover
        from fawai_avatar.runtime.checkpoint import RECOVER_STATUSES

        out: list[str] = []
        for status in sorted(RECOVER_STATUSES):
            for t in self.list_tasks(status=status, limit=limit, archived=False):
                if should_skip_auto_recover(t):
                    continue
                out.append(t.task_id)
        return out

    def update_task_goal(self, task_id: str, goal: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        ok = self._store.update_task_goal(task_id, goal)
        if ok:
            self._store.append_event(
                task_id,
                ev.TASK_FOLLOWUP,
                f"goal updated: {goal[:120]}",
                {"goal_len": len(goal)},
            )
        return ok

    def requeue_task(self, task_id: str, *, reopen: bool = False) -> bool:
        """回到 pending 以便再次执行。reopen=True 时允许从 completed/failed/cancelled 重开。"""
        task = self.get_task(task_id)
        if task is None:
            return False
        if task.status == "running":
            return False
        if task.status == "archived":
            return False
        if task.status in {"completed", "failed", "cancelled"} and not reopen:
            return False
        self._release_all_leases(task_id)
        note = "reopened for follow-up" if reopen and task.status in _TERMINAL else "requeued for follow-up"
        return self._set_status(task_id, "pending", ev.TASK_STATUS, note)

    def complete(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None or task.status in _TERMINAL:
            return False
        self._release_all_leases(task_id)
        return self._set_status(task_id, "completed", ev.TASK_COMPLETED, "completed")

    def fail(self, task_id: str, *, message: str = "failed") -> bool:
        task = self.get_task(task_id)
        if task is None or task.status in _TERMINAL:
            return False
        self._release_all_leases(task_id)
        return self._set_status(task_id, "failed", ev.TASK_FAILED, message)

    def set_running(self, task_id: str) -> bool:
        """工作任务进入 running（供 Worker 调用）。"""
        task = self.get_task(task_id)
        if task is None or task.status in _TERMINAL:
            return False
        if task.status == "running":
            return True
        return self._set_status(task_id, "running", ev.TASK_STATUS, "running")

    def set_waiting(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None or task.status in _TERMINAL:
            return False
        return self._set_status(task_id, "waiting", ev.TASK_STATUS, "waiting")

    def claim_foreground(self, task_id: str) -> bool:
        if not self._ensure_claimable(task_id):
            return False
        result = self._foreground.claim(task_id)
        if result.ok:
            self._store.append_event(
                task_id,
                ev.FOREGROUND_CLAIMED,
                "foreground lease acquired",
                {},
            )
            return True
        self._store.update_task_status(task_id, "blocked")
        self._store.append_event(
            task_id,
            ev.FOREGROUND_BLOCKED,
            "foreground held by another task",
            {"holder_task_id": result.holder_task_id},
        )
        return False

    def release_foreground(self, task_id: str) -> bool:
        released = self._foreground.release(task_id)
        if released:
            self._store.append_event(
                task_id,
                ev.FOREGROUND_RELEASED,
                "foreground lease released",
                {},
            )
        return released

    def claim_resources(self, task_id: str, items: list[ResourceItem]) -> bool:
        if not items:
            return True
        if not self._ensure_claimable(task_id):
            return False
        result = self._resources.claim(task_id, items)
        if result.ok:
            self._store.append_event(
                task_id,
                ev.RESOURCE_CLAIMED,
                f"claimed {len(items)} resource(s)",
                {"items": [i.to_dict() for i in items]},
            )
            return True
        self._store.update_task_status(task_id, "blocked")
        data: dict[str, Any] = {
            "reason": result.reason,
            "holder_task_id": result.holder_task_id,
        }
        if result.conflict is not None:
            data["conflict"] = result.conflict.to_dict()
        self._store.append_event(
            task_id,
            ev.RESOURCE_BLOCKED,
            "resource lease blocked",
            data,
        )
        return False

    def release_resources(self, task_id: str, items: list[ResourceItem] | None = None) -> int:
        if items is None:
            count = self._resources.release_all(task_id)
        else:
            count = self._resources.release_items(task_id, items)
        if count:
            self._store.append_event(
                task_id,
                ev.RESOURCE_RELEASED,
                "resource lease released",
                {"count": count},
            )
        return count

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._store.get_task(task_id)

    def resolve_task_id(self, prefix: str) -> str | None:
        """完整 id 或唯一短前缀 → task_id；歧义或不存在 → None。"""
        key = (prefix or "").strip()
        if not key:
            return None
        if self.get_task(key) is not None:
            return key
        matches = [
            t.task_id
            for t in self.list_tasks(limit=100, archived=None)
            if t.task_id.startswith(key)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        q: str | None = None,
        limit: int = 200,
        archived: bool | None = False,
    ) -> list[TaskRecord]:
        """archived=False 排除归档（主对话默认）；True 仅归档；None 全部。"""
        return self._store.list_tasks(status=status, q=q, limit=limit, archived=archived)

    def recent_events(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        return self._store.recent_events(task_id=task_id, limit=limit)

    def report(self, task_id: str, message: str, data: dict[str, Any] | None = None) -> Event:
        return self._store.append_event(task_id, ev.TASK_REPORT, message, data or {})

    def holding_foreground(self, task_id: str | None) -> bool:
        """控制层闸门：当前任务是否持有前台租约。"""
        if not task_id:
            return False
        return self._foreground.holder() == task_id

    def allows_writes(self, task_id: str | None, resources: list[str]) -> bool:
        """控制层闸门：资源路径是否均已由该任务持有 write lease。"""
        if not task_id or not resources:
            return not resources
        from fawai_avatar.task.leases import normalize_resource_key

        held: set[str] = set()
        for row in self._store.list_resource_leases():
            if str(row["task_id"]) != task_id:
                continue
            if str(row.get("resource_mode") or "") != "write":
                continue
            held.add(str(row["resource_key"]))
        for raw in resources:
            key = normalize_resource_key(raw)
            if key not in held and raw not in held:
                return False
        return True

    def _set_status(
        self,
        task_id: str,
        status: TaskStatus,
        event_type: str,
        message: str,
    ) -> bool:
        ok = self._store.update_task_status(task_id, status)
        if ok:
            self._store.append_event(task_id, event_type, message, {"status": status})
        return ok

    def _require_active(self, task_id: str) -> TaskRecord | None:
        task = self.get_task(task_id)
        if task is None or task.status in _TERMINAL:
            return None
        return task

    def _ensure_claimable(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None:
            return False
        if task.status not in _CLAIMABLE:
            self._store.append_event(
                task_id,
                ev.TASK_STATUS,
                "claim rejected: task not running or waiting",
                {"status": task.status},
            )
            return False
        return True

    def _release_all_leases(self, task_id: str) -> None:
        self._foreground.release(task_id)
        self._resources.release_all(task_id)


def _self_check(data_dir: str) -> None:
    import os
    import shutil
    import tempfile

    root = os.path.join(tempfile.gettempdir(), data_dir)
    if os.path.isdir(root):
        shutil.rmtree(root)
    sup = Supervisor(root)
    t1 = sup.create_task("task one", write_resources=[ResourceItem("file", "./a.txt", "write")])
    t2 = sup.create_task("task two")
    assert sup.set_running(t1)
    assert sup.set_running(t2)
    assert sup.claim_foreground(t1)
    assert not sup.claim_foreground(t2)
    assert sup.get_task(t2).status == "blocked"
    sup.release_foreground(t1)
    assert sup.set_waiting(t2)
    sup._store.update_task_status(t2, "running")
    assert sup.claim_foreground(t2)
    sup.release_foreground(t2)
    fpath = os.path.join(root, "shared.txt")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write("x")
    assert sup.claim_resources(t1, [ResourceItem("file", fpath, "write")])
    assert not sup.claim_resources(
        t2,
        [ResourceItem("file", fpath, "write")],
    )
    assert sup.get_task(t2).status == "blocked"
    sup._store.update_task_status(t2, "running")
    assert sup.claim_resources(
        t2,
        [ResourceItem("file", fpath, "read")],
    )
    sup.release_resources(t1)
    assert sup.complete(t1)
    events = sup.recent_events(limit=5)
    assert events
    print("supervisor self-check ok", len(sup.list_tasks()))


if __name__ == "__main__":
    DATA_DIR = "fawai_avatar_task_selfcheck"  # 临时目录名（位于系统 temp 下）
    _self_check(DATA_DIR)
