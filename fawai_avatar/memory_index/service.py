"""Host-facing reindex / search for filename + content FTS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.memory_index.content_fts import ContentFtsIndex
from fawai_avatar.memory_index.name_index import NameIndex


def index_data_dir() -> Path:
    from fawai_avatar.config import load_dotenv, _env

    load_dotenv()
    root = Path(_env("FAWAI_DATA_DIR") or "data").expanduser().resolve()
    out = root / "memory_index"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _name_db() -> Path:
    return index_data_dir() / "names.sqlite"


def _content_db() -> Path:
    return index_data_dir() / "content.sqlite"


def reindex_name_index(*, on_progress=None) -> dict[str, Any]:
    from fawai_avatar.config import name_index_enabled, resolved_name_index_roots

    if not name_index_enabled(default=True):
        return {"ok": False, "error": "文件名索引已关闭", "indexed": 0}
    roots = resolved_name_index_roots()
    if on_progress is None:
        from fawai_avatar.memory_index.progress import report_progress

        on_progress = report_progress
    idx = NameIndex(_name_db())
    try:
        result = idx.reindex_roots(roots, on_progress=on_progress)
        result["db"] = str(_name_db())
        return result
    finally:
        idx.close()


def reindex_content_fts(*, on_progress=None) -> dict[str, Any]:
    from fawai_avatar.config import resolved_fts_roots, workspace_content_fts_enabled

    if not workspace_content_fts_enabled(default=True):
        return {"ok": False, "error": "工作区/可读区未开启正文 FTS", "indexed": 0}
    roots = resolved_fts_roots()
    if on_progress is None:
        from fawai_avatar.memory_index.progress import report_progress

        on_progress = report_progress
    idx = ContentFtsIndex(_content_db())
    try:
        result = idx.reindex_roots(roots, on_progress=on_progress)
        result["db"] = str(_content_db())
        return result
    finally:
        idx.close()


def reindex_all(*, on_progress=None) -> dict[str, Any]:
    if on_progress is None:
        from fawai_avatar.memory_index.progress import report_progress

        on_progress = report_progress
    if on_progress:
        on_progress(phase="indexing", message="重建文件名索引…", indexed=0)
    name = reindex_name_index(on_progress=on_progress)
    if on_progress:
        on_progress(
            phase="indexing",
            message="重建正文 FTS…",
            indexed=int(name.get("indexed") or 0),
        )
    content = reindex_content_fts(on_progress=on_progress)
    ok = bool(name.get("ok")) and bool(content.get("ok"))
    indexed = int(name.get("indexed") or 0) + int(content.get("indexed") or 0)
    return {
        "ok": ok,
        "indexed": indexed,
        "name_index": name,
        "content_fts": content,
        "error": None if ok else (name.get("error") or content.get("error")),
    }


def ensure_name_index(*, force: bool = False) -> dict[str, Any]:
    """缺库时按当前工作区默认自动建文件名索引。"""
    db = _name_db()
    if db.is_file() and not force:
        try:
            idx = NameIndex(db)
            try:
                if idx.count() > 0:
                    return {"ok": True, "skipped": True, "indexed": idx.count()}
            finally:
                idx.close()
        except Exception:
            pass
    return reindex_name_index()


def ensure_content_fts(*, force: bool = False) -> dict[str, Any]:
    """缺库时按当前工作区默认自动建正文 FTS。"""
    db = _content_db()
    if db.is_file() and not force:
        try:
            idx = ContentFtsIndex(db)
            try:
                if idx.count() > 0:
                    return {"ok": True, "skipped": True, "indexed": idx.count()}
            finally:
                idx.close()
        except Exception:
            pass
    return reindex_content_fts()


def _filter_out_data_dir_hits(
    hits: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """去掉 FAWAI_DATA_DIR 下残留命中；返回 (截断后列表, 滤掉条数)。"""
    from pathlib import Path

    from fawai_avatar.memory_index.common import _resolve_data_dir, _under_data_dir

    data_dir = _resolve_data_dir()
    if data_dir is None:
        return hits[:limit], 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for h in hits:
        path = str(h.get("path") or "")
        if not path:
            continue
        try:
            if _under_data_dir(Path(path), data_dir):
                dropped += 1
                continue
        except Exception:
            pass
        kept.append(h)
        if len(kept) >= limit:
            break
    return kept, dropped


def search_names(query: str, *, limit: int = 50) -> dict[str, Any]:
    from fawai_avatar.config import name_index_enabled

    if not name_index_enabled(default=True):
        return {"ok": False, "error": "文件名索引已关闭（记忆配置）", "hits": [], "count": 0}
    limit = max(1, min(int(limit or 50), 100))
    db = _name_db()
    if not db.is_file():
        built = ensure_name_index()
        if not built.get("ok"):
            return {
                "ok": False,
                "error": built.get("error") or "文件名索引未建",
                "hits": [],
                "build": built,
            }
    idx = NameIndex(_name_db())
    try:
        # 多取一些再滤 data_dir，避免观察落盘残留占满 limit
        raw = idx.search(query, limit=min(100, max(limit * 4, limit + 20)))
        hits, dropped = _filter_out_data_dir_hits(raw, limit=limit)
        out: dict[str, Any] = {
            "ok": True,
            "query": query,
            "hits": hits,
            "count": len(hits),
        }
        if dropped:
            out["dropped_data_dir"] = dropped
        return out
    finally:
        idx.close()


def search_content(query: str, *, limit: int = 40) -> dict[str, Any]:
    from fawai_avatar.config import workspace_content_fts_enabled

    if not workspace_content_fts_enabled(default=True):
        return {
            "ok": False,
            "error": "工作区/可读区未开启正文 FTS（记忆配置）",
            "hits": [],
            "count": 0,
        }
    limit = max(1, min(int(limit or 40), 100))
    db = _content_db()
    if not db.is_file():
        built = ensure_content_fts()
        if not built.get("ok"):
            return {
                "ok": False,
                "error": built.get("error") or "正文 FTS 未建",
                "hits": [],
                "build": built,
            }
    idx = ContentFtsIndex(_content_db())
    try:
        raw = idx.search(query, limit=min(100, max(limit * 4, limit + 20)))
        hits, dropped = _filter_out_data_dir_hits(raw, limit=limit)
        out: dict[str, Any] = {
            "ok": True,
            "query": query,
            "hits": hits,
            "count": len(hits),
        }
        if dropped:
            out["dropped_data_dir"] = dropped
        return out
    finally:
        idx.close()


def status_snapshot() -> dict[str, Any]:
    from fawai_avatar.config import (
        name_index_enabled,
        resolved_fts_roots,
        resolved_name_index_roots,
        workspace_content_fts_enabled,
    )
    from fawai_avatar.memory_settings import get_memory_store
    from fawai_lore.fts import fts5_available

    name_db = _name_db()
    content_db = _content_db()
    name_count = 0
    content_count = 0
    if name_db.is_file():
        try:
            idx = NameIndex(name_db)
            name_count = idx.count()
            idx.close()
        except Exception:
            name_count = -1
    if content_db.is_file():
        try:
            idx = ContentFtsIndex(content_db)
            content_count = idx.count()
            idx.close()
        except Exception:
            content_count = -1
    store = get_memory_store()
    try:
        from fawai_avatar.memory_index.watch import memory_index_watch_status

        watch = memory_index_watch_status()
    except Exception:
        watch = {"enabled": False, "watching": False}
    return {
        "fts5_engine": fts5_available(),
        "fts5_enabled": workspace_content_fts_enabled(default=True),
        "fts_roots": resolved_fts_roots(),
        "content_db": str(content_db),
        "content_docs": content_count,
        "name_index_enabled": name_index_enabled(default=True),
        "name_index_roots": resolved_name_index_roots(),
        "name_db": str(name_db),
        "name_files": name_count,
        "index_policy": store.index_policy(),
        "watch": watch,
    }
