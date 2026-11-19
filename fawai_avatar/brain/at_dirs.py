# term:会话@工作区目录  doc:doc/功能/01-接入与呈现/08-会话@工作区目录/说明.md
"""compose @ 工作区目录：清洗 meta.at_dirs 与规划侧【语义目录】软偏置。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.paths import WorkspacePathError, normalize_work_root, resolve_under_root

logger = logging.getLogger(__name__)

_AT_DIRS_CAP = 32


def _normalize_rel(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    if s.startswith("@"):
        s = s[1:].lstrip()
    # 绝对路径（含盘符）一律拒绝；须在 strip 前导斜杠之前判定
    if s.startswith("/") or (len(s) >= 2 and s[1] == ":"):
        return None
    s = s.strip("/")
    if not s or s in {".", ".."} or ".." in s.split("/"):
        return None
    return s


def sanitize_at_dirs(
    raw: Any,
    *,
    work_root: str | Path | None = None,
) -> list[str]:
    """清洗路径列表：相对 POSIX、落在 work_root 内（若提供）、去重保序。

    非法项丢弃并打日志，不抛错。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return []

    root: Path | None = None
    if work_root is not None:
        try:
            root = normalize_work_root(work_root)
        except WorkspacePathError as exc:
            logger.warning("at_dirs: invalid work_root (%s); skip path checks", exc)
            root = None

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        rel = _normalize_rel(item)
        if not rel:
            logger.info("at_dirs: drop invalid path %r", item)
            continue
        if root is not None:
            try:
                target = resolve_under_root(root, rel)
            except WorkspacePathError:
                logger.info("at_dirs: drop escaped path %r", rel)
                continue
            if not target.exists():
                # 允许尚未存在的路径作语义焦点（仍须在 root 下）
                pass
            elif not (target.is_dir() or target.is_file()):
                logger.info("at_dirs: drop non-path %r", rel)
                continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
        if len(out) >= _AT_DIRS_CAP:
            break
    return out


def at_dirs_from_meta(meta: dict[str, Any] | None) -> list[str]:
    if not isinstance(meta, dict):
        return []
    return sanitize_at_dirs(meta.get("at_dirs"))


def at_dirs_from_inbound_meta(
    meta: dict[str, Any] | None,
    *,
    work_root: str | Path | None = None,
) -> list[str] | None:
    """入站显式带 at_dirs 时返回清洗列表；未带则 None（不改任务旧值）。"""
    if not isinstance(meta, dict) or "at_dirs" not in meta:
        return None
    return sanitize_at_dirs(meta.get("at_dirs"), work_root=work_root)


def merge_at_dirs(existing: Any, inbound: Any, *, work_root: str | Path | None = None) -> list[str]:
    """合并任务已有 at_dirs 与本轮入站；保序去重。"""
    prev = sanitize_at_dirs(existing, work_root=work_root)
    cur = sanitize_at_dirs(inbound, work_root=work_root)
    if not cur:
        return prev
    seen = set(prev)
    out = list(prev)
    for p in cur:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= _AT_DIRS_CAP:
            break
    return out


def format_semantic_dirs_block(dirs: list[str]) -> str:
    """规划 system 旁注：【语义目录】软偏置。"""
    cleaned = sanitize_at_dirs(dirs)
    if not cleaned:
        return ""
    lines = [
        "【语义路径】用户用 @ 指定的工作区焦点（相对 work_root，可为目录或文件）：",
    ]
    for p in cleaned:
        lines.append(f"- {p}")
    lines.append(
        "检索与改写优先落在这些路径（目录含其子树）；勿无依据扩到无关顶层。"
    )
    return "\n".join(lines)
