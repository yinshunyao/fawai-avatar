# term:数据目录  doc:doc/功能/任务与事件管理/说明.md
"""Normalize ``data_dir`` for SQLite stores; reject store objects mistaken for paths."""
from __future__ import annotations

import os

_OBJECT_REPR_MARK = " object at 0x"


def resolve_data_dir(
    data_dir: str | os.PathLike[str] | None,
    *,
    default: str = "data",
) -> str:
    """Return absolute data directory path.

    Accepts ``str`` / ``PathLike`` only. Passing a store/supervisor instance
    (or its ``str()`` leftover like ``<TaskStore object at 0x...>``) raises
    ``TypeError`` so we never ``makedirs`` junk folders under cwd.
    """
    if data_dir is None or data_dir == "":
        return os.path.abspath(os.path.expanduser(default))
    if not isinstance(data_dir, (str, os.PathLike)):
        raise TypeError(
            "data_dir must be a path str or PathLike, "
            f"got {type(data_dir).__name__}; pass .data_dir instead of the store object"
        )
    text = os.fspath(data_dir).strip()
    if not text:
        return os.path.abspath(os.path.expanduser(default))
    # Already-stringified object repr (cwd junk dir names)
    if text.startswith("<") and _OBJECT_REPR_MARK in text.lower():
        raise TypeError(
            "data_dir looks like a Python object repr "
            f"({text!r}); pass a real directory path, not str(store)"
        )
    return os.path.abspath(os.path.expanduser(text))
