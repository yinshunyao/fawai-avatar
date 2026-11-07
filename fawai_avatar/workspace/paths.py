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
        "fawai_avatar/runtime/reload_gate.py",
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


def parse_read_roots(raw: str | None, *, default: str = "/") -> list[Path]:
    """解析 FAWAI_READ_ROOTS：冒号/分号分隔；空或缺省 → default（默认根目录 `/`）。

    特殊值 ``work`` / ``.`` 表示不另开只读根（仅 work_root）。
    """
    text = (raw if raw is not None else default).strip()
    if not text or text.lower() in {"work", ".", "work_root"}:
        return []
    out: list[Path] = []
    for part in text.replace(";", ":").split(":"):
        item = part.strip()
        if not item:
            continue
        if item.lower() in {"work", ".", "work_root"}:
            continue
        try:
            p = Path(item).expanduser().resolve()
        except OSError:
            continue
        if p not in out:
            out.append(p)
    return out


def workspace_paths_prompt_block() -> str:
    """执行脑系统提示：当前工作区根与只读根（解析后的配置，不是默认假设）。"""
    from fawai_avatar.config import RuntimeConfig

    cfg = RuntimeConfig.from_env()
    try:
        work = str(Path(cfg.work_root).expanduser().resolve())
    except OSError:
        work = cfg.work_root
    roots = [r for r in cfg.read_roots if r]
    lines = [
        f"工作区根（可写）：{work}",
        "相对路径相对工作区根；写入不得越出工作区根。",
    ]
    if roots:
        lines.append("只读根：" + "、".join(roots))
        lines.append(
            "绝对路径的 fs_read / fs_grep / fs_glob 须落在工作区根或只读根之下；"
            "未列入的目录不要当可读根。"
        )
    else:
        lines.append("只读根：无（仅工作区）")
        lines.append("读也不得越出工作区根。")
    return "\n".join(lines)


def path_under_any(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


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


def resolve_for_read(
    work_root: Path,
    user_path: str,
    *,
    read_roots: list[Path] | None = None,
) -> Path:
    """读路径：相对路径仍相对 work_root；绝对/~ 路径须落在 work_root 或 FAWAI_READ_ROOTS。"""
    roots = list(read_roots or [])
    raw = (user_path or ".").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    expanded = Path(raw).expanduser()
    if expanded.is_absolute() or raw.startswith("~"):
        candidate = expanded.resolve()
        if path_under_any(candidate, [work_root.resolve(), *roots]):
            return candidate
        raise WorkspacePathError(
            f"path escapes work root and read roots: {user_path}"
        )
    try:
        return resolve_under_root(work_root, raw)
    except WorkspacePathError:
        # 相对路径只认 work_root，避免歧义挂到只读根
        raise


def display_path(path: Path, work_root: Path) -> str:
    """优先返回 work_root 相对路径；否则绝对 posix。"""
    resolved = path.resolve()
    try:
        return resolved.relative_to(work_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def format_file_missing_message(
    shown: str | Path | BaseException,
    *,
    work_root: Path | None = None,
) -> str:
    """LLM 可读的「文件不存在」拒因：与沙箱越界分码，并按父目录是否存在分流建议。

    - 父目录已在：路径闸已放行、仅缺该文件 → 禁止当成拼接错误空读重试
    - 父目录也不在 / 相对路径：才提示核对拼接或用 basename/hits 纠路径
    """
    if isinstance(shown, BaseException):
        fn = getattr(shown, "filename", None)
        raw = str(fn).strip() if fn else str(shown).strip()
    else:
        raw = str(shown or "").strip()
    # 剥常见前缀 / errno 外壳，避免叠字
    low = raw.lower()
    for prefix in (
        "not found (file missing):",
        "not found:",
    ):
        if low.startswith(prefix):
            raw = raw[len(prefix) :].strip(" :'\"")
            low = raw.lower()
            break
    if "no such file or directory:" in low:
        raw = raw.split(":", 1)[-1].strip(" '\"")
        low = raw.lower()
    elif low.startswith("[errno"):
        # [Errno 2] No such file or directory: 'path'
        parts = raw.split(":", 1)
        raw = parts[-1].strip(" '\"") if len(parts) > 1 else raw

    candidate: Path | None = None
    looks_abs = raw.startswith("/") or raw.startswith("~")
    try:
        if looks_abs:
            candidate = Path(raw).expanduser()
        elif work_root is not None and raw:
            candidate = Path(work_root).expanduser().resolve() / raw
    except OSError:
        candidate = None

    parent_exists = False
    parent_shown = ""
    if candidate is not None:
        try:
            parent = candidate.parent
            parent_exists = parent.is_dir()
            parent_shown = parent.as_posix()
        except OSError:
            parent_exists = False

    head = f"not found (file missing): {raw or shown}"
    if parent_exists:
        return (
            f"{head}；"
            f"≠ 沙箱越界/禁读：路径闸已放行，父目录存在（{parent_shown}），仅该文件名缺失。"
            "勿当作路径拼接错误反复纠 path；"
            "改查其它已证实落点（部署说明/配置文档/lore），"
            f"或 fs_glob path={parent_shown} 看目录实有哪些文件；禁止同 path 空读重试"
        )
    if looks_abs:
        return (
            f"{head}；"
            "≠ 沙箱越界/禁读：路径闸已放行，但路径上的目录也不存在。"
            "先核对目录层级是否拼错；"
            "再用 fs_glob path=<已知存在的上级> 或 fs_grep basename；"
            "禁止同 path 空读重试"
        )
    return (
        f"{head}；"
        "≠ 沙箱越界/禁读。相对路径相对 work_root；"
        "若来自文档链接请核对相对仓库/work_root 的拼接，"
        "或 fs_grep basename / 用 hits 绝对 path 再 fs_read；"
        "fs_glob 可传 path=只读根下目录"
    )


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
