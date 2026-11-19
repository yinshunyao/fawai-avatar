# term:会话@工作区目录  doc:doc/功能/01-接入与呈现/08-会话@工作区目录/说明.md
"""列举 work_root 下子目录与文件（供 compose @ 级联菜单）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.workspace.paths import (
    SKIP_DIR_NAMES,
    WorkspacePathError,
    normalize_work_root,
    resolve_under_root,
)

# 额外噪音目录名（与 SKIP_DIR_NAMES 合并）
_EXTRA_SKIP: frozenset[str] = frozenset(
    {
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".cursor",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".DS_Store",
        "dist",
        "build",
        "coverage",
    }
)

_DIR_LIMIT_DEFAULT = 200


def _skip_name(name: str) -> bool:
    if not name or name.startswith("."):
        return True
    return name in SKIP_DIR_NAMES or name in _EXTRA_SKIP


def _rel_posix(work_root: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(work_root.resolve())
    except ValueError as exc:
        raise WorkspacePathError(f"path escapes work root: {path}") from exc
    return rel.as_posix()


def _has_children(path: Path) -> bool:
    """是否有可展示的子目录或文件。"""
    try:
        for child in path.iterdir():
            try:
                if child.is_symlink():
                    continue
                if _skip_name(child.name):
                    continue
                if child.is_dir() or child.is_file():
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def list_workspace_dirs(
    work_root: str | Path,
    rel_path: str = "",
    *,
    limit: int = _DIR_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """列出相对 work_root 的子目录与文件。

    Returns:
        ``{"ok": True, "path": "", "entries": [{"name","path","kind","has_children"}, ...],
           "dirs": [...仅目录...], "truncated": bool}``
    Raises:
        WorkspacePathError: 越界或目标不是目录。
    """
    root = normalize_work_root(work_root)
    raw = (rel_path or "").strip().replace("\\", "/")
    if raw in ("", ".", "/"):
        target = root
        path_out = ""
    else:
        target = resolve_under_root(root, raw)
        if not target.is_dir():
            raise WorkspacePathError(f"not a directory: {raw}")
        path_out = _rel_posix(root, target)

    cap = max(1, min(int(limit or _DIR_LIMIT_DEFAULT), 500))
    dir_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise WorkspacePathError(f"cannot list directory: {path_out or '.'}") from exc

    for child in children:
        if len(dir_rows) + len(file_rows) >= cap:
            break
        try:
            if child.is_symlink():
                continue
            is_dir = child.is_dir()
            is_file = child.is_file()
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        if _skip_name(child.name):
            continue
        child_rel = _rel_posix(root, child)
        if is_dir:
            dir_rows.append(
                {
                    "name": child.name,
                    "path": child_rel,
                    "kind": "dir",
                    "has_children": _has_children(child),
                }
            )
        else:
            file_rows.append(
                {
                    "name": child.name,
                    "path": child_rel,
                    "kind": "file",
                    "has_children": False,
                }
            )

    # 目录在前、文件在后（各自已按名排序）
    entries = dir_rows + file_rows
    return {
        "ok": True,
        "path": path_out,
        "entries": entries,
        "dirs": dir_rows,
        "truncated": len(entries) >= cap,
    }
