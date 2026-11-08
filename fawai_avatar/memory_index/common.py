"""Shared walk / text filters for memory indexes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

from fawai_avatar.workspace.fs import SKIP_DIR_NAMES_OUTSIDE
from fawai_avatar.workspace.paths import SKIP_DIR_NAMES, should_prune_dir

TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".css",
        ".scss",
        ".html",
        ".htm",
        ".xml",
        ".svg",
        ".csv",
        ".tsv",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".cs",
        ".r",
        ".m",
        ".mm",
        ".env",
        ".gitignore",
        ".dockerignore",
        ".mdc",
        ".rst",
        ".tex",
    }
)

MAX_FILE_BYTES = 1_500_000  # skip huge files for content FTS
MAX_INDEX_FILES = 200_000


def parse_roots(raw: str) -> list[Path]:
    out: list[Path] = []
    text = (raw or "").strip()
    if not text:
        return out
    for part in text.replace(";", ":").split(":"):
        item = part.strip()
        if not item or item.lower() in {"work", ".", "work_root"}:
            continue
        try:
            p = Path(item).expanduser().resolve()
        except OSError:
            continue
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def root_too_broad(root: Path) -> str | None:
    try:
        resolved = root.resolve()
    except OSError:
        return None
    if resolved == Path("/"):
        return "索引根为 / 过宽；请在记忆配置中收窄 FTS / 文件名索引目录"
    # home alone with no further path is still huge on macOS
    home = Path.home().resolve()
    if resolved == home:
        return "索引根为用户主目录过宽；请收窄到具体项目目录"
    return None


# 工作区扫盘时跳过的目录名：索引缓存 / 运行态（即使不在 FAWAI_DATA_DIR 下也勿灌进正文索引）
_SKIP_INDEX_DIR_NAMES: frozenset[str] = frozenset(
    {
        "memory_index",
        "code_index",
        "__pycache__",
        ".venv",
        "node_modules",
        ".git",
    }
)


def should_skip_dir_name(name: str) -> bool:
    if name.startswith("._") or name in SKIP_DIR_NAMES or name in SKIP_DIR_NAMES_OUTSIDE:
        return True
    if name in _SKIP_INDEX_DIR_NAMES:
        return True
    if name.startswith(".") and name not in {".cursor", ".github", ".vscode"}:
        # keep common project dots; skip other hidden
        if name in {".env"}:
            return False
        return True
    return False


def _resolve_data_dir() -> Path | None:
    """分身数据盘：工作区文件索引不得再扫这里（防缓存/会话/观察二次入库）。"""
    try:
        from fawai_avatar.config import load_dotenv, _env

        load_dotenv()
        raw = _env("FAWAI_DATA_DIR") or "data"
        return Path(raw).expanduser().resolve()
    except Exception:
        try:
            return Path("data").expanduser().resolve()
        except OSError:
            return None


def _under_data_dir(path: Path, data_dir: Path | None) -> bool:
    if data_dir is None:
        return False
    try:
        path.resolve().relative_to(data_dir)
        return True
    except (ValueError, OSError):
        return False


def iter_files(roots: list[Path], *, max_files: int = MAX_INDEX_FILES) -> Iterator[Path]:
    seen: set[str] = set()
    count = 0
    data_dir = _resolve_data_dir()
    for root in roots:
        err = root_too_broad(root)
        if err:
            continue
        if not root.is_dir():
            continue
        # 根本身就是数据盘：工作区索引跳过（知识库另有 lore FTS）
        if data_dir is not None:
            try:
                if root.resolve() == data_dir or _under_data_dir(root, data_dir):
                    continue
            except OSError:
                pass
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            try:
                cur = Path(dirpath).resolve()
            except OSError:
                cur = Path(dirpath)
            if _under_data_dir(cur, data_dir):
                dirnames[:] = []
                continue
            try:
                rel_base = str(cur.relative_to(root.resolve() if root.exists() else root))
            except ValueError:
                rel_base = ""
            except OSError:
                rel_base = ""
            # prune in-place
            kept: list[str] = []
            for d in dirnames:
                if should_skip_dir_name(d) or should_prune_dir(rel_base, d):
                    continue
                child = cur / d
                if _under_data_dir(child, data_dir):
                    continue
                kept.append(d)
            dirnames[:] = kept
            for name in filenames:
                if name.startswith("._"):
                    continue
                path = Path(dirpath) / name
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen:
                    continue
                if _under_data_dir(Path(key), data_dir):
                    continue
                seen.add(key)
                yield path
                count += 1
                if count >= max_files:
                    return


def is_text_path(path: Path) -> bool:
    suf = path.suffix.lower()
    if suf in TEXT_SUFFIXES:
        return True
    # extensionless common docs
    name = path.name.lower()
    return name in {"readme", "makefile", "dockerfile", "license", "agents.md"}


def read_text_capped(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_size <= 0 or st.st_size > max_bytes:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


_CJK = re.compile(r"[\u4e00-\u9fff]")


def prepare_fts_text(*parts: str) -> str:
    text = " ".join(str(p or "") for p in parts)
    out: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            out.append(f" {ch} ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip().lower()


def build_match_query(query: str) -> str:
    from fawai_lore.fts import build_match_query as lore_match

    return lore_match(query)


def split_name_query_terms(query: str) -> list[str]:
    """文件名检索用词：空白分词；保留整段中文词（LIKE），不只拆成单字。"""
    import re

    q = (query or "").strip().lower()
    if not q:
        return []
    parts = re.findall(r"[a-z0-9_\.\-]+|[\u4e00-\u9fff]+", q, flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        tok = (p or "").strip().lower()
        if not tok or tok in seen:
            continue
        if tok.isascii() and len(tok) < 2 and not tok.isdigit():
            continue
        seen.add(tok)
        out.append(tok)
    return out[:16]


def build_name_match_query(query: str) -> str:
    """文件名 FTS：空白分隔的词之间 OR；词内中文仍按字 AND（与入库 prepare 对齐）。

    自然语言整句（「4090 服务器 ssh 配置」）若整句 AND，几乎不会命中任何文件名。
    """
    terms = split_name_query_terms(query)
    if not terms:
        return ""
    groups: list[str] = []
    for term in terms:
        g = build_match_query(term)
        if not g:
            continue
        groups.append(f"({g})" if " AND " in g else g)
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    return " OR ".join(groups[:12])
