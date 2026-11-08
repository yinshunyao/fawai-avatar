# term:全文索引 term:文件名索引  doc:doc/功能/个人知识库/说明.md
"""监视工作区/可读区文件变更，增量更新文件名与正文索引（复用 watchfiles / mtime 轮询）。"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from fawai_avatar.memory_index.common import (
    is_text_path,
    parse_roots,
    root_too_broad,
    should_skip_dir_name,
)

_lock = threading.Lock()
_stop: threading.Event | None = None
_thread: threading.Thread | None = None
_roots_key: str = ""


def memory_index_watch_enabled() -> bool:
    raw = (os.environ.get("FAWAI_MEMORY_INDEX_WATCH") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _union_watch_roots() -> list[Path]:
    from fawai_avatar.config import (
        name_index_enabled,
        resolved_fts_roots,
        resolved_name_index_roots,
        workspace_content_fts_enabled,
    )

    raw_parts: list[str] = []
    if name_index_enabled(default=True):
        raw_parts.append(resolved_name_index_roots() or "")
    if workspace_content_fts_enabled(default=True):
        raw_parts.append(resolved_fts_roots() or "")
    merged = ":".join(p for p in raw_parts if p)
    roots = parse_roots(merged)
    out: list[Path] = []
    for r in roots:
        if root_too_broad(r):
            continue
        if r.is_dir():
            out.append(r)
    return out


def _path_indexable(path: Path) -> bool:
    try:
        parts = set(path.parts)
    except Exception:
        return False
    if any(should_skip_dir_name(p) for p in path.parts):
        return False
    if path.name.startswith("._"):
        return False
    # 目录事件忽略；删除时 path 可能已不存在，仍交给 apply
    if path.exists() and path.is_dir():
        return False
    return True


def apply_fs_changes(paths: list[str]) -> dict[str, Any]:
    """对变更路径做文件名/正文增量 upsert 或删除。"""
    from fawai_avatar.config import name_index_enabled, workspace_content_fts_enabled
    from fawai_avatar.memory_index.content_fts import ContentFtsIndex
    from fawai_avatar.memory_index.name_index import NameIndex
    from fawai_avatar.memory_index.service import _content_db, _name_db

    uniq = sorted({str(Path(p)) for p in paths if p})
    if not uniq:
        return {"ok": True, "updated": 0, "removed": 0}

    do_name = name_index_enabled(default=True)
    do_content = workspace_content_fts_enabled(default=True)
    name_idx = NameIndex(_name_db()) if do_name else None
    content_idx = ContentFtsIndex(_content_db()) if do_content else None
    updated = 0
    removed = 0
    try:
        for raw in uniq:
            path = Path(raw)
            if not _path_indexable(path) and path.exists():
                continue
            exists = path.is_file()
            if name_idx is not None:
                if exists:
                    if name_idx.upsert_path(path):
                        updated += 1
                else:
                    if name_idx.remove_path(path):
                        removed += 1
            if content_idx is not None:
                if exists and is_text_path(path):
                    if content_idx.upsert_path(path):
                        updated += 1
                else:
                    if content_idx.remove_path(path):
                        removed += 1
    finally:
        if name_idx is not None:
            name_idx.close()
        if content_idx is not None:
            content_idx.close()
    return {"ok": True, "updated": updated, "removed": removed, "paths": len(uniq)}


def _start_watch_thread(roots: list[Path], stop: threading.Event) -> threading.Thread:
    debounce_s = 0.8

    def _on_change(changed: list[str]) -> None:
        try:
            apply_fs_changes(changed)
        except Exception as exc:  # noqa: BLE001
            from fawai_avatar.runtime.console_log import cprint

            cprint(f"[memory-index-watch] apply failed: {exc}")

    def _run_watchfiles() -> None:
        from watchfiles import watch

        def _watch_filter(change: object, path: str) -> bool:  # noqa: ARG001
            return _path_indexable(Path(path))

        for changes in watch(
            *roots,
            watch_filter=_watch_filter,
            stop_event=stop,
            debounce=int(max(0.1, debounce_s) * 1000),
            step=250,
            raise_interrupt=False,
        ):
            paths = [str(p) for _ch, p in changes if _path_indexable(Path(p)) or not Path(p).exists()]
            if paths:
                _on_change(paths)

    def _run_poll() -> None:
        stamps: dict[str, float] = {}

        def _scan() -> dict[str, float]:
            found: dict[str, float] = {}
            for root in roots:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if not should_skip_dir_name(d)]
                    for name in filenames:
                        p = Path(dirpath) / name
                        if not _path_indexable(p):
                            continue
                        try:
                            found[str(p)] = p.stat().st_mtime
                        except OSError:
                            continue
            return found

        stamps = _scan()
        pending: list[str] = []
        first_at = 0.0
        while not stop.wait(0.6):
            now = _scan()
            changed = [p for p, m in now.items() if stamps.get(p) != m]
            for p in stamps:
                if p not in now:
                    changed.append(p)
            stamps = now
            if changed:
                if not pending:
                    first_at = time.monotonic()
                pending.extend(changed)
            if pending and (time.monotonic() - first_at) >= debounce_s:
                uniq = sorted(set(pending))
                pending.clear()
                first_at = 0.0
                _on_change(uniq)

    def _target() -> None:
        from fawai_avatar.runtime.console_log import cprint

        try:
            _run_watchfiles()
        except Exception as exc:  # noqa: BLE001
            cprint(f"[memory-index-watch] watchfiles 不可用，改用轮询: {exc}")
            _run_poll()

    thread = threading.Thread(target=_target, name="fawai-memory-index-watch", daemon=True)
    thread.start()
    return thread


def start_memory_index_watch(*, force_restart: bool = False) -> dict[str, Any]:
    """网关生命周期 / 配置变更后调用；已在跑且根未变则跳过。"""
    global _stop, _thread, _roots_key

    if not memory_index_watch_enabled():
        stop_memory_index_watch()
        return {"ok": True, "watching": False, "reason": "disabled"}

    roots = _union_watch_roots()
    key = ":".join(str(r) for r in roots)
    with _lock:
        if (
            not force_restart
            and _thread is not None
            and _thread.is_alive()
            and _roots_key == key
            and roots
        ):
            return {"ok": True, "watching": True, "roots": [str(r) for r in roots], "skipped": True}
        if _stop is not None:
            _stop.set()
        _stop = threading.Event()
        _thread = None
        _roots_key = key
        if not roots:
            return {"ok": True, "watching": False, "reason": "no_roots"}
        _thread = _start_watch_thread(roots, _stop)
        return {"ok": True, "watching": True, "roots": [str(r) for r in roots]}


def stop_memory_index_watch() -> None:
    global _stop, _thread, _roots_key
    with _lock:
        if _stop is not None:
            _stop.set()
        _stop = None
        _thread = None
        _roots_key = ""


def memory_index_watch_status() -> dict[str, Any]:
    alive = _thread is not None and _thread.is_alive()
    return {
        "enabled": memory_index_watch_enabled(),
        "watching": alive,
        "roots_key": _roots_key,
    }
