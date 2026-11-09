# term:个人知识库  doc:doc/术语与称呼索引.md
"""HTTP helpers for lore buffer / entries / vector status (used by lan_gateway)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.config import (
    lore_embed_model,
    lore_fts5_enabled,
    lore_note_fts_enabled,
    lore_sidecar_enabled,
    lore_vector_enabled,
    name_index_enabled,
    resolve_lore_dir,
    resolved_fts_roots,
    resolved_name_index_roots,
    workspace_content_fts_enabled,
)
from fawai_avatar.memory_settings import get_memory_store


def lore_status() -> dict[str, Any]:
    root = resolve_lore_dir()
    vec_pkg = False
    try:
        from fawai_lore.vector import sqlite_vec_available

        vec_pkg = bool(sqlite_vec_available())
    except Exception:
        vec_pkg = False
    from fawai_avatar.config import lore_auto_p1_kv
    from fawai_lore import list_buffer, open_lore
    from fawai_lore.fts import fts5_available

    rows = list_buffer(root, limit=500)
    lore = open_lore(root)
    kv_n = len(lore.kv.keys())
    note_n = len(lore.notes.list_all())
    mem = get_memory_store()
    lore_fts_docs = 0
    fts_path = Path(root) / "fts.sqlite"
    if fts_path.is_file() and fts5_available():
        try:
            from fawai_lore.fts import LoreFtsIndex

            idx = LoreFtsIndex(root)
            try:
                lore_fts_docs = idx.count()
            finally:
                idx.close()
        except Exception:
            lore_fts_docs = -1
    fs_index: dict[str, Any] = {}
    try:
        from fawai_avatar.memory_index import status_snapshot

        fs_index = status_snapshot()
    except Exception as exc:
        fs_index = {"error": str(exc)}
    policy_payload = mem.index_policy_payload()
    return {
        "ok": True,
        "lore_dir": root,
        "vector_enabled": lore_vector_enabled(),
        "vector_configured": mem.vector_enabled(default=True),
        "vector_dir": mem.vector_dir_raw(),
        "vector_roots": mem.resolved_vector_roots(),
        "sqlite_vec_available": vec_pkg,
        "embed_model": lore_embed_model(),
        "fts5_enabled": lore_fts5_enabled(default=True),
        "workspace_fts_enabled": workspace_content_fts_enabled(default=True),
        "lore_note_fts_enabled": lore_note_fts_enabled(default=True),
        "fts5_engine": fts5_available(),
        "fts_roots": resolved_fts_roots(),
        "lore_fts_docs": lore_fts_docs,
        "lore_fts_db": str(fts_path),
        "name_index_enabled": name_index_enabled(default=True),
        "name_index_roots": resolved_name_index_roots(),
        "fs_index": fs_index,
        "index_policy": policy_payload.get("policy"),
        "index_catalog": policy_payload.get("catalog"),
        "buffer_count": len(rows),
        "entry_count": kv_n + note_n,
        "kv_count": kv_n,
        "note_count": note_n,
        "auto_p1_kv": lore_auto_p1_kv(default=False),
        "sidecar_enabled": lore_sidecar_enabled(default=False),
    }


def lore_search(
    *,
    query: str,
    limit: int = 10,
    scopes: list | str | None = None,
) -> dict[str, Any]:
    """Web / 工具：按 scopes 检索；缺省混合已开通道（不再搜旁路 KV）。"""
    from fawai_avatar.knowledge_search import knowledge_search

    return knowledge_search(query=query, limit=limit, scopes=scopes)


def lore_get(*, key: str) -> dict[str, Any]:
    """Web / 工具：笔记 id / 文件路径 / 索引命中（不以 KV key 为主）。"""
    from fawai_avatar.knowledge_search import knowledge_get

    return knowledge_get(key=key)


def lore_recent_commits(*, limit: int = 40) -> dict[str, Any]:
    """Recently auto-committed / promoted rows from sidecar_log."""
    from fawai_lore import list_sidecar_log

    rows = list_sidecar_log(
        resolve_lore_dir(),
        limit=limit,
        actions=("set", "note_upsert", "buffer_promote"),
    )
    # buffer_promote is not a log action; applied promote logs as set/note_upsert
    return {"ok": True, "rows": rows, "count": len(rows)}


def lore_buffer_list(*, limit: int = 80) -> dict[str, Any]:
    from fawai_lore import list_buffer

    rows = list_buffer(resolve_lore_dir(), limit=limit)
    return {"ok": True, "rows": rows, "count": len(rows)}


def lore_buffer_clear() -> dict[str, Any]:
    from fawai_lore import clear_buffer

    n = clear_buffer(resolve_lore_dir())
    return {"ok": True, "cleared": n}


def lore_entries_clear_all() -> dict[str, Any]:
    """硬清已入库：全部 KV + 笔记（并清空 lore FTS / 向量表）。"""
    from fawai_lore import open_lore
    from fawai_lore.fts import LoreFtsIndex, fts5_available

    root = resolve_lore_dir()
    lore = open_lore(root)
    kv_keys = [e.key for e in lore.kv.list_prefix()]
    note_ids = [n.id for n in lore.notes.list_all()]
    kv_n = 0
    note_n = 0
    for key in kv_keys:
        if lore.kv.delete(key):
            kv_n += 1
    for nid in note_ids:
        if lore.notes.delete(nid):
            note_n += 1

    fts_cleared = False
    if fts5_available():
        try:
            idx = LoreFtsIndex(root)
            try:
                idx.clear()
                fts_cleared = True
            finally:
                idx.close()
        except Exception:
            pass

    vec_cleared = 0
    try:
        from fawai_lore.vector import VectorIndex, sqlite_vec_available

        if sqlite_vec_available():
            idx = VectorIndex(root)
            try:
                for nid in note_ids:
                    try:
                        if idx.delete(nid):
                            vec_cleared += 1
                    except Exception:
                        continue
            finally:
                idx.close()
    except Exception:
        pass

    return {
        "ok": True,
        "kv_deleted": kv_n,
        "notes_deleted": note_n,
        "fts_cleared": fts_cleared,
        "vector_deleted": vec_cleared,
    }


def lore_purge_short_kv_data() -> dict[str, Any]:
    """关闭旁路场景：清空待确认缓冲 + 已入库 KV/笔记。"""
    buf = lore_buffer_clear()
    entries = lore_entries_clear_all()
    # 旁路日志只作审计；一并截断以免「最近入库」仍堆旧短 KV
    log_cleared = False
    try:
        from pathlib import Path

        log_path = Path(resolve_lore_dir()) / "sidecar_log.jsonl"
        if log_path.is_file():
            log_path.write_text("", encoding="utf-8")
            log_cleared = True
    except Exception:
        pass
    return {
        "ok": True,
        "buffer_cleared": int(buf.get("cleared") or 0),
        **{k: v for k, v in entries.items() if k != "ok"},
        "sidecar_log_cleared": log_cleared,
    }


def lore_buffer_promote(row: dict[str, Any]) -> dict[str, Any]:
    from fawai_lore import promote_buffer_row

    if not isinstance(row, dict) or not row:
        return {"ok": False, "error": "row required"}
    result = promote_buffer_row(resolve_lore_dir(), row, drop=True)
    # index notes if vector on
    if result.get("action") == "note_upsert":
        try:
            from fawai_avatar.lore_embed import index_note_text

            note = result.get("note") or {}
            index_note_text(
                str(note.get("id") or ""),
                f"{note.get('title') or ''}\n{note.get('body') or ''}",
            )
        except Exception:
            pass
    ok = result.get("action") not in {None, "skip"}
    return {"ok": ok, "result": result}


def lore_buffer_drop(row: dict[str, Any]) -> dict[str, Any]:
    from fawai_lore import drop_buffer_row

    if not isinstance(row, dict) or not row:
        return {"ok": False, "error": "row required"}
    dropped = drop_buffer_row(resolve_lore_dir(), row)
    return {"ok": dropped, "dropped": dropped}


def lore_entries_list(*, limit: int = 200) -> dict[str, Any]:
    """Committed KV + notes for Web management (enable / delete)."""
    from fawai_lore import open_lore

    limit = max(1, min(int(limit), 500))
    lore = open_lore(resolve_lore_dir())
    rows: list[dict[str, Any]] = []
    for entry in lore.kv.list_prefix():
        rows.append(
            {
                "kind": "kv",
                "id": entry.key,
                "key": entry.key,
                "value": entry.value,
                "source": entry.source,
                "aliases": list(entry.aliases),
                "updated_at": entry.updated_at,
                "enabled": bool(entry.enabled),
            }
        )
    for note in lore.notes.list_all():
        rows.append(
            {
                "kind": "note",
                "id": note.id,
                "title": note.title,
                "body": note.body,
                "tags": list(note.tags),
                "related_keys": list(note.related_keys),
                "source": note.source,
                "updated_at": note.updated_at,
                "created_at": note.created_at,
                "enabled": bool(note.enabled),
            }
        )
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    rows = rows[:limit]
    return {"ok": True, "rows": rows, "count": len(rows)}


def lore_entry_set_enabled(
    *,
    kind: str,
    id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Soft enable/disable a committed KV or note. Disabled rows skip retrieve."""
    from fawai_lore import KvValidationError, open_lore

    kind_n = str(kind or "").strip().lower()
    eid = str(id or "").strip()
    if kind_n not in {"kv", "note"} or not eid:
        return {"ok": False, "error": "kind (kv|note) and id required"}
    lore = open_lore(resolve_lore_dir())
    if kind_n == "kv":
        try:
            entry = lore.kv.set_enabled(eid, bool(enabled))
        except KvValidationError as exc:
            return {"ok": False, "error": str(exc)}
        if entry is None:
            return {"ok": False, "error": "kv not found"}
        return {
            "ok": True,
            "kind": "kv",
            "id": entry.key,
            "enabled": bool(entry.enabled),
            "entry": entry.to_dict(),
        }
    note = lore.notes.set_enabled(eid, bool(enabled))
    if note is None:
        return {"ok": False, "error": "note not found"}
    return {
        "ok": True,
        "kind": "note",
        "id": note.id,
        "enabled": bool(note.enabled),
        "entry": note.to_dict(),
    }


def lore_entry_delete(*, kind: str, id: str) -> dict[str, Any]:
    """Hard-delete a committed KV or note (and note vector row if present)."""
    from fawai_lore import KvValidationError, open_lore

    kind_n = str(kind or "").strip().lower()
    eid = str(id or "").strip()
    if kind_n not in {"kv", "note"} or not eid:
        return {"ok": False, "error": "kind (kv|note) and id required"}
    lore = open_lore(resolve_lore_dir())
    if kind_n == "kv":
        try:
            deleted = lore.kv.delete(eid)
        except KvValidationError as exc:
            return {"ok": False, "error": str(exc)}
        if not deleted:
            return {"ok": False, "error": "kv not found"}
        sync_lore_fts_entry(kind="kv", id=eid, delete=True)
        return {"ok": True, "kind": "kv", "id": eid, "deleted": True}
    deleted = lore.notes.delete(eid)
    if not deleted:
        return {"ok": False, "error": "note not found"}
    try:
        from fawai_lore.vector import VectorIndex, sqlite_vec_available

        if sqlite_vec_available():
            idx = VectorIndex(lore.root)
            try:
                idx.delete(eid)
            finally:
                idx.close()
    except Exception:
        pass
    sync_lore_fts_entry(kind="note", id=eid, delete=True)
    return {"ok": True, "kind": "note", "id": eid, "deleted": True}


def _parse_kv_edit_value(raw: Any) -> Any:
    """Web textarea may send JSON text; keep plain strings as strings."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "{[" or text in {"true", "false", "null"}:
        try:
            import json

            return json.loads(text)
        except Exception:
            return raw
    if text[0] in "-0123456789":
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, (int, float)):
                return parsed
        except Exception:
            pass
    return raw


def lore_entry_update(
    *,
    kind: str,
    id: str,
    title: str | None = None,
    body: str | None = None,
    value: Any = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update committed KV value or note title/body (Web detail editor)."""
    from fawai_lore import KvValidationError, NoteValidationError, open_lore

    kind_n = str(kind or "").strip().lower()
    eid = str(id or "").strip()
    if kind_n not in {"kv", "note"} or not eid:
        return {"ok": False, "error": "kind (kv|note) and id required"}
    lore = open_lore(resolve_lore_dir())
    if kind_n == "kv":
        existing = lore.kv.get(eid)
        if existing is None:
            return {"ok": False, "error": "kv not found"}
        if value is None:
            return {"ok": False, "error": "value required"}
        try:
            entry = lore.kv.set(
                eid,
                _parse_kv_edit_value(value),
                source=existing.source,
                aliases=list(existing.aliases),
                merge_aliases=False,
                enabled=bool(existing.enabled),
            )
        except KvValidationError as exc:
            return {"ok": False, "error": str(exc)}
        sync_lore_fts_entry(
            kind="kv",
            id=entry.key,
            value=entry.value,
            aliases=list(entry.aliases),
            enabled=bool(entry.enabled),
        )
        return {
            "ok": True,
            "kind": "kv",
            "id": entry.key,
            "entry": entry.to_dict(),
        }

    existing_note = lore.notes.get(eid)
    if existing_note is None:
        return {"ok": False, "error": "note not found"}
    next_title = existing_note.title if title is None else str(title)
    next_body = existing_note.body if body is None else str(body)
    next_tags = list(existing_note.tags) if tags is None else list(tags)
    try:
        note = lore.notes.upsert(
            {
                "id": existing_note.id,
                "title": next_title,
                "body": next_body,
                "tags": next_tags,
                "related_keys": list(existing_note.related_keys),
                "source": existing_note.source,
                "enabled": bool(existing_note.enabled),
            }
        )
    except NoteValidationError as exc:
        return {"ok": False, "error": str(exc)}
    sync_lore_fts_entry(
        kind="note",
        id=note.id,
        title=note.title,
        body=note.body,
        tags=list(note.tags),
        related_keys=list(note.related_keys),
        source=note.source,
        enabled=bool(note.enabled),
    )
    try:
        from fawai_avatar.lore_embed import index_note_text

        index_note_text(note.id, f"{note.title}\n{note.body}")
    except Exception:
        pass
    return {
        "ok": True,
        "kind": "note",
        "id": note.id,
        "entry": note.to_dict(),
    }


def lore_vector_reindex_api() -> dict[str, Any]:
    from fawai_avatar.lore_embed import reindex_all_notes
    from fawai_avatar.memory_index.progress import report_progress

    report_progress(phase="embedding", message="重建笔记向量…", indexed=0)
    result = reindex_all_notes()
    if result.get("ok"):
        report_progress(
            phase="embedding",
            message=f"向量重建完成 {result.get('indexed') or 0}",
            indexed=int(result.get("indexed") or 0),
        )
    return result


def lore_fts_reindex_api() -> dict[str, Any]:
    """Rebuild lore notes FTS5 index at ``{lore}/fts.sqlite``."""
    from fawai_avatar.memory_index.progress import report_progress
    from fawai_lore import open_lore
    from fawai_lore.fts import LoreFtsIndex, fts5_available

    if not lore_note_fts_enabled(default=True):
        return {"ok": False, "error": "知识库存盘未开启笔记 FTS"}
    if not fts5_available():
        return {"ok": False, "error": "本机 SQLite 无 FTS5"}
    root = resolve_lore_dir()
    lore = open_lore(root)
    notes = lore.notes.list_all()
    report_progress(
        phase="indexing",
        message=f"重建知识库 FTS（笔记 {len(notes)}）…",
        indexed=0,
        total=len(notes),
    )
    idx = LoreFtsIndex(root)
    try:
        # 主搜已不依赖 KV；FTS 仍可顺带索引 KV 便于运维，但进度按笔记计
        stats = idx.reindex(kv_entries=lore.kv.list_prefix(), notes=notes)
        report_progress(
            phase="indexing",
            message=f"知识库 FTS 完成 {stats.get('total') or 0}",
            indexed=int(stats.get("total") or 0),
            total=int(stats.get("total") or 0),
        )
        return {"ok": True, "db": str(idx.path), "indexed": stats.get("total"), **stats}
    finally:
        idx.close()


def ensure_lore_fts(*, force: bool = False) -> dict[str, Any]:
    """知识库 FTS：缺库或空表时自动重建（用户数据区默认可用）。"""
    from fawai_lore.fts import LoreFtsIndex, fts5_available

    if not lore_note_fts_enabled(default=True):
        return {"ok": False, "error": "知识库存盘未开启笔记 FTS", "skipped": True}
    if not fts5_available():
        return {"ok": False, "error": "本机 SQLite 无 FTS5", "skipped": True}
    root = resolve_lore_dir()
    path = Path(root) / "fts.sqlite"
    if path.is_file() and not force:
        try:
            idx = LoreFtsIndex(root)
            try:
                if idx.count() > 0:
                    return {"ok": True, "skipped": True, "total": idx.count()}
            finally:
                idx.close()
        except Exception:
            pass
    return lore_fts_reindex_api()


def sync_lore_fts_entry(*, kind: str, id: str, delete: bool = False, **payload: Any) -> None:
    if not lore_note_fts_enabled(default=True):
        return
    from fawai_lore.fts import sync_lore_fts_after_write

    sync_lore_fts_after_write(
        resolve_lore_dir(), kind=kind, doc_id=id, delete=delete, **payload
    )


def memory_index_reindex_api(*, which: str = "all") -> dict[str, Any]:
    from fawai_avatar import memory_index as mi

    which_n = str(which or "all").strip().lower()
    if which_n in {"name", "names", "name_index"}:
        return mi.reindex_name_index()
    if which_n in {"fts", "content", "content_fts"}:
        return mi.reindex_content_fts()
    return mi.reindex_all()


def _reindex_fn(which: str):
    which_n = str(which or "").strip().lower()
    if which_n in {"lore_fts", "lore-fts", "knowledge_fts"}:
        return lore_fts_reindex_api
    if which_n in {"vector", "lore_vector"}:
        return lore_vector_reindex_api
    if which_n in {"name", "names", "name_index"}:
        from fawai_avatar.memory_index import reindex_name_index

        return reindex_name_index
    if which_n in {"fts", "content", "content_fts"}:
        from fawai_avatar.memory_index import reindex_content_fts

        return reindex_content_fts
    if which_n in {"all", "memory"}:
        from fawai_avatar.memory_index import reindex_all

        return reindex_all
    raise ValueError(f"unknown reindex which: {which}")


def start_reindex_job(*, which: str) -> dict[str, Any]:
    """Web：后台启动重建，立即返回；用 progress API 轮询。"""
    from fawai_avatar.memory_index.progress import start_job

    which_n = str(which or "").strip().lower() or "all"
    try:
        fn = _reindex_fn(which_n)
    except ValueError as exc:
        return {"ok": False, "started": False, "error": str(exc)}
    return start_job(which_n, fn)


def reindex_progress_api() -> dict[str, Any]:
    from fawai_avatar.memory_index.progress import snapshot

    return {"ok": True, **snapshot()}
