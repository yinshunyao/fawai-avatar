# term:个人知识库  doc:doc/功能/个人知识库/说明.md
"""记忆配置：工作区/可读区/数据区索引策略。本机 SQLite，热更新，不写 .env。"""
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 与 Web settings 字段 key 对齐（历史 FAWAI_* 命名）
KEY_WORK_ROOT = "FAWAI_WORK_ROOT"
KEY_READ_ROOTS = "FAWAI_READ_ROOTS"
KEY_SIDECAR = "FAWAI_LORE_SIDECAR"
# 旧全局开关（迁移后仍可读；新真源 KEY_INDEX_POLICY）
KEY_FTS5 = "FAWAI_LORE_FTS5"
KEY_FTS_DIR = "FAWAI_LORE_FTS_DIR"
KEY_NAME_INDEX = "FAWAI_NAME_INDEX"
KEY_NAME_INDEX_DIR = "FAWAI_NAME_INDEX_DIR"
KEY_VECTOR = "FAWAI_LORE_VECTOR"
KEY_VECTOR_DIR = "FAWAI_LORE_VECTOR_DIR"
KEY_INDEX_POLICY = "FAWAI_INDEX_POLICY"

MEMORY_KEYS: tuple[str, ...] = (
    KEY_WORK_ROOT,
    KEY_READ_ROOTS,
    KEY_SIDECAR,
    KEY_FTS5,
    KEY_FTS_DIR,
    KEY_NAME_INDEX,
    KEY_NAME_INDEX_DIR,
    KEY_VECTOR,
    KEY_VECTOR_DIR,
    KEY_INDEX_POLICY,
)

# 目录字段哨兵：跟随可读区 / 跟随工作区（用户数据区默认）
FOLLOW_READ_ROOTS = "read_roots"
FOLLOW_WORK_ROOT = "work_root"

_DEFAULTS: dict[str, str] = {
    KEY_WORK_ROOT: ".",
    KEY_READ_ROOTS: "/",
    # 旁路抽短 KV：默认关（意义不大；主知识面=工作区索引∪笔记手写）
    KEY_SIDECAR: "0",
    KEY_FTS5: "1",
    # 默认跟工作区：用户数据区开箱即可建正文 FTS / 文件名索引（勿默认跟整盘可读区）
    KEY_FTS_DIR: FOLLOW_WORK_ROOT,
    KEY_NAME_INDEX: "1",
    KEY_NAME_INDEX_DIR: FOLLOW_WORK_ROOT,
    KEY_VECTOR: "1",
    KEY_VECTOR_DIR: "",
    KEY_INDEX_POLICY: "",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

_store_lock = threading.RLock()
_cached: "MemorySettingsStore | None" = None
_generation = 0


def bump_generation() -> int:
    global _generation
    with _store_lock:
        _generation += 1
        return _generation


def current_generation() -> int:
    return _generation


def _data_dir() -> str:
    from fawai_avatar.config import load_dotenv, _env

    load_dotenv()
    return str(Path(_env("FAWAI_DATA_DIR") or "data").expanduser().resolve())


def is_follow_read_roots(raw: str | None) -> bool:
    s = (raw or "").strip().lower()
    return s in {FOLLOW_READ_ROOTS, "__read_roots__", "same_as_read_roots"}


def is_follow_work_root(raw: str | None) -> bool:
    s = (raw or "").strip().lower()
    return s in {FOLLOW_WORK_ROOT, "work", ".", "__work_root__", "same_as_work_root"}


def resolve_work_root_path(work_root: str | None) -> str:
    raw = (work_root or "").strip() or "."
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return str(Path(raw).expanduser())


def resolve_index_roots(
    raw: str | None,
    *,
    read_roots: str,
    work_root: str = ".",
) -> str:
    """
    解析索引扫根。

    - 空 / ``work_root``：跟随工作区（用户数据区默认）
    - ``read_roots``：跟随可读区（勿默认；可读区常为 /）
    - 否则原样（可冒号多分隔）
    """
    s = (raw or "").strip()
    if not s or is_follow_work_root(s):
        return resolve_work_root_path(work_root)
    if is_follow_read_roots(s):
        return (read_roots or "").strip() or _DEFAULTS[KEY_READ_ROOTS]
    return s


class MemorySettingsStore:
    """SQLite：``data/memory_settings.sqlite``。"""

    def __init__(self, data_dir: str | None = None) -> None:
        root = os.path.abspath(os.path.expanduser(str(data_dir or _data_dir())))
        self._data_dir = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)
        self._db_path = os.path.join(root, "memory_settings.sqlite")
        self._init_db()
        self._ensure_migrate()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def data_dir(self) -> str:
        return self._data_dir

    def _open(self) -> sqlite3.Connection:
        os.makedirs(self._data_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
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

    def _kv_get(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _kv_set(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _kv_delete(self, conn: sqlite3.Connection, key: str) -> None:
        conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def _ensure_migrate(self) -> None:
        with self._lock:
            with self._connect() as conn:
                if self._meta_get(conn, "env_migrated") != "1":
                    self._migrate_from_env(conn)
                    self._meta_set(conn, "env_migrated", "1")
                # 存量：索引目录曾空=未扫；改为默认跟工作区，开箱支持用户数据区知识索引
                if self._meta_get(conn, "index_dir_work_root_v1") != "1":
                    for key in (KEY_FTS_DIR, KEY_NAME_INDEX_DIR):
                        cur = self._kv_get(conn, key)
                        if cur is None or str(cur).strip() == "":
                            self._kv_set(conn, key, FOLLOW_WORK_ROOT)
                    self._meta_set(conn, "index_dir_work_root_v1", "1")
                # 旧全局开关 → 按区/业务 INDEX_POLICY
                if self._meta_get(conn, "index_policy_v1") != "1":
                    self._migrate_index_policy(conn)
                    self._meta_set(conn, "index_policy_v1", "1")
                conn.commit()

    def _migrate_index_policy(self, conn: sqlite3.Connection) -> None:
        from fawai_avatar.index_policy import migrate_from_legacy_flags, policy_to_json

        existing = self._kv_get(conn, KEY_INDEX_POLICY)
        if existing is not None and str(existing).strip():
            return

        def _on(raw: str | None, *, default: bool) -> bool:
            if raw is None or raw == "":
                return default
            return str(raw).lower() in {"1", "true", "yes", "on"}

        fts_raw = self._kv_get(conn, KEY_FTS5)
        name_raw = self._kv_get(conn, KEY_NAME_INDEX)
        vec_raw = self._kv_get(conn, KEY_VECTOR)
        fts_dir = self._kv_get(conn, KEY_FTS_DIR) or FOLLOW_WORK_ROOT
        name_dir = self._kv_get(conn, KEY_NAME_INDEX_DIR) or FOLLOW_WORK_ROOT
        pol = migrate_from_legacy_flags(
            fts5=_on(fts_raw, default=True),
            fts_dir=str(fts_dir),
            name_on=_on(name_raw, default=True),
            name_dir=str(name_dir),
            vector_on=_on(vec_raw, default=True),
        )
        self._kv_set(conn, KEY_INDEX_POLICY, policy_to_json(pol))

    def _migrate_from_env(self, conn: sqlite3.Connection) -> None:
        from fawai_avatar.config import load_dotenv

        load_dotenv()
        disk = self._read_dotenv_map()
        for key in MEMORY_KEYS:
            if self._kv_get(conn, key) is not None:
                continue
            raw = os.environ.get(key)
            if raw is None:
                raw = disk.get(key)
            if raw is None:
                continue
            self._kv_set(conn, key, str(raw))

    @staticmethod
    def _read_dotenv_map() -> dict[str, str]:
        """轻量读仓库根 .env，避免与 system_settings 循环 import。"""
        path = Path(__file__).resolve().parents[1] / ".env"
        if not path.is_file():
            return {}
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            key = k.strip()
            if not key:
                continue
            val = v.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            out[key] = val
        return out

    def get(self, key: str, *, default: str | None = None) -> str:
        if key not in MEMORY_KEYS:
            raise KeyError(key)
        with self._lock:
            with self._connect() as conn:
                raw = self._kv_get(conn, key)
        if raw is None:
            if default is not None:
                return default
            return _DEFAULTS.get(key, "")
        return raw

    def as_map(self) -> dict[str, str]:
        out: dict[str, str] = dict(_DEFAULTS)
        with self._lock:
            with self._connect() as conn:
                for key in MEMORY_KEYS:
                    raw = self._kv_get(conn, key)
                    if raw is not None:
                        out[key] = raw
        return out

    def set_many(self, updates: dict[str, str | None]) -> list[str]:
        """``None`` 表示删键回默认。返回实际变更的 key 列表。"""
        changed: list[str] = []
        with self._lock:
            with self._connect() as conn:
                for key, val in updates.items():
                    if key not in MEMORY_KEYS:
                        continue
                    cur = self._kv_get(conn, key)
                    if val is None:
                        if cur is None:
                            continue
                        self._kv_delete(conn, key)
                        changed.append(key)
                        continue
                    text = str(val)
                    if cur is not None and cur == text:
                        continue
                    if cur is None and text == _DEFAULTS.get(key, ""):
                        # 与默认相同且从未写过：不必落盘
                        continue
                    self._kv_set(conn, key, text)
                    changed.append(key)
                conn.commit()
        if changed:
            bump_generation()
            self.sync_process_env()
        return changed

    def sync_process_env(self) -> None:
        """热更进程环境，兼容仍读 ``os.environ`` / ``_env`` 的旧路径。"""
        for key, val in self.as_map().items():
            os.environ[key] = val

    def work_root(self) -> str:
        return self.get(KEY_WORK_ROOT) or "."

    def read_roots_raw(self) -> str:
        return self.get(KEY_READ_ROOTS)

    def sidecar_enabled(self, *, default: bool = False) -> bool:
        """旁路抽短 KV/偏好：默认关。"""
        raw = self.get(KEY_SIDECAR)
        if raw is None or raw == "":
            return bool(default)
        return raw.lower() in {"1", "true", "yes", "on"}

    def index_policy(self) -> dict[str, Any]:
        from fawai_avatar.index_policy import default_policy, parse_policy

        raw = self.get(KEY_INDEX_POLICY)
        if not (raw or "").strip():
            return default_policy()
        return parse_policy(raw)

    def set_index_policy(self, policy: dict[str, Any] | str) -> list[str]:
        from fawai_avatar.index_policy import policy_to_json

        if isinstance(policy, str):
            text = policy
        else:
            text = policy_to_json(policy)
        return self.set_many({KEY_INDEX_POLICY: text})

    def fts5_enabled(self, *, default: bool = True) -> bool:
        """兼容旧调用：工作区正文 FTS 或笔记 FTS 任一开即为 True。"""
        from fawai_avatar.index_policy import lore_fts_enabled, workspace_fts_enabled

        pol = self.index_policy()
        return bool(workspace_fts_enabled(pol) or lore_fts_enabled(pol))

    def workspace_fts_enabled(self, *, default: bool = True) -> bool:
        from fawai_avatar.index_policy import workspace_fts_enabled

        return bool(workspace_fts_enabled(self.index_policy()))

    def lore_note_fts_enabled(self, *, default: bool = True) -> bool:
        from fawai_avatar.index_policy import lore_fts_enabled

        return bool(lore_fts_enabled(self.index_policy()))

    def fts_dir_raw(self) -> str:
        # 兼容旧字段展示；真源已是按区 methods
        return self.get(KEY_FTS_DIR)

    def name_index_enabled(self, *, default: bool = True) -> bool:
        from fawai_avatar.index_policy import workspace_name_enabled

        return workspace_name_enabled(self.index_policy())

    def name_index_dir_raw(self) -> str:
        return self.get(KEY_NAME_INDEX_DIR)

    def vector_enabled(self, *, default: bool = True) -> bool:
        from fawai_avatar.index_policy import lore_vector_enabled

        pol_on = lore_vector_enabled(self.index_policy())
        if not pol_on:
            return False
        # auto 兼容：有 sqlite-vec 才真正可用（调用方仍会再查包）
        raw = self.get(KEY_VECTOR)
        if raw and raw.lower() == "auto":
            try:
                from fawai_lore.vector import sqlite_vec_available

                return bool(sqlite_vec_available())
            except Exception:
                return False
        return True

    def vector_dir_raw(self) -> str:
        return self.get(KEY_VECTOR_DIR)

    def resolved_fts_roots(self) -> str:
        from fawai_avatar.index_policy import METHOD_FTS, resolve_zone_scan_roots

        roots = resolve_zone_scan_roots(
            self.index_policy(),
            method=METHOD_FTS,
            work_root=self.work_root(),
            read_roots=self.read_roots_raw(),
        )
        if roots:
            return roots
        # 无区开启时回空（禁止误跟旧 DIR）
        return ""

    def resolved_name_index_roots(self) -> str:
        from fawai_avatar.index_policy import METHOD_NAME, resolve_zone_scan_roots

        return resolve_zone_scan_roots(
            self.index_policy(),
            method=METHOD_NAME,
            work_root=self.work_root(),
            read_roots=self.read_roots_raw(),
        )

    def resolved_vector_roots(self) -> str:
        # 向量目录空=知识库内建，不跟工作区扫盘
        s = (self.vector_dir_raw() or "").strip()
        if not s:
            return ""
        return resolve_index_roots(
            s,
            read_roots=self.read_roots_raw(),
            work_root=self.work_root(),
        )

    def settings_payload_values(self) -> dict[str, Any]:
        m = self.as_map()
        values: dict[str, Any] = {}
        for key in MEMORY_KEYS:
            if key == KEY_INDEX_POLICY:
                continue
            # 旧全局索引字段 / 旁路短 KV：暂不暴露到 Web 表单
            if key in {
                KEY_SIDECAR,
                KEY_FTS5,
                KEY_FTS_DIR,
                KEY_NAME_INDEX,
                KEY_NAME_INDEX_DIR,
                KEY_VECTOR,
                KEY_VECTOR_DIR,
            }:
                continue
            eff = m.get(key, _DEFAULTS.get(key, ""))
            values[key] = {"set": True, "value": eff, "display": eff}
        return values

    def index_policy_payload(self) -> dict[str, Any]:
        from fawai_avatar.index_policy import catalog_for_agent, ui_schema

        pol = self.index_policy()
        data_dir = self.data_dir
        try:
            from fawai_avatar.config import _env, load_dotenv

            load_dotenv()
            raw = (_env("FAWAI_DATA_DIR") or "").strip()
            if raw:
                data_dir = str(Path(raw).expanduser().resolve())
        except Exception:
            pass
        return {
            "policy": pol,
            "ui": ui_schema(),
            "catalog": catalog_for_agent(
                pol,
                work_root=resolve_work_root_path(self.work_root()),
                read_roots=self.read_roots_raw(),
                data_dir=data_dir,
            ),
        }


def get_memory_store(*, data_dir: str | None = None) -> MemorySettingsStore:
    global _cached
    with _store_lock:
        if data_dir is not None:
            return MemorySettingsStore(data_dir=data_dir)
        if _cached is None:
            _cached = MemorySettingsStore()
            _cached.sync_process_env()
        return _cached


def reset_memory_store_cache() -> None:
    """测试用：清空进程内单例。"""
    global _cached
    with _store_lock:
        _cached = None
        bump_generation()
