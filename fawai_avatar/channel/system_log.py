# term:系统日志  doc:doc/功能/系统日志/说明.md
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from fawai_avatar.types import Action, ExecResult

logger = logging.getLogger(__name__)


# 大类 → 操作码 → 中文名（订阅与界面真源）
LOG_CATALOG: dict[str, dict[str, Any]] = {
    "task": {
        "label": "任务操作",
        "actions": {
            "delete": "删除",
            "archive": "归档",
            "unarchive": "取消归档",
            "cancel": "取消",
            "pause": "暂停",
            "resume": "恢复",
            "recover": "进程恢复续跑",
        },
    },
    "file": {
        "label": "文件操作",
        "actions": {
            "read": "读取",
            "write": "写入",
        },
    },
    "command": {
        "label": "命令",
        "actions": {
            "shell": "执行命令",
        },
    },
    "code": {
        "label": "代码与程序",
        "actions": {
            "run": "执行代码/程序",
        },
    },
    "mouse": {
        "label": "鼠标与截屏",
        "actions": {
            "click": "点击",
            "type": "键入",
            "hotkey": "热键",
            "screenshot": "截屏",
        },
    },
}

# Action.kind → (category, action)
_KIND_TO_LOG: dict[str, tuple[str, str]] = {
    "delete": ("task", "delete"),
    "archive": ("task", "archive"),
    "unarchive": ("task", "unarchive"),
    "cancel": ("task", "cancel"),
    "pause": ("task", "pause"),
    "resume": ("task", "resume"),
    "recover": ("task", "recover"),
    "fs_read": ("file", "read"),
    "fs_read_many": ("file", "read"),
    "fs_glob": ("file", "read"),
    "fs_grep": ("file", "read"),
    "fs_write": ("file", "write"),
    "fs_replace": ("file", "write"),
    "fs_patch": ("file", "write"),
    "shell": ("command", "shell"),
    "shell_verify": ("command", "shell"),
    "code_check": ("code", "run"),
    "code_symbols": ("code", "run"),
    "code_map": ("code", "run"),
    "code_index_refresh": ("code", "run"),
    "explore_search": ("code", "run"),
    "click": ("mouse", "click"),
    "type": ("mouse", "type"),
    "hotkey": ("mouse", "hotkey"),
    "screenshot": ("mouse", "screenshot"),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_logs (
    log_id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'task',
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'api',
    task_id TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    message TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    analyze_task_id TEXT,
    analyze_ts REAL
);

CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_action ON system_logs(action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_task ON system_logs(task_id, ts DESC);
"""

_SUB_FILENAME = "system_log_subscriptions.json"


@dataclass
class SystemLogEntry:
    log_id: str
    ts: float
    category: str
    action: str
    actor: str
    task_id: str | None
    ok: bool
    message: str
    detail: dict[str, Any]
    analyze_task_id: str | None = None
    analyze_ts: float | None = None

    @property
    def analyzed(self) -> bool:
        return bool(self.analyze_task_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category_label"] = category_label(self.category)
        data["action_label"] = action_label(self.category, self.action)
        data["analyzed"] = self.analyzed
        return data


def category_label(category: str) -> str:
    cat = LOG_CATALOG.get(category) or {}
    return str(cat.get("label") or category)


def action_label(category: str, action: str) -> str:
    cat = LOG_CATALOG.get(category) or {}
    actions = cat.get("actions") or {}
    if isinstance(actions, dict) and action in actions:
        return str(actions[action])
    return action


def catalog_payload() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cat_id, meta in LOG_CATALOG.items():
        actions = meta.get("actions") or {}
        out.append(
            {
                "id": cat_id,
                "label": meta.get("label") or cat_id,
                "actions": [
                    {"id": aid, "label": alabel}
                    for aid, alabel in (actions.items() if isinstance(actions, dict) else [])
                ],
            }
        )
    return out


def resolve_kind(kind: str) -> tuple[str, str] | None:
    key = (kind or "").strip().lower()
    return _KIND_TO_LOG.get(key)


def default_subscriptions() -> dict[str, bool]:
    subs: dict[str, bool] = {}
    for cat_id, meta in LOG_CATALOG.items():
        actions = meta.get("actions") or {}
        if not isinstance(actions, dict):
            continue
        for action_id in actions:
            subs[f"{cat_id}.{action_id}"] = True
    return subs


def build_action_detail(action: Action, result: ExecResult | None = None) -> dict[str, Any]:
    """从动作与结果提炼 detail（路径、行范围、命令等）。"""
    kind = (action.kind or "").strip().lower()
    args = dict(action.args or {})
    detail: dict[str, Any] = {"kind": kind}
    data = result.data if result and isinstance(result.data, dict) else {}

    if kind in {"fs_read", "fs_read_many"}:
        from fawai_avatar.workspace.fs import resolve_read_range

        try:
            offset, limit = resolve_read_range(
                args, default_limit=(200 if kind == "fs_read" else 120)
            )
        except ValueError:
            offset, limit = 1, (200 if kind == "fs_read" else 120)
        # 优先用实际读出的行号（别名解析后与结果一致）
        file_lines = data.get("lines")
        if isinstance(file_lines, list) and file_lines:
            first = file_lines[0] if isinstance(file_lines[0], dict) else None
            last = file_lines[-1] if isinstance(file_lines[-1], dict) else None
            if first and last and first.get("n") is not None and last.get("n") is not None:
                detail["lines"] = {"start": int(first["n"]), "end": int(last["n"])}
            else:
                detail["lines"] = {"start": offset, "end": offset + max(limit, 1) - 1}
        else:
            detail["lines"] = {"start": offset, "end": offset + max(limit, 1) - 1}
        if kind == "fs_read":
            detail["path"] = str(args.get("path") or data.get("path") or "")
        else:
            paths = args.get("paths") or data.get("paths") or []
            detail["paths"] = [str(p) for p in paths] if isinstance(paths, list) else []
    elif kind in {"fs_glob", "fs_grep"}:
        if args.get("path"):
            detail["path"] = str(args.get("path"))
        if args.get("pattern") or args.get("glob") or args.get("q"):
            detail["query"] = str(
                args.get("pattern") or args.get("glob") or args.get("q") or ""
            )
        hits = data.get("hits") or data.get("matches") or data.get("paths")
        if isinstance(hits, list):
            detail["hit_count"] = len(hits)
    elif kind in {"fs_write", "fs_replace", "fs_patch"}:
        path = str(args.get("path") or data.get("path") or "")
        detail["path"] = path
        content = args.get("content")
        if isinstance(content, str):
            detail["line_count"] = content.count("\n") + (1 if content else 0)
        if kind == "fs_replace":
            detail["replace"] = True
        if kind == "fs_patch":
            hunks = args.get("hunks")
            if isinstance(hunks, list):
                detail["hunk_count"] = len(hunks)
            elif args.get("old") is not None:
                old = str(args.get("old") or "")
                detail["patch_lines"] = old.count("\n") + (1 if old else 0)
        if data.get("diff"):
            detail["has_diff"] = True
    elif kind in {"shell", "shell_verify"}:
        cmd = args.get("cmd") or args.get("command") or args.get("argv") or data.get("cmd")
        if isinstance(cmd, list):
            detail["cmd"] = " ".join(str(x) for x in cmd)
        elif cmd is not None:
            detail["cmd"] = str(cmd)
        if data.get("returncode") is not None:
            detail["returncode"] = data.get("returncode")
    elif kind in {"code_check", "code_symbols", "code_map", "code_index_refresh", "explore_search"}:
        if args.get("path"):
            detail["path"] = str(args.get("path"))
        if args.get("paths"):
            detail["paths"] = [str(p) for p in args["paths"]] if isinstance(args["paths"], list) else []
        if args.get("command"):
            detail["program"] = str(args.get("command"))
        if args.get("query") or args.get("q"):
            detail["query"] = str(args.get("query") or args.get("q"))
        if args.get("queries") and isinstance(args.get("queries"), list):
            detail["queries"] = [str(q) for q in args["queries"]]
    elif kind == "click":
        detail["x"] = args.get("x", data.get("x"))
        detail["y"] = args.get("y", data.get("y"))
    elif kind == "type":
        text = str(args.get("text") or "")
        detail["chars"] = len(text)
    elif kind == "hotkey":
        keys = args.get("keys") or data.get("keys") or []
        detail["keys"] = [str(k) for k in keys] if isinstance(keys, list) else []
    elif kind == "screenshot":
        path = args.get("path") or data.get("path") or data.get("image_path")
        if path:
            detail["path"] = str(path)
        if data.get("width") is not None:
            detail["width"] = data.get("width")
        if data.get("height") is not None:
            detail["height"] = data.get("height")

    return {k: v for k, v in detail.items() if v is not None and v != "" and v != []}


class SystemLogStore:
    """系统操作独立落盘；支持按大类/操作订阅；不进会话历史。

    库初始化失败时进入降级：网关仍可起，读写接口空操作/空列表，
    ``available=False`` 并由 Web「后台日志 / 一键修复」处理。
    """

    def __init__(self, data_dir: str) -> None:
        root = os.path.abspath(os.path.expanduser(str(data_dir or "data")))
        self._data_dir = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)
        self._db_path = os.path.join(root, "system_logs.sqlite")
        self._sub_path = os.path.join(root, _SUB_FILENAME)
        self._degraded = False
        self.init_error: str | None = None
        try:
            self._init_db()
            self._ensure_subscriptions_file()
        except Exception as exc:  # noqa: BLE001 — 降级保网关
            self._degraded = True
            self.init_error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "system log store init failed; running degraded: %s", self.init_error
            )

    @property
    def available(self) -> bool:
        return not self._degraded

    @property
    def db_path(self) -> str:
        return self._db_path

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "degraded": self._degraded,
            "init_error": self.init_error,
            "db_path": self._db_path,
        }

    def _open(self) -> sqlite3.Connection:
        os.makedirs(self._data_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_logs'"
        ).fetchone()
        if has is None:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """打开连接；成功离开时 commit，异常 rollback；必须 close（sqlite 的 with conn 不关 FD）。"""
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 — 关闭前尽力回滚
                pass
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                self._migrate(conn)
                conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        # 旧库 CREATE TABLE IF NOT EXISTS 不会加列；须先 ALTER 再建索引
        cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(system_logs)")}
        if "category" not in cols:
            conn.execute(
                "ALTER TABLE system_logs ADD COLUMN category TEXT NOT NULL DEFAULT 'task'"
            )
        if "analyze_task_id" not in cols:
            conn.execute("ALTER TABLE system_logs ADD COLUMN analyze_task_id TEXT")
        if "analyze_ts" not in cols:
            conn.execute("ALTER TABLE system_logs ADD COLUMN analyze_ts REAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_logs_category "
            "ON system_logs(category, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_logs_analyze "
            "ON system_logs(analyze_task_id, ts DESC)"
        )

    def _ensure_subscriptions_file(self) -> None:
        with self._lock:
            if os.path.isfile(self._sub_path):
                return
            self._write_subscriptions(default_subscriptions())

    def _read_subscriptions(self) -> dict[str, bool]:
        base = default_subscriptions()
        try:
            with open(self._sub_path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return base
        if not isinstance(raw, dict):
            return base
        for key, val in raw.items():
            if key in base:
                base[key] = bool(val)
        return base

    def _write_subscriptions(self, subs: dict[str, bool]) -> None:
        merged = default_subscriptions()
        for key, val in subs.items():
            if key in merged:
                merged[key] = bool(val)
        tmp = self._sub_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self._sub_path)

    def get_subscriptions(self) -> dict[str, Any]:
        if self._degraded:
            return {
                "catalog": catalog_payload(),
                "enabled": default_subscriptions(),
                "degraded": True,
                "init_error": self.init_error,
            }
        with self._lock:
            enabled = self._read_subscriptions()
        return {
            "catalog": catalog_payload(),
            "enabled": enabled,
        }

    def set_subscriptions(self, enabled: dict[str, Any] | None) -> dict[str, Any]:
        if self._degraded:
            return self.get_subscriptions()
        patch = enabled if isinstance(enabled, dict) else {}
        with self._lock:
            current = self._read_subscriptions()
            for key, val in patch.items():
                if key in current:
                    current[key] = bool(val)
            self._write_subscriptions(current)
            enabled_out = self._read_subscriptions()
        return {"catalog": catalog_payload(), "enabled": enabled_out}

    def is_subscribed(self, category: str, action: str) -> bool:
        if self._degraded:
            return False
        key = f"{category}.{action}"
        with self._lock:
            subs = self._read_subscriptions()
        return bool(subs.get(key, True))

    def append(
        self,
        action: str,
        *,
        category: str | None = None,
        message: str = "",
        ok: bool = True,
        task_id: str | None = None,
        actor: str = "api",
        detail: dict[str, Any] | None = None,
        ts: float | None = None,
        force: bool = False,
    ) -> SystemLogEntry | None:
        if self._degraded:
            return None
        cat = (category or "").strip() or "task"
        act = str(action or "").strip() or "unknown"
        if cat not in LOG_CATALOG:
            # 未知大类：仍允许写入（任务类兼容旧调用）
            cat = cat or "task"
        if not force and not self.is_subscribed(cat, act):
            return None
        entry = SystemLogEntry(
            log_id=uuid.uuid4().hex,
            ts=float(ts if ts is not None else time.time()),
            category=cat,
            action=act,
            actor=str(actor or "api").strip() or "api",
            task_id=task_id,
            ok=bool(ok),
            message=str(message or ""),
            detail=dict(detail or {}),
        )
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO system_logs
                    (log_id, ts, category, action, actor, task_id, ok, message, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.log_id,
                        entry.ts,
                        entry.category,
                        entry.action,
                        entry.actor,
                        entry.task_id,
                        1 if entry.ok else 0,
                        entry.message,
                        json.dumps(entry.detail, ensure_ascii=False),
                    ),
                )
        return entry

    def log_action(
        self,
        action: Action,
        result: ExecResult,
        *,
        task_id: str | None = None,
        actor: str = "runtime",
    ) -> SystemLogEntry | None:
        mapped = resolve_kind(action.kind)
        if mapped is None:
            return None
        category, act = mapped
        detail = build_action_detail(action, result)
        msg = (result.message or "").strip()
        if not msg:
            msg = f"{action.kind} → {'ok' if result.ok else 'fail'}"
        return self.append(
            act,
            category=category,
            message=msg[:2000],
            ok=bool(result.ok),
            task_id=task_id,
            actor=actor,
            detail=detail,
        )

    def log_screenshot(
        self,
        *,
        path: str | None,
        width: int | None = None,
        height: int | None = None,
        ok: bool = True,
        message: str = "",
        task_id: str | None = None,
        actor: str = "runtime",
    ) -> SystemLogEntry | None:
        detail: dict[str, Any] = {}
        if path:
            detail["path"] = str(path)
        if width is not None:
            detail["width"] = width
        if height is not None:
            detail["height"] = height
        return self.append(
            "screenshot",
            category="mouse",
            message=message or (f"screenshot {path}" if path else "screenshot"),
            ok=ok,
            task_id=task_id,
            actor=actor,
            detail=detail,
        )

    def get_log(self, log_id: str) -> SystemLogEntry | None:
        if self._degraded:
            return None
        lid = (log_id or "").strip()
        if not lid:
            return None
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM system_logs WHERE log_id = ? LIMIT 1", (lid,)
                ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def get_logs_by_ids(self, log_ids: list[str]) -> list[SystemLogEntry]:
        if self._degraded:
            return []
        ids = [str(x).strip() for x in log_ids if str(x).strip()]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM system_logs WHERE log_id IN ({placeholders})",
                    ids,
                ).fetchall()
        by_id: dict[str, SystemLogEntry] = {}
        for row in rows:
            entry = self._row_to_entry(row)
            by_id[entry.log_id] = entry
        return [by_id[i] for i in ids if i in by_id]

    def mark_analyzed(self, log_ids: list[str], analyze_task_id: str) -> int:
        """标记失败日志已纳入某次一键分析任务，避免重复开跑。"""
        if self._degraded:
            return 0
        tid = (analyze_task_id or "").strip()
        ids = [str(x).strip() for x in log_ids if str(x).strip()]
        if not tid or not ids:
            return 0
        now = time.time()
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    f"""
                    UPDATE system_logs
                    SET analyze_task_id = ?, analyze_ts = ?
                    WHERE log_id IN ({placeholders})
                    """,
                    [tid, now, *ids],
                )
                conn.commit()
                return int(cur.rowcount or 0)

    def clear_analyzed(self, log_ids: list[str]) -> int:
        """清除分析标记（原整改任务已删或强制重分析前）。"""
        if self._degraded:
            return 0
        ids = [str(x).strip() for x in log_ids if str(x).strip()]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    f"""
                    UPDATE system_logs
                    SET analyze_task_id = NULL, analyze_ts = NULL
                    WHERE log_id IN ({placeholders})
                    """,
                    ids,
                )
                conn.commit()
                return int(cur.rowcount or 0)

    def _filter_clauses(
        self,
        *,
        action: str | None = None,
        category: str | None = None,
        task_id: str | None = None,
        since: float | None = None,
        ok: bool | None = None,
        analyzed: bool | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(float(since))
        if ok is not None:
            clauses.append("ok = ?")
            params.append(1 if ok else 0)
        if analyzed is True:
            clauses.append(
                "(analyze_task_id IS NOT NULL AND TRIM(analyze_task_id) != '')"
            )
        elif analyzed is False:
            clauses.append(
                "(analyze_task_id IS NULL OR TRIM(analyze_task_id) = '')"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def count_logs(
        self,
        *,
        action: str | None = None,
        category: str | None = None,
        task_id: str | None = None,
        since: float | None = None,
        ok: bool | None = None,
        analyzed: bool | None = None,
    ) -> int:
        if self._degraded:
            return 0
        where, params = self._filter_clauses(
            action=action,
            category=category,
            task_id=task_id,
            since=since,
            ok=ok,
            analyzed=analyzed,
        )
        sql = f"SELECT COUNT(*) FROM system_logs {where}"
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(sql, params).fetchone()
        return int(row[0] if row is not None else 0)

    def list_logs(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        action: str | None = None,
        category: str | None = None,
        task_id: str | None = None,
        since: float | None = None,
        ok: bool | None = None,
        analyzed: bool | None = None,
    ) -> list[SystemLogEntry]:
        if self._degraded:
            return []
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        where, params = self._filter_clauses(
            action=action,
            category=category,
            task_id=task_id,
            since=since,
            ok=ok,
            analyzed=analyzed,
        )
        sql = (
            f"SELECT * FROM system_logs {where} "
            "ORDER BY ts DESC, log_id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> SystemLogEntry:
        raw = row["detail_json"] or "{}"
        try:
            detail = json.loads(raw)
            if not isinstance(detail, dict):
                detail = {"value": detail}
        except json.JSONDecodeError:
            detail = {}
        keys = row.keys()
        category = str(row["category"]) if "category" in keys and row["category"] else "task"
        analyze_task_id = None
        analyze_ts = None
        if "analyze_task_id" in keys and row["analyze_task_id"]:
            analyze_task_id = str(row["analyze_task_id"])
        if "analyze_ts" in keys and row["analyze_ts"] is not None:
            try:
                analyze_ts = float(row["analyze_ts"])
            except (TypeError, ValueError):
                analyze_ts = None
        return SystemLogEntry(
            log_id=str(row["log_id"]),
            ts=float(row["ts"]),
            category=category,
            action=str(row["action"]),
            actor=str(row["actor"] or "api"),
            task_id=row["task_id"],
            ok=bool(row["ok"]),
            message=str(row["message"] or ""),
            detail=detail,
            analyze_task_id=analyze_task_id,
            analyze_ts=analyze_ts,
        )
