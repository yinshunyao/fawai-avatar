# term:个人知识库  doc:doc/术语与称呼索引.md
"""HTTP helpers for lore buffer / entries / vector status (used by lan_gateway)."""

from __future__ import annotations

from typing import Any

from fawai_avatar.config import (
    lore_embed_model,
    lore_vector_enabled,
    resolve_lore_dir,
)


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

    rows = list_buffer(root, limit=500)
    lore = open_lore(root)
    kv_n = len(lore.kv.keys())
    note_n = len(lore.notes.list_all())
    return {
        "ok": True,
        "lore_dir": root,
        "vector_enabled": lore_vector_enabled(),
        "sqlite_vec_available": vec_pkg,
        "embed_model": lore_embed_model(),
        "buffer_count": len(rows),
        "entry_count": kv_n + note_n,
        "kv_count": kv_n,
        "note_count": note_n,
        "auto_p1_kv": lore_auto_p1_kv(default=False),
    }


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
    return {"ok": True, "kind": "note", "id": eid, "deleted": True}


def lore_vector_reindex_api() -> dict[str, Any]:
    from fawai_avatar.lore_embed import reindex_all_notes

    return reindex_all_notes()
