# term:代码索引  tool:code_symbols/code_map/code_index_refresh  doc:doc/功能/代码索引/说明.md
"""工作区代码结构索引：多语言符号 + 落盘复用（Python ast；其余启发式）。"""

from __future__ import annotations

import ast
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from fawai_avatar.workspace.paths import (
    WorkspacePathError,
    is_skipped_rel,
    normalize_work_root,
    rel_posix,
    resolve_under_root,
    should_prune_dir,
)

_PY_SUFFIXES = frozenset({".py"})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_CSS_SUFFIXES = frozenset({".css"})
_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".m",
        ".mm",
        ".rb",
        ".php",
        ".swift",
        ".cs",
        ".html",
        ".htm",
        ".css",
    }
)
_KIND_ORDER = {
    "module": 0,
    "class": 1,
    "struct": 1,
    "interface": 1,
    "enum": 1,
    "type": 1,
    "id": 1,
    "selector": 1,
    "media": 1,
    "function": 2,
    "method": 3,
    "async_function": 2,
    "async_method": 3,
}

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_IDENT_DOLLAR = r"[A-Za-z_$][A-Za-z0-9_$]*"

# (kind, name_group, signature_hint_group or 0)
_HEURISTIC_PATTERNS: dict[str, list[tuple[str, re.Pattern[str], int]]] = {
    "js": [
        ("class", re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?class\s+({_IDENT_DOLLAR})"), 1),
        (
            "function",
            re.compile(
                rf"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*({_IDENT_DOLLAR})\s*(\([^)]*\))?"
            ),
            2,
        ),
        (
            "function",
            re.compile(
                rf"^\s*(?:export\s+)?(?:const|let|var)\s+({_IDENT_DOLLAR})\s*=\s*"
                rf"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][A-Za-z0-9_$]*\s*=>)"
            ),
            1,
        ),
        (
            "method",
            re.compile(
                rf"^\s+(?:async\s+)?(?:static\s+)?(?:get\s+|set\s+)?({_IDENT_DOLLAR})\s*(\([^)]*\))\s*\{{"
            ),
            2,
        ),
    ],
    "go": [
        ("type", re.compile(rf"^\s*type\s+({_IDENT})\s+(?:struct|interface)\b"), 1),
        (
            "function",
            re.compile(rf"^\s*func\s+(?:\([^)]+\)\s+)?({_IDENT})\s*(\([^)]*\))"),
            2,
        ),
    ],
    "rs": [
        ("struct", re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+({_IDENT})"), 1),
        ("enum", re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+({_IDENT})"), 1),
        ("trait", re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+({_IDENT})"), 1),
        (
            "function",
            re.compile(
                rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+({_IDENT})\s*"
                rf"(?:<[^>]*>\s*)?(\([^)]*\))"
            ),
            2,
        ),
    ],
    "java": [
        (
            "class",
            re.compile(
                rf"^\s*(?:public\s+|protected\s+|private\s+)?(?:abstract\s+|final\s+|static\s+)*"
                rf"(?:class|interface|enum|record)\s+({_IDENT})"
            ),
            1,
        ),
        (
            "method",
            re.compile(
                rf"^\s+(?:public\s+|protected\s+|private\s+)?(?:static\s+|final\s+|abstract\s+|synchronized\s+)*"
                rf"(?:[\w.<>,\[\]?]+\s+)+({_IDENT})\s*(\([^;]*\))\s*(?:throws\s+[^{{]+)?\s*\{{"
            ),
            2,
        ),
    ],
    "c": [
        (
            "function",
            re.compile(
                rf"^\s*(?:static\s+|inline\s+|extern\s+)*(?:[\w*]+\s+)+\*?\s*({_IDENT})\s*(\([^;]*\))\s*\{{"
            ),
            2,
        ),
        ("struct", re.compile(rf"^\s*(?:typedef\s+)?struct\s+({_IDENT})\b"), 1),
        ("class", re.compile(rf"^\s*(?:template\s*<[^>]*>\s*)?class\s+({_IDENT})\b"), 1),
    ],
    "rb": [
        ("class", re.compile(rf"^\s*class\s+({_IDENT})"), 1),
        ("module", re.compile(rf"^\s*module\s+({_IDENT})"), 1),
        ("method", re.compile(rf"^\s*def\s+(?:self\.)?({_IDENT})"), 1),
    ],
    "php": [
        ("class", re.compile(rf"^\s*(?:abstract\s+|final\s+)?class\s+({_IDENT})"), 1),
        ("interface", re.compile(rf"^\s*interface\s+({_IDENT})"), 1),
        (
            "function",
            re.compile(rf"^\s*(?:public\s+|protected\s+|private\s+|static\s+)*function\s+({_IDENT})\s*(\([^)]*\))?"),
            2,
        ),
    ],
    "swift": [
        ("class", re.compile(rf"^\s*(?:public\s+|open\s+|internal\s+|fileprivate\s+|private\s+)?(?:final\s+)?class\s+({_IDENT})"), 1),
        ("struct", re.compile(rf"^\s*(?:public\s+|internal\s+|fileprivate\s+|private\s+)?struct\s+({_IDENT})"), 1),
        ("enum", re.compile(rf"^\s*(?:public\s+|internal\s+|fileprivate\s+|private\s+)?enum\s+({_IDENT})"), 1),
        (
            "function",
            re.compile(
                rf"^\s*(?:public\s+|open\s+|internal\s+|fileprivate\s+|private\s+|static\s+|class\s+)*"
                rf"func\s+({_IDENT})\s*(\([^)]*\))?"
            ),
            2,
        ),
    ],
    "cs": [
        (
            "class",
            re.compile(
                rf"^\s*(?:public\s+|internal\s+|private\s+|protected\s+)?(?:static\s+|abstract\s+|sealed\s+|partial\s+)*"
                rf"(?:class|interface|struct|enum|record)\s+({_IDENT})"
            ),
            1,
        ),
        (
            "method",
            re.compile(
                rf"^\s+(?:public\s+|private\s+|protected\s+|internal\s+)?(?:static\s+|virtual\s+|override\s+|async\s+)*"
                rf"[\w.<>,\[\]?]+\s+({_IDENT})\s*(\([^;]*\))\s*\{{"
            ),
            2,
        ),
    ],
}

_SUFFIX_LANG = {
    ".js": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".vue": "js",
    ".go": "go",
    ".rs": "rs",
    ".java": "java",
    ".kt": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "c",
    ".cpp": "c",
    ".hpp": "c",
    ".m": "c",
    ".mm": "c",
    ".rb": "rb",
    ".php": "php",
    ".swift": "swift",
    ".cs": "cs",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
}

_VUE_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_RE = re.compile(
    r"<style\b[^>]*>(.*?)</style>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_ID_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HTML_CLASS_RE = re.compile(r"""\bclass\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_CSS_MEDIA_RE = re.compile(r"^\s*(@media\b[^{]*)\{", re.IGNORECASE)
_CSS_SELECTOR_RE = re.compile(r"^\s*([^{}@/][^{]*?)\s*\{\s*(?:$|/\*)")
_CLASS_TOKEN_RE = re.compile(r"[A-Za-z_][\w-]*")


def _kind_matches(hit_kind: str, kind_filter: str) -> bool:
    if not kind_filter:
        return True
    if hit_kind == kind_filter:
        return True
    if kind_filter == "function" and hit_kind in ("function", "async_function"):
        return True
    if kind_filter == "method" and hit_kind in ("method", "async_method"):
        return True
    if kind_filter == "class" and hit_kind in ("class", "struct", "interface", "enum", "type", "trait", "module"):
        return True
    if kind_filter in {"css", "selector"} and hit_kind in ("selector", "media", "class", "id"):
        return True
    return False


@dataclass(frozen=True)
class SymbolHit:
    path: str
    name: str
    kind: str
    line: int
    signature: str
    parent: str = ""
    engine: str = "python-ast"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def map_line(self) -> str:
        if self.parent:
            return f"{self.path}:{self.line} {self.kind} {self.parent}.{self.name}{self.signature}"
        return f"{self.path}:{self.line} {self.kind} {self.name}{self.signature}"


@dataclass
class _FileRecord:
    path: str
    mtime_ns: int
    size: int
    engine: str
    symbols: list[SymbolHit] = field(default_factory=list)


class CodeIndex:
    """Structural index scoped to a work root; optional disk cache under data_dir."""

    def __init__(
        self,
        work_root: str | Path,
        *,
        data_dir: str | Path | None = None,
    ) -> None:
        self._root = normalize_work_root(work_root)
        self._data_dir = Path(data_dir).expanduser() if data_dir else None
        self._files: dict[str, _FileRecord] = {}
        self._loaded = False
        self._dirty = False

    @property
    def work_root(self) -> Path:
        return self._root

    @property
    def store_dir(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "code_index"

    def symbols(
        self,
        path: str = ".",
        *,
        query: str = "",
        kind: str = "",
        head_limit: int = 100,
    ) -> list[dict[str, Any]]:
        if head_limit < 1:
            raise ValueError("head_limit must be >= 1")
        self.ensure_index(path)
        hits = list(self._iter_filtered(path, query=query, kind=kind, head_limit=head_limit))
        return [h.to_dict() for h in hits]

    def map(
        self,
        path: str = ".",
        *,
        query: str = "",
        kind: str = "",
        head_limit: int = 80,
    ) -> dict[str, Any]:
        if head_limit < 1:
            raise ValueError("head_limit must be >= 1")
        self.ensure_index(path)
        pool = list(self._iter_filtered(path, query=query, kind=kind, head_limit=max(head_limit * 4, 200)))
        pool.sort(key=lambda h: (h.path, _KIND_ORDER.get(h.kind, 9), h.line, h.name))
        selected = pool[:head_limit]
        lines = [h.map_line() for h in selected]
        engines = sorted({h.engine for h in selected}) or ["none"]
        engine = engines[0] if len(engines) == 1 else "mixed"
        return {
            "path": (path or ".").strip() or ".",
            "lines": lines,
            "count": len(lines),
            "truncated": len(pool) > len(selected),
            "engine": engine,
            "engines": engines,
        }

    def ensure_index(self, path: str = ".") -> dict[str, Any]:
        """Lazy build / incremental refresh for path; persist when data_dir set."""
        return self._sync(path, force=False)

    def refresh(self, path: str = ".") -> dict[str, Any]:
        """Force re-extract under path."""
        return self._sync(path, force=True)

    def _sync(self, path: str, *, force: bool) -> dict[str, Any]:
        self._load_store()
        base = resolve_under_root(self._root, path)
        rel = rel_posix(base, self._root) if base != self._root else "."
        if rel != "." and is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        if base.is_file():
            files = [base] if base.suffix.lower() in _SOURCE_SUFFIXES else []
        elif base.is_dir():
            files = self._iter_source_files(base)
        else:
            raise FileNotFoundError(rel if rel != "." else path)

        scanned = 0
        updated = 0
        for file_path in files:
            scanned += 1
            if self._index_one(file_path, force=force):
                updated += 1

        # Drop records under scope that no longer exist
        scope_prefix = "" if rel == "." else rel.rstrip("/") + "/"
        alive = {rel_posix(f, self._root) for f in files}
        removed = 0
        for key in list(self._files.keys()):
            under = rel == "." or key == rel or key.startswith(scope_prefix)
            if under and key not in alive:
                del self._files[key]
                removed += 1
                self._dirty = True

        if self._dirty:
            self._save_store()

        return {
            "path": rel if rel != "." else ".",
            "scanned": scanned,
            "updated": updated,
            "removed": removed,
            "files": len(self._files),
            "symbols": sum(len(r.symbols) for r in self._files.values()),
            "store": str(self.store_dir) if self.store_dir else "",
        }

    def _index_one(self, file_path: Path, *, force: bool) -> bool:
        rel = rel_posix(file_path, self._root)
        if is_skipped_rel(rel):
            return False
        try:
            st = file_path.stat()
        except OSError:
            return False
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
        prev = self._files.get(rel)
        if (
            not force
            and prev is not None
            and prev.mtime_ns == mtime_ns
            and prev.size == size
        ):
            return False
        hits, engine = self._extract_file(file_path, rel)
        self._files[rel] = _FileRecord(
            path=rel,
            mtime_ns=mtime_ns,
            size=size,
            engine=engine,
            symbols=hits,
        )
        self._dirty = True
        return True

    def _iter_filtered(
        self,
        path: str,
        *,
        query: str,
        kind: str,
        head_limit: int,
    ) -> Iterable[SymbolHit]:
        q = (query or "").strip().lower()
        kind_filter = (kind or "").strip().lower()
        count = 0
        for hit in self._iter_symbols_from_store(path):
            if not _kind_matches(hit.kind, kind_filter):
                continue
            if q:
                blob = f"{hit.name} {hit.parent} {hit.path} {hit.signature}".lower()
                if q not in blob:
                    continue
            yield hit
            count += 1
            if count >= head_limit:
                return

    def _iter_symbols_from_store(self, path: str) -> Iterable[SymbolHit]:
        base = resolve_under_root(self._root, path)
        rel = rel_posix(base, self._root) if base != self._root else "."
        if rel != "." and is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        if base.is_file():
            rec = self._files.get(rel)
            if rec:
                yield from rec.symbols
            return
        if not base.is_dir():
            raise FileNotFoundError(rel if rel != "." else path)
        prefix = "" if rel == "." else rel.rstrip("/") + "/"
        for key in sorted(self._files.keys()):
            if rel == "." or key == rel or key.startswith(prefix):
                yield from self._files[key].symbols

    def _iter_source_files(self, base: Path) -> list[Path]:
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            rel_dir = rel_posix(Path(dirpath), self._root)
            if rel_dir != "." and is_skipped_rel(rel_dir):
                dirnames.clear()
                continue
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if not should_prune_dir("" if rel_dir == "." else rel_dir, d)
            ]
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                files.append(Path(dirpath) / name)
        return files

    def _extract_file(self, file_path: Path, rel: str) -> tuple[list[SymbolHit], str]:
        suffix = file_path.suffix.lower()
        if suffix not in _SOURCE_SUFFIXES:
            return [], "skip"
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], "unreadable"
        if suffix in _PY_SUFFIXES:
            return self._extract_python(rel, source), "python-ast"
        if suffix in _HTML_SUFFIXES:
            return self._extract_html(rel, source), "heuristic-html"
        if suffix in _CSS_SUFFIXES:
            return self._extract_css(rel, source), "heuristic-css"
        lang = _SUFFIX_LANG.get(suffix, "generic")
        if suffix == ".vue":
            chunks: list[tuple[str, int]] = []
            for m in _VUE_SCRIPT_RE.finditer(source):
                block = m.group(1) or ""
                start_line = source[: m.start(1)].count("\n") + 1
                chunks.append((block, start_line))
            if not chunks:
                return [], f"heuristic-{lang}"
            hits: list[SymbolHit] = []
            for block, start_line in chunks:
                hits.extend(self._extract_heuristic(rel, block, lang, line_offset=start_line - 1))
            return hits, f"heuristic-{lang}"
        return self._extract_heuristic(rel, source, lang), f"heuristic-{lang}"

    def _extract_html(self, rel: str, source: str) -> list[SymbolHit]:
        hits: list[SymbolHit] = []
        seen: set[tuple[str, str]] = set()
        for m in _HTML_ID_RE.finditer(source):
            name = (m.group(1) or "").strip()
            if not name or len(name) < 2:
                continue
            key = ("id", name)
            if key in seen:
                continue
            seen.add(key)
            line = source[: m.start()].count("\n") + 1
            hits.append(
                SymbolHit(
                    path=rel,
                    name=name,
                    kind="id",
                    line=line,
                    signature="",
                    parent="",
                    engine="heuristic-html",
                )
            )
        for m in _HTML_CLASS_RE.finditer(source):
            raw = m.group(1) or ""
            line = source[: m.start()].count("\n") + 1
            for token in _CLASS_TOKEN_RE.findall(raw):
                if len(token) < 2:
                    continue
                key = ("class", token)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    SymbolHit(
                        path=rel,
                        name=token,
                        kind="class",
                        line=line,
                        signature="",
                        parent="",
                        engine="heuristic-html",
                    )
                )
        for m in _STYLE_RE.finditer(source):
            block = m.group(1) or ""
            start_line = source[: m.start(1)].count("\n") + 1
            hits.extend(self._extract_css(rel, block, line_offset=start_line - 1))
        for m in _VUE_SCRIPT_RE.finditer(source):
            block = m.group(1) or ""
            if not block.strip():
                continue
            start_line = source[: m.start(1)].count("\n") + 1
            hits.extend(
                self._extract_heuristic(rel, block, "js", line_offset=start_line - 1)
            )
        return hits

    def _extract_css(
        self,
        rel: str,
        source: str,
        *,
        line_offset: int = 0,
    ) -> list[SymbolHit]:
        hits: list[SymbolHit] = []
        seen: set[tuple[str, str]] = set()
        for i, line in enumerate(source.splitlines(), start=1):
            lineno = i + line_offset
            media = _CSS_MEDIA_RE.match(line)
            if media:
                name = " ".join((media.group(1) or "").split())
                if len(name) > 80:
                    name = name[:77] + "..."
                key = ("media", name)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    SymbolHit(
                        path=rel,
                        name=name,
                        kind="media",
                        line=lineno,
                        signature="",
                        parent="",
                        engine="heuristic-css",
                    )
                )
                continue
            sel = _CSS_SELECTOR_RE.match(line)
            if not sel:
                continue
            name = " ".join((sel.group(1) or "").split()).rstrip(",")
            if not name or name.startswith("/*") or len(name) < 2:
                continue
            if len(name) > 80:
                name = name[:77] + "..."
            key = ("selector", name)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                SymbolHit(
                    path=rel,
                    name=name,
                    kind="selector",
                    line=lineno,
                    signature="",
                    parent="",
                    engine="heuristic-css",
                )
            )
        return hits

    def _extract_python(self, rel: str, source: str) -> list[SymbolHit]:
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            return []
        hits: list[SymbolHit] = []
        for node in tree.body:
            hits.extend(self._from_module_stmt(rel, node, parent=""))
        return hits

    def _extract_heuristic(
        self,
        rel: str,
        source: str,
        lang: str,
        *,
        line_offset: int = 0,
    ) -> list[SymbolHit]:
        patterns = _HEURISTIC_PATTERNS.get(lang) or []
        if not patterns:
            return []
        hits: list[SymbolHit] = []
        engine = f"heuristic-{lang}"
        seen: set[tuple[str, str, int]] = set()
        for i, line in enumerate(source.splitlines(), start=1):
            lineno = i + line_offset
            for kind, cre, sig_group in patterns:
                m = cre.match(line)
                if not m:
                    continue
                name = m.group(1)
                if not name or name in {
                    "if",
                    "for",
                    "while",
                    "switch",
                    "catch",
                    "return",
                    "new",
                    "typeof",
                    "constructor",
                }:
                    continue
                sig = "()"
                if sig_group > 1 and m.lastindex and m.lastindex >= sig_group:
                    raw = m.group(sig_group)
                    if raw:
                        sig = raw if raw.startswith("(") else f"({raw})"
                key = (name, kind, lineno)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    SymbolHit(
                        path=rel,
                        name=name,
                        kind=kind,
                        line=lineno,
                        signature=sig,
                        parent="",
                        engine=engine,
                    )
                )
                break
        return hits

    def _from_module_stmt(self, rel: str, node: ast.AST, parent: str) -> list[SymbolHit]:
        out: list[SymbolHit] = []
        if isinstance(node, ast.ClassDef):
            out.append(
                SymbolHit(
                    path=rel,
                    name=node.name,
                    kind="class",
                    line=getattr(node, "lineno", 1),
                    signature=self._class_signature(node),
                    parent=parent,
                    engine="python-ast",
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "async_method" if isinstance(child, ast.AsyncFunctionDef) else "method"
                    out.append(
                        SymbolHit(
                            path=rel,
                            name=child.name,
                            kind=kind,
                            line=getattr(child, "lineno", 1),
                            signature=self._func_signature(child),
                            parent=node.name,
                            engine="python-ast",
                        )
                    )
                elif isinstance(child, ast.ClassDef):
                    out.extend(self._from_module_stmt(rel, child, parent=node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            out.append(
                SymbolHit(
                    path=rel,
                    name=node.name,
                    kind=kind,
                    line=getattr(node, "lineno", 1),
                    signature=self._func_signature(node),
                    parent=parent,
                    engine="python-ast",
                )
            )
        return out

    @staticmethod
    def _format_arg(a: ast.arg) -> str:
        if a.annotation is None:
            return a.arg
        try:
            return f"{a.arg}: {ast.unparse(a.annotation)}"
        except Exception:
            return a.arg

    @classmethod
    def _func_signature(cls, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = node.args
        parts: list[str] = []
        for a in args.posonlyargs:
            parts.append(cls._format_arg(a))
        if args.posonlyargs:
            parts.append("/")
        for a in args.args:
            parts.append(cls._format_arg(a))
        if args.vararg is not None:
            parts.append("*" + cls._format_arg(args.vararg))
        elif args.kwonlyargs:
            parts.append("*")
        for a in args.kwonlyargs:
            parts.append(cls._format_arg(a))
        if args.kwarg is not None:
            parts.append("**" + cls._format_arg(args.kwarg))
        ret = ""
        if node.returns is not None:
            try:
                ret = " -> " + ast.unparse(node.returns)
            except Exception:
                ret = ""
        return "(" + ", ".join(parts) + ")" + ret

    @staticmethod
    def _class_signature(node: ast.ClassDef) -> str:
        if not node.bases:
            return "()"
        names: list[str] = []
        for base in node.bases:
            try:
                names.append(ast.unparse(base))
            except Exception:
                names.append("?")
        return "(" + ", ".join(names) + ")"

    def _meta_path(self) -> Path | None:
        d = self.store_dir
        return None if d is None else d / "meta.json"

    def _symbols_path(self) -> Path | None:
        d = self.store_dir
        return None if d is None else d / "symbols.jsonl"

    def _load_store(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        meta_path = self._meta_path()
        symbols_path = self._symbols_path()
        if meta_path is None or symbols_path is None:
            return
        if not meta_path.is_file() or not symbols_path.is_file():
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        root = str(meta.get("work_root") or "")
        if root and Path(root).resolve() != self._root.resolve():
            return
        files_meta = meta.get("files") or {}
        by_path: dict[str, _FileRecord] = {}
        try:
            with symbols_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    path = str(row.get("path") or "")
                    if not path:
                        continue
                    fm = files_meta.get(path) or {}
                    syms: list[SymbolHit] = []
                    for s in row.get("symbols") or []:
                        try:
                            syms.append(
                                SymbolHit(
                                    path=str(s.get("path") or path),
                                    name=str(s.get("name") or ""),
                                    kind=str(s.get("kind") or "function"),
                                    line=int(s.get("line") or 1),
                                    signature=str(s.get("signature") or "()"),
                                    parent=str(s.get("parent") or ""),
                                    engine=str(s.get("engine") or fm.get("engine") or "unknown"),
                                )
                            )
                        except (TypeError, ValueError):
                            continue
                    by_path[path] = _FileRecord(
                        path=path,
                        mtime_ns=int(fm.get("mtime_ns") or 0),
                        size=int(fm.get("size") or 0),
                        engine=str(fm.get("engine") or row.get("engine") or "unknown"),
                        symbols=syms,
                    )
        except OSError:
            return
        self._files = by_path
        self._dirty = False

    def _save_store(self) -> None:
        store = self.store_dir
        if store is None:
            self._dirty = False
            return
        store.mkdir(parents=True, exist_ok=True)
        meta = {
            "work_root": str(self._root),
            "built_at": time.time(),
            "file_count": len(self._files),
            "symbol_count": sum(len(r.symbols) for r in self._files.values()),
            "files": {
                path: {
                    "mtime_ns": rec.mtime_ns,
                    "size": rec.size,
                    "engine": rec.engine,
                    "symbol_count": len(rec.symbols),
                }
                for path, rec in sorted(self._files.items())
            },
        }
        meta_path = self._meta_path()
        symbols_path = self._symbols_path()
        assert meta_path is not None and symbols_path is not None
        tmp_meta = meta_path.with_suffix(".tmp")
        tmp_sym = symbols_path.with_suffix(".tmp")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        with tmp_sym.open("w", encoding="utf-8") as fh:
            for path, rec in sorted(self._files.items()):
                row = {
                    "path": path,
                    "engine": rec.engine,
                    "symbols": [h.to_dict() for h in rec.symbols],
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp_meta.replace(meta_path)
        tmp_sym.replace(symbols_path)
        self._dirty = False


if __name__ == "__main__":
    import tempfile

    WORK_ROOT = tempfile.mkdtemp(prefix="fawai-code-index-")
    sample = Path(WORK_ROOT) / "pkg" / "mod.py"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(
        "class Foo:\n    def bar(self, x: int) -> str:\n        return str(x)\n\ndef top(a):\n    pass\n",
        encoding="utf-8",
    )
    ts = Path(WORK_ROOT) / "pkg" / "util.ts"
    ts.write_text(
        "export class Widget {}\nexport function helper(x: number): number {\n  return x\n}\n",
        encoding="utf-8",
    )
    idx = CodeIndex(WORK_ROOT, data_dir=Path(WORK_ROOT) / "_data")
    print(json.dumps(idx.symbols("pkg", query="bar"), ensure_ascii=False, indent=2))
    print(json.dumps(idx.symbols("pkg", query="helper"), ensure_ascii=False, indent=2))
    print(json.dumps(idx.map("."), ensure_ascii=False, indent=2))
    print(json.dumps(idx.refresh("."), ensure_ascii=False, indent=2))
