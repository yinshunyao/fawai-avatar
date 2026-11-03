# term:任务与事件管理  tool:TaskStore  doc:doc/功能/任务与事件管理/说明.md
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fawai_avatar.task.models import Event, ResourceItem, TaskRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}',
    write_resources_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    lease_kind TEXT NOT NULL,
    resource_kind TEXT,
    resource_key TEXT,
    resource_mode TEXT,
    created_at REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_foreground_unique
    ON leases(lease_kind) WHERE lease_kind = 'foreground';

CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id);
CREATE INDEX IF NOT EXISTS idx_leases_resource ON leases(resource_kind, resource_key);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, created_at);
"""


class TaskStore:
    """SQLite 持久化：工作任务、租约、事件。"""

    def __init__(self, data_dir: str) -> None:
        # 绝对路径：避免进程 cwd 变化后相对 data/ 打不开库
        root = os.path.abspath(os.path.expanduser(str(data_dir or "data")))
        self._data_dir = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)
        self._db_path = os.path.join(root, "tasks.sqlite")
        self._init_db()

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @property
    def db_path(self) -> str:
        return self._db_path

    def _open(self) -> sqlite3.Connection:
        os.makedirs(self._data_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # 目录被删后重建、或库文件丢失时，sqlite 会建空文件；须补 schema
        has_tasks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if has_tasks is None:
            conn.executescript(_SCHEMA)
            conn.commit()
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """打开连接；离开 with 时必须 close。

        注意：`with sqlite3.connect(...)` / `with conn` 只提交事务，**不会**关闭
        连接；在 macOS 默认 soft nofile=256 下会很快 EMFILE。
        """
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()

    def insert_task(self, record: TaskRecord) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        task_id, goal, status, priority, meta_json,
                        write_resources_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.task_id,
                        record.goal,
                        record.status,
                        record.priority,
                        json.dumps(record.meta, ensure_ascii=False),
                        json.dumps([r.to_dict() for r in record.write_resources], ensure_ascii=False),
                        record.created_at,
                        record.updated_at,
                    ),
                )
                conn.commit()

    def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        updated_at: float | None = None,
    ) -> bool:
        ts = updated_at if updated_at is not None else time.time()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (status, ts, task_id),
                )
                conn.commit()
                return cur.rowcount > 0

    def update_task_meta(
        self,
        task_id: str,
        meta: dict[str, Any],
        *,
        updated_at: float | None = None,
    ) -> bool:
        """整表替换 meta_json（调用方先 merge）。"""
        ts = updated_at if updated_at is not None else time.time()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE tasks SET meta_json = ?, updated_at = ? WHERE task_id = ?",
                    (json.dumps(meta, ensure_ascii=False), ts, task_id),
                )
                conn.commit()
                return cur.rowcount > 0

    def update_task_goal(
        self,
        task_id: str,
        goal: str,
        *,
        updated_at: float | None = None,
    ) -> bool:
        ts = updated_at if updated_at is not None else time.time()
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE tasks SET goal = ?, updated_at = ? WHERE task_id = ?",
                    (goal, ts, task_id),
                )
                conn.commit()
                return cur.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        """硬删任务行与其事件、租约。调用方须已校验为终态（completed/failed/cancelled）。"""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM leases WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
                cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        if row is None:
            return None
        return TaskRecord.from_row(dict(row))

    def list_tasks(
        self,
        *,
        status: str | None = None,
        q: str | None = None,
        limit: int = 200,
        archived: bool | None = False,
    ) -> list[TaskRecord]:
        """archived=False 排除归档；True 仅归档；None 不过滤归档。"""
        query = (q or "").strip().lower()
        fetch_n = max(limit * 5, limit) if query or archived is False else limit
        with self._lock:
            with self._connect() as conn:
                if status is None:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks
                        ORDER BY updated_at DESC, created_at DESC
                        LIMIT ?
                        """,
                        (fetch_n,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM tasks WHERE status = ?
                        ORDER BY updated_at DESC, created_at DESC
                        LIMIT ?
                        """,
                        (status, fetch_n),
                    ).fetchall()
        records = [TaskRecord.from_row(dict(r)) for r in rows]
        if status is None:
            if archived is False:
                records = [r for r in records if r.status != "archived"]
            elif archived is True:
                records = [r for r in records if r.status == "archived"]
        if query:
            filtered: list[TaskRecord] = []
            for rec in records:
                title = str((rec.meta or {}).get("title") or "")
                blob = f"{rec.task_id} {title} {rec.goal} {rec.status}".lower()
                if query in blob or any(tok in blob for tok in query.split() if tok):
                    filtered.append(rec)
                if len(filtered) >= limit:
                    break
            return filtered
        return records[:limit]

    def append_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> Event:
        ts = time.time()
        payload = data or {}
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO events (task_id, event_type, message, data_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        event_type,
                        message,
                        json.dumps(payload, ensure_ascii=False),
                        ts,
                    ),
                )
                event_id = int(cur.lastrowid)
                conn.commit()
        return Event(
            event_id=event_id,
            task_id=task_id,
            event_type=event_type,
            message=message,
            data=payload,
            created_at=ts,
        )

    def recent_events(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        with self._lock:
            with self._connect() as conn:
                if task_id is None:
                    rows = conn.execute(
                        """
                        SELECT * FROM events
                        ORDER BY created_at DESC, event_id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM events WHERE task_id = ?
                        ORDER BY created_at DESC, event_id DESC
                        LIMIT ?
                        """,
                        (task_id, limit),
                    ).fetchall()
        return [Event.from_row(dict(r)) for r in rows]

    # --- leases ---

    def get_foreground_holder(self) -> str | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT task_id FROM leases
                    WHERE lease_kind = 'foreground'
                    LIMIT 1
                    """,
                ).fetchone()
        if row is None:
            return None
        return str(row["task_id"])

    def insert_foreground_lease(self, task_id: str) -> bool:
        ts = time.time()
        with self._lock:
            with self._connect() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO leases (
                            task_id, lease_kind, resource_kind, resource_key,
                            resource_mode, created_at
                        ) VALUES (?, 'foreground', NULL, NULL, NULL, ?)
                        """,
                        (task_id, ts),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    return False

    def delete_foreground_lease(self, task_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM leases
                    WHERE lease_kind = 'foreground' AND task_id = ?
                    """,
                    (task_id,),
                )
                conn.commit()
                return cur.rowcount > 0

    def list_resource_leases(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT task_id, resource_kind, resource_key, resource_mode
                    FROM leases
                    WHERE lease_kind = 'resource'
                    """,
                ).fetchall()
        return [dict(r) for r in rows]

    def insert_resource_leases(
        self,
        task_id: str,
        items: list[ResourceItem],
    ) -> None:
        ts = time.time()
        with self._lock:
            with self._connect() as conn:
                for item in items:
                    conn.execute(
                        """
                        INSERT INTO leases (
                            task_id, lease_kind, resource_kind, resource_key,
                            resource_mode, created_at
                        ) VALUES (?, 'resource', ?, ?, ?, ?)
                        """,
                        (task_id, item.kind, item.key, item.mode, ts),
                    )
                conn.commit()

    def delete_resource_leases(self, task_id: str) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM leases
                    WHERE lease_kind = 'resource' AND task_id = ?
                    """,
                    (task_id,),
                )
                conn.commit()
                return cur.rowcount

    def delete_resource_leases_for_keys(
        self,
        task_id: str,
        keys: list[tuple[str, str]],
    ) -> int:
        if not keys:
            return 0
        removed = 0
        with self._lock:
            with self._connect() as conn:
                for kind, key in keys:
                    cur = conn.execute(
                        """
                        DELETE FROM leases
                        WHERE lease_kind = 'resource'
                          AND task_id = ?
                          AND resource_kind = ?
                          AND resource_key = ?
                        """,
                        (task_id, kind, key),
                    )
                    removed += cur.rowcount
                conn.commit()
        return removed
