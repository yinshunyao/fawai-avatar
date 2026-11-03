# term:工作区文件  tool:fs_*  doc:doc/功能/工作区文件/说明.md
# term:自我进化  doc:doc/功能/自我进化/说明.md
from __future__ import annotations

import re
from pathlib import Path

SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
)
SKIP_REL_PREFIXES: tuple[str, ...] = ("data/versions",)

# 自我进化硬禁改（仅约束本产品智能体经 WorkspaceFs 的写入；Cursor/人工不受限）
# 尺子：弄坏则无法从坏进化自救 → 回退面 + 保活外层 + 闸门自身 + 评测真源
_EVOLUTION_FORBID_PREFIXES: tuple[str, ...] = ("fawai_avatar/versioning",)
_EVOLUTION_FORBID_EXACT: frozenset[str] = frozenset(
    {
        "fawai_avatar/runtime/releases.py",
        "fawai_avatar/runtime/keeper.py",
        "fawai_avatar/runtime/code_watch.py",
        "fawai_avatar/runtime/port_guard.py",
        "fawai_avatar/workspace/paths.py",
    }
)
_VERSION_ARCHIVE_RE = re.compile(r"^doc/版本/v[^/]+(/|$)")


class WorkspacePathError(ValueError):
    """Path outside work root or blocked by policy."""


def normalize_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve()
    if not root.is_dir():
        raise WorkspacePathError(f"work root is not a directory: {root}")
    return root


def resolve_under_root(work_root: Path, user_path: str) -> Path:
    """Resolve user_path relative to work_root; reject escape."""
    root = work_root.resolve()
    raw = (user_path or ".").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if raw in ("", "."):
        return root
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(f"path escapes work root: {user_path}") from exc
    return candidate


def rel_posix(path: Path, work_root: Path) -> str:
    return path.resolve().relative_to(work_root.resolve()).as_posix()


def is_skipped_rel(rel: str) -> bool:
    norm = rel.replace("\\", "/").strip("/")
    if not norm:
        return False
    for prefix in SKIP_REL_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    parts = norm.split("/")
    return any(part in SKIP_DIR_NAMES for part in parts)


def should_prune_dir(rel_dir: str, dirname: str) -> bool:
    if dirname in SKIP_DIR_NAMES:
        return True
    child = f"{rel_dir}/{dirname}".strip("/") if rel_dir else dirname
    child_norm = child.replace("\\", "/")
    for prefix in SKIP_REL_PREFIXES:
        if child_norm == prefix or child_norm.startswith(prefix + "/"):
            return True
    return False


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/").strip("/")


def is_evolution_core_rel(rel: str) -> bool:
    """回退/保活/闸门等最少核路径（全拒写）。"""
    norm = _norm_rel(rel)
    if not norm:
        return False
    if norm in _EVOLUTION_FORBID_EXACT:
        return True
    for prefix in _EVOLUTION_FORBID_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "/"):
            return True
    return False


def is_versioning_impl_rel(rel: str) -> bool:
    """兼容旧名：仅版本管理实现前缀。"""
    norm = _norm_rel(rel)
    return norm == "fawai_avatar/versioning" or norm.startswith(
        "fawai_avatar/versioning/"
    )


def is_version_archive_rel(rel: str) -> bool:
    return bool(_VERSION_ARCHIVE_RE.match(_norm_rel(rel)))


def assert_writable_rel(
    rel: str,
    *,
    work_root: Path | None = None,
    allow_env: bool = False,
) -> None:
    """拒写：.env、进化最少核、已存在的 doc/版本/v* 归档。仅智能体写路径调用。"""
    if not allow_env:
        name = Path(rel.replace("\\", "/")).name
        if name == ".env":
            raise WorkspacePathError("writing .env is not allowed by default")
    norm = _norm_rel(rel)
    if is_evolution_core_rel(norm):
        raise WorkspacePathError(
            f"evolution forbid: cannot modify recovery/keeper core path: {norm}"
        )
    if is_version_archive_rel(norm):
        if work_root is None:
            raise WorkspacePathError(
                "evolution forbid: cannot modify archived version eval without work_root check"
            )
        target = (work_root / norm).resolve()
        if target.exists():
            raise WorkspacePathError(
                f"evolution forbid: cannot modify existing archived version path: {norm}"
            )
