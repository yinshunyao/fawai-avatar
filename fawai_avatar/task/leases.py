# term:设备租约  tool:ForegroundLeaseManager,ResourceLeaseManager  doc:doc/功能/任务与事件管理/说明.md
from __future__ import annotations

import os
from dataclasses import dataclass

from fawai_avatar.task.models import ResourceItem
from fawai_avatar.task.store import TaskStore


def normalize_resource_key(key: str) -> str:
    """路径型 key 归一化为绝对路径。"""
    stripped = key.strip()
    if not stripped:
        return key
    looks_like_path = (
        stripped.startswith(("/", "./", "../", "~"))
        or os.sep in stripped
        or (os.altsep and os.altsep in stripped)
    )
    if not looks_like_path:
        return stripped
    expanded = os.path.expanduser(stripped)
    return os.path.abspath(expanded)


def normalize_resource_item(item: ResourceItem) -> ResourceItem:
    return ResourceItem(kind=item.kind, key=normalize_resource_key(item.key), mode=item.mode)


@dataclass
class ClaimResult:
    ok: bool
    reason: str = ""
    holder_task_id: str | None = None
    conflict: ResourceItem | None = None


class ForegroundLeaseManager:
    """foreground_lease：前台键鼠，全局唯一。"""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def holder(self) -> str | None:
        return self._store.get_foreground_holder()

    def claim(self, task_id: str) -> ClaimResult:
        holder = self._store.get_foreground_holder()
        if holder is not None and holder != task_id:
            return ClaimResult(
                ok=False,
                reason="foreground_held_by_other",
                holder_task_id=holder,
            )
        if holder == task_id:
            return ClaimResult(ok=True)
        if self._store.insert_foreground_lease(task_id):
            return ClaimResult(ok=True)
        holder = self._store.get_foreground_holder()
        return ClaimResult(
            ok=False,
            reason="foreground_held_by_other",
            holder_task_id=holder,
        )

    def release(self, task_id: str) -> bool:
        return self._store.delete_foreground_lease(task_id)


class ResourceLeaseManager:
    """resource_lease：后台资源；写写冲突，写读允许。"""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def _active_leases(self) -> list[tuple[str, ResourceItem]]:
        rows = self._store.list_resource_leases()
        out: list[tuple[str, ResourceItem]] = []
        for row in rows:
            item = ResourceItem(
                kind=str(row["resource_kind"]),
                key=str(row["resource_key"]),
                mode=str(row["resource_mode"]),  # type: ignore[arg-type]
            )
            out.append((str(row["task_id"]), normalize_resource_item(item)))
        return out

    def conflicts_with(
        self,
        task_id: str,
        items: list[ResourceItem],
    ) -> ClaimResult:
        normalized = [normalize_resource_item(i) for i in items]
        for other_id, held in self._active_leases():
            if other_id == task_id:
                continue
            for req in normalized:
                if req.kind != held.kind or req.key != held.key:
                    continue
                if req.mode == "write" and held.mode == "write":
                    return ClaimResult(
                        ok=False,
                        reason="write_write_conflict",
                        holder_task_id=other_id,
                        conflict=held,
                    )
        return ClaimResult(ok=True)

    def claim(self, task_id: str, items: list[ResourceItem]) -> ClaimResult:
        check = self.conflicts_with(task_id, items)
        if not check.ok:
            return check
        normalized = [normalize_resource_item(i) for i in items]
        self._store.insert_resource_leases(task_id, normalized)
        return ClaimResult(ok=True)

    def release_all(self, task_id: str) -> int:
        return self._store.delete_resource_leases(task_id)

    def release_items(self, task_id: str, items: list[ResourceItem]) -> int:
        keys = [(normalize_resource_item(i).kind, normalize_resource_item(i).key) for i in items]
        return self._store.delete_resource_leases_for_keys(task_id, keys)
