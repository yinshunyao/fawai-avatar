# term:全文索引 term:文件名索引  doc:doc/功能/个人知识库/说明.md
"""Filesystem filename index + content FTS5 over configured memory roots."""

from __future__ import annotations

from fawai_avatar.memory_index.content_fts import ContentFtsIndex, open_content_fts
from fawai_avatar.memory_index.name_index import NameIndex, open_name_index
from fawai_avatar.memory_index.progress import snapshot as progress_snapshot
from fawai_avatar.memory_index.service import (
    ensure_content_fts,
    ensure_name_index,
    index_data_dir,
    reindex_all,
    reindex_content_fts,
    reindex_name_index,
    search_content,
    search_names,
    status_snapshot,
)
from fawai_avatar.memory_index.watch import (
    apply_fs_changes,
    memory_index_watch_status,
    start_memory_index_watch,
    stop_memory_index_watch,
)

__all__ = [
    "ContentFtsIndex",
    "NameIndex",
    "apply_fs_changes",
    "ensure_content_fts",
    "ensure_name_index",
    "index_data_dir",
    "memory_index_watch_status",
    "open_content_fts",
    "open_name_index",
    "progress_snapshot",
    "reindex_all",
    "reindex_content_fts",
    "reindex_name_index",
    "search_content",
    "search_names",
    "start_memory_index_watch",
    "status_snapshot",
    "stop_memory_index_watch",
]
