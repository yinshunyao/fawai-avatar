# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""Cursor Agent 连接配置：本机 SQLite，与 llm_settings 分库。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
"""

_KEY_ENABLED = "enabled"
_KEY_API_KEY = "api_key"
_KEY_MODEL_ID = "model_id"
_KEY_MODE = "mode"
_KEY_AUTO_ACCEPT = "auto_accept"
_KEY_MODELS_JSON = "models_json"
_KEY_MODELS_FETCHED_AT = "models_fetched_at"
_KEY_MODELS_SOURCE = "models_source"

_store_lock = threading.RLock()
_cached: "CursorAgentSettingsStore | None" = None


def _data_dir() -> str:
    from fawai_avatar.config import load_dotenv, _env

    load_dotenv()
    return str(Path(_env("FAWAI_DATA_DIR") or "data").expanduser().resolve())


class CursorAgentSettingsStore:
    """SQLite：启用开关 / API Key / 默认模型 / 模型目录缓存 / agent 登记。"""

    def __init__(self, data_dir: str | None = None) -> None:
        from fawai_avatar.data_dir import resolve_data_dir

        root = resolve_data_dir(data_dir, default=_data_dir())
        self._data_dir = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)
        self._db_path = os.path.join(root, "cursor_agent_settings.sqlite")
        self._init_db()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _open(self) -> sqlite3.Connection:
        os.makedirs(self._data_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
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

    def _meta_get(self, conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _meta_set(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def is_enabled(self) -> bool:
        with self._lock:
            with self._connect() as conn:
                return self._meta_get(conn, _KEY_ENABLED).strip() in {"1", "true", "yes", "on"}

    def api_key(self) -> str:
        with self._lock:
            with self._connect() as conn:
                return self._meta_get(conn, _KEY_API_KEY).strip()

    def model_id(self) -> str:
        with self._lock:
            with self._connect() as conn:
                return self._meta_get(conn, _KEY_MODEL_ID).strip() or "composer-2.5"

    def mode(self) -> str:
        from fawai_avatar.cursor_agent.runtime import normalize_mode

        with self._lock:
            with self._connect() as conn:
                return normalize_mode(self._meta_get(conn, _KEY_MODE))

    def auto_accept(self) -> bool:
        with self._lock:
            with self._connect() as conn:
                return self._meta_get(conn, _KEY_AUTO_ACCEPT).strip() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }

    def has_key(self) -> bool:
        """兼容旧调用；请用 auth.resolve_api_key / auth_snapshot。"""
        return bool(self.api_key())

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        model_id: str | None = None,
        mode: str | None = None,
        auto_accept: bool | None = None,
    ) -> None:
        # api_key 仅作遗留兼容；正式登录走 ~/.cursor/sdk/auth.json，UI 不再提交 Key。
        from fawai_avatar.cursor_agent.runtime import normalize_mode

        with self._lock:
            with self._connect() as conn:
                if enabled is not None:
                    self._meta_set(conn, _KEY_ENABLED, "1" if enabled else "0")
                if clear_api_key:
                    self._meta_set(conn, _KEY_API_KEY, "")
                elif isinstance(api_key, str) and api_key.strip():
                    self._meta_set(conn, _KEY_API_KEY, api_key.strip())
                if model_id is not None:
                    self._meta_set(conn, _KEY_MODEL_ID, str(model_id).strip())
                if mode is not None:
                    self._meta_set(conn, _KEY_MODE, normalize_mode(mode))
                if auto_accept is not None:
                    self._meta_set(conn, _KEY_AUTO_ACCEPT, "1" if auto_accept else "0")
                conn.commit()

    def set_model_catalog(
        self,
        models: list[dict[str, Any]],
        *,
        source: str = "cursor.models.list",
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                self._meta_set(conn, _KEY_MODELS_JSON, json.dumps(models, ensure_ascii=False))
                self._meta_set(conn, _KEY_MODELS_FETCHED_AT, str(time.time()))
                self._meta_set(conn, _KEY_MODELS_SOURCE, source)
                conn.commit()

    def model_catalog(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                raw = self._meta_get(conn, _KEY_MODELS_JSON)
                fetched = self._meta_get(conn, _KEY_MODELS_FETCHED_AT)
                source = self._meta_get(conn, _KEY_MODELS_SOURCE)
        models: list[dict[str, Any]] = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    models = [m for m in parsed if isinstance(m, dict)]
            except json.JSONDecodeError:
                models = []
        try:
            fetched_at = float(fetched) if fetched else 0.0
        except ValueError:
            fetched_at = 0.0
        return {
            "models": models,
            "count": len(models),
            "fetched_at": fetched_at,
            "source": source or "",
        }

    def settings_payload(self) -> dict[str, Any]:
        from fawai_avatar.cursor_agent import auth as ca_auth

        catalog = self.model_catalog()
        snap = ca_auth.auth_snapshot()
        has_auth = bool(snap.get("logged_in"))
        return {
            "ok": True,
            "enabled": self.is_enabled(),
            "has_auth": has_auth,
            "has_key": has_auth,
            "api_key_set": has_auth,
            "auth": snap,
            "model_id": self.model_id(),
            "mode": self.mode(),
            "auto_accept": self.auto_accept(),
            "models": catalog["models"],
            "models_count": catalog["count"],
            "models_fetched_at": catalog["fetched_at"],
            "models_source": catalog["source"],
            "db_path": self._db_path,
        }

    def upsert_agent(
        self,
        agent_id: str,
        *,
        task_id: str = "",
        cwd: str = "",
        model_id: str = "",
        title: str = "",
        meta: dict[str, Any] | None = None,
        archived: bool | None = None,
    ) -> None:
        aid = (agent_id or "").strip()
        if not aid:
            raise ValueError("agent_id required")
        now = time.time()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT agent_id FROM agents WHERE agent_id = ?", (aid,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO agents(
                            agent_id, task_id, cwd, model_id, title,
                            created_at, updated_at, archived, meta_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            aid,
                            (task_id or "").strip(),
                            (cwd or "").strip(),
                            (model_id or "").strip(),
                            (title or "").strip(),
                            now,
                            now,
                            1 if archived else 0,
                            meta_json,
                        ),
                    )
                else:
                    sets = ["updated_at = ?"]
                    args: list[Any] = [now]
                    if task_id is not None and str(task_id) != "":
                        sets.append("task_id = ?")
                        args.append(str(task_id).strip())
                    if cwd is not None and str(cwd) != "":
                        sets.append("cwd = ?")
                        args.append(str(cwd).strip())
                    if model_id is not None and str(model_id) != "":
                        sets.append("model_id = ?")
                        args.append(str(model_id).strip())
                    if title is not None and str(title) != "":
                        sets.append("title = ?")
                        args.append(str(title).strip())
                    if meta is not None:
                        sets.append("meta_json = ?")
                        args.append(meta_json)
                    if archived is not None:
                        sets.append("archived = ?")
                        args.append(1 if archived else 0)
                    args.append(aid)
                    conn.execute(
                        f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?",
                        args,
                    )
                conn.commit()

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        aid = (agent_id or "").strip()
        if not aid:
            return None
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM agents WHERE agent_id = ?", (aid,)
                ).fetchone()
        if row is None:
            return None
        return self._agent_row(row)

    def list_agents(self, *, include_archived: bool = False, limit: int = 80) -> list[dict[str, Any]]:
        lim = max(1, min(200, int(limit)))
        with self._lock:
            with self._connect() as conn:
                if include_archived:
                    rows = conn.execute(
                        "SELECT * FROM agents ORDER BY updated_at DESC LIMIT ?",
                        (lim,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM agents WHERE archived = 0 "
                        "ORDER BY updated_at DESC LIMIT ?",
                        (lim,),
                    ).fetchall()
        return [self._agent_row(r) for r in rows]

    def find_agent_by_task(self, task_id: str) -> dict[str, Any] | None:
        tid = (task_id or "").strip()
        if not tid:
            return None
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM agents WHERE task_id = ? AND archived = 0 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
        return self._agent_row(row) if row else None

    def archive_agent(self, agent_id: str) -> bool:
        aid = (agent_id or "").strip()
        if not aid:
            return False
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE agents SET archived = 1, updated_at = ? WHERE agent_id = ?",
                    (time.time(), aid),
                )
                conn.commit()
                return cur.rowcount > 0

    @staticmethod
    def _agent_row(row: sqlite3.Row) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        raw = str(row["meta_json"] or "")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = {}
        return {
            "agent_id": str(row["agent_id"]),
            "task_id": str(row["task_id"] or ""),
            "cwd": str(row["cwd"] or ""),
            "model_id": str(row["model_id"] or ""),
            "title": str(row["title"] or ""),
            "created_at": float(row["created_at"] or 0),
            "updated_at": float(row["updated_at"] or 0),
            "archived": bool(int(row["archived"] or 0)),
            "meta": meta,
        }


def get_store(data_dir: str | None = None) -> CursorAgentSettingsStore:
    global _cached
    with _store_lock:
        if _cached is None or (data_dir and data_dir != _cached._data_dir):
            _cached = CursorAgentSettingsStore(data_dir=data_dir)
        return _cached
