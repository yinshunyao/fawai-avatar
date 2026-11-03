# term:个人知识库  doc:doc/术语与称呼索引.md
"""Embedding + sqlite-vec index helpers for lore notes."""

from __future__ import annotations

import logging
from typing import Any

from fawai_avatar.config import (
    lore_vector_enabled,
    resolve_lore_dir,
)

logger = logging.getLogger(__name__)


def make_embed_fn(*, allow_fake: bool = False):
    """Return embed_fn(text)->list[float]. API first; optional fake for tests."""

    def _api_embed(text: str) -> list[float]:
        from fawai_avatar.llm.client import LlmClient

        resp = LlmClient().embed([text or " "])
        if not resp.get("ok") or not resp.get("embeddings"):
            raise RuntimeError(resp.get("error") or "embed failed")
        return list(resp["embeddings"][0])

    if allow_fake:
        from fawai_lore.vector import fake_embed

        return fake_embed

    return _api_embed


def index_note_text(note_id: str, text: str, *, embed_fn=None) -> bool:
    if not lore_vector_enabled():
        return False
    try:
        from fawai_lore.vector import VectorIndex, sqlite_vec_available

        if not sqlite_vec_available():
            return False
        fn = embed_fn or make_embed_fn()
        idx = VectorIndex(resolve_lore_dir())
        try:
            idx.upsert_with_embed(note_id, text, fn)
        finally:
            idx.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("lore vector index note failed: %s", exc)
        return False


def reindex_all_notes(*, embed_fn=None) -> dict[str, Any]:
    if not lore_vector_enabled():
        return {"ok": False, "error": "FAWAI_LORE_VECTOR off"}
    try:
        from fawai_avatar.lore import open_lore_store
        from fawai_lore.vector import VectorIndex, sqlite_vec_available

        if not sqlite_vec_available():
            return {"ok": False, "error": "sqlite-vec not installed"}
        fn = embed_fn or make_embed_fn()
        lore = open_lore_store()
        idx = VectorIndex(lore.root)
        try:
            n = idx.reindex_notes(lore.notes.list_all(), fn)
        finally:
            idx.close()
        return {"ok": True, "indexed": n}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
