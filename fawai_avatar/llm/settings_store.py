# term:LLM 接入  doc:doc/功能/LLM接入/说明.md
"""LLM 供应商凭证 + 角色路由 + 模型档案：本机 SQLite，热更新，不写 .env。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROUTE_ROLES: tuple[str, ...] = ("text", "vlm", "fast", "asr", "ocr", "embed", "force")

_ROLE_ENV_KEYS: dict[str, str] = {
    "text": "FAWAI_LLM_TEXT_MODEL",
    "vlm": "FAWAI_LLM_VLM_MODEL",
    "fast": "FAWAI_LLM_FAST_MODEL",
    "asr": "FAWAI_LLM_ASR_MODEL",
    "ocr": "FAWAI_LLM_OCR_MODEL",
    "embed": "FAWAI_LORE_EMBED_MODEL",
    "force": "FAWAI_LLM_MODEL",
}

_META_SCREENSHOT_GROUNDING = "screenshot_grounding"

_BUILTIN_SEED: tuple[dict[str, Any], ...] = (
    {
        "id": "dashscope",
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "timeout_sec": 120.0,
        "defaults": {
            "text": "qwen3.7-plus",
            "vlm": "qwen3-vl-plus",
            "fast": "qwen3.8-27b",
            "asr": "qwen3-asr-flash",
            "ocr": "qwen3.5-ocr",
            "embed": "text-embedding-v3",
            "force": "",
        },
    },
    {
        "id": "kklt",
        "label": "小黄",
        "base_url": "https://api.kklt.lol/v1",
        "timeout_sec": 120.0,
        "defaults": {
            "text": "gpt-6-sol",
            "vlm": "gpt-6-sol",
            "fast": "gpt-6-sol",
            "asr": "",
            "ocr": "",
            "embed": "",
            "force": "",
        },
    },
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    timeout_sec REAL,
    builtin INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS routes (
    role TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS model_profiles (
    model_id TEXT PRIMARY KEY,
    params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS model_catalogs (
    provider_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    models_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    fetched_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (provider_id, kind)
);
"""

_store_lock = threading.RLock()
_cached: "LlmSettingsStore | None" = None
_generation = 0


def bump_generation() -> int:
    global _generation
    with _store_lock:
        _generation += 1
        return _generation


def current_generation() -> int:
    return _generation


@dataclass(frozen=True)
class RouteEndpoint:
    role: str
    provider_id: str
    model: str
    base_url: str | None
    api_key: str | None
    timeout_sec: float | None
    label: str = ""

    @property
    def has_key(self) -> bool:
        return bool((self.api_key or "").strip())


def _data_dir() -> str:
    from fawai_avatar.config import load_dotenv, _env

    load_dotenv()
    return str(Path(_env("FAWAI_DATA_DIR") or "data").expanduser().resolve())


class LlmSettingsStore:
    """SQLite：providers / routes / model_profiles / model_catalogs。"""

    def __init__(self, data_dir: str | None = None) -> None:
        from fawai_avatar.data_dir import resolve_data_dir

        root = resolve_data_dir(data_dir, default=_data_dir())
        self._data_dir = root
        self._lock = threading.RLock()
        os.makedirs(root, exist_ok=True)
        self._db_path = os.path.join(root, "llm_settings.sqlite")
        self._init_db()
        self._ensure_seed_and_migrate()

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
        has = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='providers'"
        ).fetchone()
        if has is None:
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

    def _ensure_seed_and_migrate(self) -> None:
        with self._lock:
            with self._connect() as conn:
                for seed in _BUILTIN_SEED:
                    row = conn.execute(
                        "SELECT id FROM providers WHERE id = ?", (seed["id"],)
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            """
                            INSERT INTO providers(id, label, base_url, api_key, timeout_sec, builtin)
                            VALUES (?, ?, ?, '', ?, 1)
                            """,
                            (
                                seed["id"],
                                seed["label"],
                                seed["base_url"],
                                seed.get("timeout_sec"),
                            ),
                        )
                for role in ROUTE_ROLES:
                    row = conn.execute(
                        "SELECT role FROM routes WHERE role = ?", (role,)
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            "INSERT INTO routes(role, provider_id, model_id) VALUES (?, '', '')",
                            (role,),
                        )
                migrated = self._meta_get(conn, "env_migrated")
                if migrated != "1":
                    self._migrate_from_env(conn)
                    self._migrate_profiles_from_files(conn)
                    self._meta_set(conn, "env_migrated", "1")
                # 若路由仍全空，填 dashscope 默认
                empty = True
                for role in ROUTE_ROLES:
                    r = conn.execute(
                        "SELECT provider_id, model_id FROM routes WHERE role = ?",
                        (role,),
                    ).fetchone()
                    if r and (str(r["provider_id"] or "").strip() or str(r["model_id"] or "").strip()):
                        empty = False
                        break
                if empty:
                    defaults = _BUILTIN_SEED[0]["defaults"]
                    for role in ROUTE_ROLES:
                        conn.execute(
                            "UPDATE routes SET provider_id = ?, model_id = ? WHERE role = ?",
                            ("dashscope", str(defaults.get(role) or ""), role),
                        )
                # 存量库新加 ocr 角色：若仍空且百炼已有 Key，填默认 OCR 模型
                ocr_row = conn.execute(
                    "SELECT provider_id, model_id FROM routes WHERE role = 'ocr'"
                ).fetchone()
                if ocr_row and not (
                    str(ocr_row["provider_id"] or "").strip()
                    or str(ocr_row["model_id"] or "").strip()
                ):
                    ds = conn.execute(
                        "SELECT api_key FROM providers WHERE id = 'dashscope'"
                    ).fetchone()
                    if ds and str(ds["api_key"] or "").strip():
                        conn.execute(
                            "UPDATE routes SET provider_id = ?, model_id = ? WHERE role = 'ocr'",
                            ("dashscope", str(_BUILTIN_SEED[0]["defaults"].get("ocr") or "qwen3.5-ocr")),
                        )
                conn.commit()

    def _migrate_from_env(self, conn: sqlite3.Connection) -> None:
        from fawai_avatar.config import load_dotenv, _env
        from fawai_avatar.llm.providers import match_provider_id

        load_dotenv()
        base = (_env("FAWAI_LLM_BASE_URL") or _env("OPENAI_BASE_URL") or "").strip()
        key = (_env("FAWAI_LLM_API_KEY") or _env("OPENAI_API_KEY") or "").strip()
        timeout_raw = (_env("FAWAI_LLM_TIMEOUT") or "").strip()
        timeout: float | None = 120.0
        if timeout_raw:
            try:
                t = float(timeout_raw)
                timeout = None if t <= 0 else t
            except ValueError:
                timeout = 120.0
        pid = match_provider_id(base) if base else "dashscope"
        if pid == "custom":
            # 迁成 custom_migrated 供应商
            pid = "custom_migrated"
            conn.execute(
                """
                INSERT INTO providers(id, label, base_url, api_key, timeout_sec, builtin)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET
                    base_url = excluded.base_url,
                    api_key = CASE
                        WHEN excluded.api_key != '' THEN excluded.api_key
                        ELSE providers.api_key
                    END,
                    timeout_sec = excluded.timeout_sec
                """,
                (pid, "自定义（迁移）", base, key, timeout),
            )
        else:
            conn.execute(
                """
                UPDATE providers SET
                    base_url = CASE WHEN ? != '' THEN ? ELSE base_url END,
                    api_key = CASE WHEN ? != '' THEN ? ELSE api_key END,
                    timeout_sec = ?
                WHERE id = ?
                """,
                (base, base, key, key, timeout, pid),
            )
        for role, env_key in _ROLE_ENV_KEYS.items():
            mid = (_env(env_key) or "").strip()
            if role == "force" and not mid:
                mid = (_env("OPENAI_MODEL") or "").strip()
            conn.execute(
                "UPDATE routes SET provider_id = ?, model_id = ? WHERE role = ?",
                (pid if mid or role in {"text", "vlm"} else "", mid, role),
            )
        # text/vlm 即使模型空也挂上供应商
        for role in ("text", "vlm", "asr", "ocr", "embed"):
            conn.execute(
                "UPDATE routes SET provider_id = ? WHERE role = ? AND (provider_id IS NULL OR provider_id = '')",
                (pid, role),
            )

    def _migrate_profiles_from_files(self, conn: sqlite3.Connection) -> None:
        models_dir = Path(self._data_dir) / "llm_models"
        if not models_dir.is_dir():
            return
        for path in models_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            mid = str(data.get("model") or path.stem).strip()
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            if not mid:
                continue
            existing = conn.execute(
                "SELECT model_id FROM model_profiles WHERE model_id = ?", (mid,)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO model_profiles(model_id, params_json) VALUES (?, ?)",
                (mid, json.dumps(params, ensure_ascii=False)),
            )

    # --- providers ---

    def list_providers(self, *, include_secrets: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, label, base_url, api_key, timeout_sec, builtin "
                    "FROM providers ORDER BY builtin DESC, id ASC"
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            key = str(r["api_key"] or "")
            item: dict[str, Any] = {
                "id": str(r["id"]),
                "label": str(r["label"] or r["id"]),
                "base_url": str(r["base_url"] or ""),
                "timeout_sec": float(r["timeout_sec"]) if r["timeout_sec"] is not None else None,
                "builtin": bool(int(r["builtin"] or 0)),
                "has_key": bool(key.strip()),
            }
            if include_secrets:
                item["api_key"] = key
            out.append(item)
        return out

    def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        pid = (provider_id or "").strip()
        if not pid:
            return None
        with self._lock:
            with self._connect() as conn:
                r = conn.execute(
                    "SELECT id, label, base_url, api_key, timeout_sec, builtin "
                    "FROM providers WHERE id = ?",
                    (pid,),
                ).fetchone()
        if not r:
            return None
        return {
            "id": str(r["id"]),
            "label": str(r["label"] or r["id"]),
            "base_url": str(r["base_url"] or ""),
            "api_key": str(r["api_key"] or ""),
            "timeout_sec": float(r["timeout_sec"]) if r["timeout_sec"] is not None else None,
            "builtin": bool(int(r["builtin"] or 0)),
            "has_key": bool(str(r["api_key"] or "").strip()),
        }

    def upsert_provider(
        self,
        provider_id: str,
        *,
        label: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
        timeout_sec: float | None = None,
        builtin: bool | None = None,
    ) -> dict[str, Any]:
        pid = (provider_id or "").strip()
        if not pid:
            raise ValueError("provider id required")
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM providers WHERE id = ?", (pid,)
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO providers(id, label, base_url, api_key, timeout_sec, builtin)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pid,
                            (label or pid).strip(),
                            (base_url or "").strip(),
                            "" if clear_api_key else (api_key or "").strip(),
                            timeout_sec,
                            1 if builtin else 0,
                        ),
                    )
                else:
                    new_label = (label if label is not None else str(existing["label"] or pid)).strip()
                    old_url = str(existing["base_url"] or "").strip().rstrip("/")
                    new_url = (
                        base_url.strip()
                        if base_url is not None
                        else str(existing["base_url"] or "")
                    )
                    if clear_api_key:
                        new_key = ""
                    elif api_key is not None and str(api_key).strip() != "":
                        new_key = str(api_key).strip()
                    else:
                        new_key = str(existing["api_key"] or "")
                    new_timeout = (
                        timeout_sec if timeout_sec is not None else existing["timeout_sec"]
                    )
                    new_builtin = (
                        int(existing["builtin"] or 0)
                        if builtin is None
                        else (1 if builtin else 0)
                    )
                    conn.execute(
                        """
                        UPDATE providers SET label=?, base_url=?, api_key=?, timeout_sec=?, builtin=?
                        WHERE id=?
                        """,
                        (new_label, new_url, new_key, new_timeout, new_builtin, pid),
                    )
                    # Base URL 变了则旧目录不可信，避免串台
                    if old_url != str(new_url or "").strip().rstrip("/"):
                        conn.execute(
                            "DELETE FROM model_catalogs WHERE provider_id = ?", (pid,)
                        )
                conn.commit()
        bump_generation()
        out = self.get_provider(pid)
        assert out is not None
        return out

    def delete_provider(self, provider_id: str) -> None:
        pid = (provider_id or "").strip()
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT builtin FROM providers WHERE id = ?", (pid,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"unknown provider: {pid}")
                if int(row["builtin"] or 0):
                    raise ValueError("builtin provider cannot be deleted")
                conn.execute("DELETE FROM providers WHERE id = ?", (pid,))
                conn.execute(
                    "UPDATE routes SET provider_id = '', model_id = '' WHERE provider_id = ?",
                    (pid,),
                )
                conn.execute("DELETE FROM model_catalogs WHERE provider_id = ?", (pid,))
                conn.commit()
        bump_generation()

    # --- routes ---

    def list_routes(self) -> dict[str, dict[str, str]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT role, provider_id, model_id FROM routes"
                ).fetchall()
        out: dict[str, dict[str, str]] = {
            role: {"provider_id": "", "model_id": ""} for role in ROUTE_ROLES
        }
        for r in rows:
            role = str(r["role"])
            if role not in out:
                continue
            out[role] = {
                "provider_id": str(r["provider_id"] or ""),
                "model_id": str(r["model_id"] or ""),
            }
        return out

    def set_route(self, role: str, *, provider_id: str, model_id: str) -> dict[str, str]:
        role_n = (role or "").strip()
        if role_n not in ROUTE_ROLES:
            raise ValueError(f"invalid role: {role}")
        pid = (provider_id or "").strip()
        mid = (model_id or "").strip()
        if pid:
            prov = self.get_provider(pid)
            if prov is None:
                raise ValueError(f"unknown provider: {pid}")
            if mid and not prov.get("has_key"):
                raise ValueError(f"provider {pid} has no API key")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO routes(role, provider_id, model_id) VALUES (?, ?, ?) "
                    "ON CONFLICT(role) DO UPDATE SET provider_id=excluded.provider_id, "
                    "model_id=excluded.model_id",
                    (role_n, pid, mid),
                )
                conn.commit()
        bump_generation()
        return {"role": role_n, "provider_id": pid, "model_id": mid}

    def resolve_route(self, role: str) -> RouteEndpoint:
        role_n = (role or "").strip()
        if role_n not in ROUTE_ROLES:
            raise ValueError(f"invalid role: {role}")
        routes = self.list_routes()
        r = routes.get(role_n) or {"provider_id": "", "model_id": ""}
        pid = str(r.get("provider_id") or "").strip()
        mid = str(r.get("model_id") or "").strip()
        prov = self.get_provider(pid) if pid else None
        return RouteEndpoint(
            role=role_n,
            provider_id=pid,
            model=mid,
            base_url=(prov.get("base_url") if prov else None) or None,
            api_key=(prov.get("api_key") if prov else None) or None,
            timeout_sec=prov.get("timeout_sec") if prov else None,
            label=str(prov.get("label") or "") if prov else "",
        )

    # --- profiles ---

    def has_profile(self, model_id: str) -> bool:
        mid = (model_id or "").strip()
        if not mid:
            return False
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM model_profiles WHERE model_id = ?",
                    (mid,),
                ).fetchone()
        return row is not None

    def get_profile_params(self, model_id: str) -> dict[str, Any]:
        mid = (model_id or "").strip()
        if not mid:
            return {}
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT params_json FROM model_profiles WHERE model_id = ?",
                    (mid,),
                ).fetchone()
        if not row:
            return {}
        try:
            data = json.loads(row["params_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def set_profile_params(self, model_id: str, params: dict[str, Any]) -> dict[str, Any]:
        mid = (model_id or "").strip()
        if not mid:
            raise ValueError("model id required")
        clean = params if isinstance(params, dict) else {}
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO model_profiles(model_id, params_json) VALUES (?, ?) "
                    "ON CONFLICT(model_id) DO UPDATE SET params_json = excluded.params_json",
                    (mid, json.dumps(clean, ensure_ascii=False)),
                )
                conn.commit()
        bump_generation()
        return {"model": mid, "params": clean}

    def get_screenshot_grounding(self) -> str:
        with self._lock:
            with self._connect() as conn:
                raw = self._meta_get(conn, _META_SCREENSHOT_GROUNDING)
        from fawai_avatar.observe.screen_grounding import normalize_screenshot_grounding

        return normalize_screenshot_grounding(raw)

    def set_screenshot_grounding(self, mode: str) -> str:
        from fawai_avatar.observe.screen_grounding import normalize_screenshot_grounding

        value = normalize_screenshot_grounding(mode)
        with self._lock:
            with self._connect() as conn:
                self._meta_set(conn, _META_SCREENSHOT_GROUNDING, value)
                conn.commit()
        bump_generation()
        return value

    # --- model catalogs (synced remote lists) ---

    def list_model_catalogs(self) -> dict[str, dict[str, list[Any]]]:
        """``{provider_id: {kind: [model, ...]}}``；供 Web 打开页先填下拉。"""
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT provider_id, kind, models_json FROM model_catalogs"
                ).fetchall()
        out: dict[str, dict[str, list[Any]]] = {}
        for r in rows:
            pid = str(r["provider_id"] or "").strip()
            kind = str(r["kind"] or "").strip() or "all"
            if not pid:
                continue
            try:
                models = json.loads(r["models_json"] or "[]")
            except json.JSONDecodeError:
                models = []
            if not isinstance(models, list):
                models = []
            out.setdefault(pid, {})[kind] = models
        return out

    def set_model_catalog(
        self,
        provider_id: str,
        kind: str,
        models: list[Any],
        *,
        source: str = "",
        base_url: str = "",
    ) -> None:
        pid = (provider_id or "").strip()
        kind_n = (kind or "").strip() or "all"
        if not pid:
            return
        payload = models if isinstance(models, list) else []
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO model_catalogs(
                        provider_id, kind, models_json, source, base_url, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, kind) DO UPDATE SET
                        models_json = excluded.models_json,
                        source = excluded.source,
                        base_url = excluded.base_url,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        pid,
                        kind_n,
                        json.dumps(payload, ensure_ascii=False),
                        str(source or ""),
                        str(base_url or ""),
                        time.time(),
                    ),
                )
                conn.commit()
        bump_generation()

    def clear_model_catalogs(self, provider_id: str | None = None) -> None:
        pid = (provider_id or "").strip()
        with self._lock:
            with self._connect() as conn:
                if pid:
                    conn.execute(
                        "DELETE FROM model_catalogs WHERE provider_id = ?", (pid,)
                    )
                else:
                    conn.execute("DELETE FROM model_catalogs")
                conn.commit()
        bump_generation()

    def settings_payload(self) -> dict[str, Any]:
        providers = self.list_providers(include_secrets=False)
        routes = self.list_routes()
        return {
            "ok": True,
            "db_path": self.db_path,
            "generation": current_generation(),
            "providers": providers,
            "routes": routes,
            "roles": list(ROUTE_ROLES),
            "screenshot_grounding": self.get_screenshot_grounding(),
            "model_catalogs": self.list_model_catalogs(),
            "updated_at": time.time(),
        }


def get_store(*, data_dir: str | None = None) -> LlmSettingsStore:
    global _cached
    with _store_lock:
        if data_dir is not None:
            return LlmSettingsStore(data_dir=data_dir)
        if _cached is None:
            _cached = LlmSettingsStore()
        return _cached


def reset_store_cache() -> None:
    """测试用：清空进程内单例。"""
    global _cached
    with _store_lock:
        _cached = None
        bump_generation()
