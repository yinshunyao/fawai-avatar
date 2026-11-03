# term:个人知识库  doc:doc/术语与称呼索引.md
"""HTTP helpers for lore buffer / vector status (used by lan_gateway)."""

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
    from fawai_lore import list_buffer

    rows = list_buffer(root, limit=500)
    return {
        "ok": True,
        "lore_dir": root,
        "vector_enabled": lore_vector_enabled(),
        "sqlite_vec_available": vec_pkg,
        "embed_model": lore_embed_model(),
        "buffer_count": len(rows),
    }


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


def lore_vector_reindex_api() -> dict[str, Any]:
    from fawai_avatar.lore_embed import reindex_all_notes

    return reindex_all_notes()
