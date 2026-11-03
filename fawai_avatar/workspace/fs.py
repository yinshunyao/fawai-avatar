# term:工作区文件  tool:fs_*  doc:doc/功能/工作区文件/说明.md
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.paths import (
    WorkspacePathError,
    assert_writable_rel,
    is_skipped_rel,
    normalize_work_root,
    rel_posix,
    resolve_under_root,
    should_prune_dir,
)

_BRACE_RE = re.compile(r"\{([^{}]+)\}")


def expand_glob_braces(pattern: str) -> list[str]:
    """展开一层或多层 `{a,b}`；无花括号则原样返回。"""
    text = (pattern or "").strip() or "*"
    m = _BRACE_RE.search(text)
    if not m:
        return [text]
    prefix = text[: m.start()]
    suffix = text[m.end() :]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(expand_glob_braces(f"{prefix}{alt.strip()}{suffix}"))
    # 去重且保序
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq or [text]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把常见 glob（含 `**`、`*`、`?`）编成整路径正则。"""
    text = (pattern or "*").replace("\\", "/").lstrip("./")
    i = 0
    parts: list[str] = ["^"]
    while i < len(text):
        ch = text[i]
        if text.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
            continue
        if text.startswith("**", i):
            parts.append(".*")
            i += 2
            continue
        if ch == "*":
            parts.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            parts.append("[^/]")
            i += 1
            continue
        parts.append(re.escape(ch))
        i += 1
    parts.append("$")
    return re.compile("".join(parts))


def path_matches_glob(rel: str, pattern: str) -> bool:
    """工作区相对路径是否匹配 glob（支持 `**` 与 `{a,b}`）。"""
    rel_n = (rel or "").replace("\\", "/").lstrip("./")
    for pat in expand_glob_braces(pattern):
        pat_n = pat.replace("\\", "/").lstrip("./")
        if glob_to_regex(pat_n).match(rel_n):
            return True
        # 仅文件名模式（无斜杠）也允许匹配 basename
        if "/" not in pat_n.rstrip("/") and glob_to_regex(pat_n).match(Path(rel_n).name):
            return True
    return False


def _first_int(*values: Any, default: int | None = None) -> int | None:
    """取第一个可解析为 int 的值；全空则返回 default。"""
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            return int(text)
        except (TypeError, ValueError):
            continue
    return default


def resolve_read_range(
    args: dict[str, Any] | None,
    *,
    default_limit: int = 200,
) -> tuple[int, int]:
    """解析 fs_read 行范围：正式名 offset/limit，兼容 start_line/end_line/line。

    行号均为 1-based；end_line 含该行。
    """
    raw = args or {}
    offset = _first_int(raw.get("offset"), raw.get("start_line"), raw.get("line"), default=1)
    assert offset is not None
    if offset < 1:
        raise ValueError("offset/start_line must be >= 1")

    limit = _first_int(raw.get("limit"), default=None)
    if limit is None:
        end_line = _first_int(raw.get("end_line"), default=None)
        if end_line is not None:
            if end_line < offset:
                raise ValueError("end_line must be >= start_line/offset")
            limit = end_line - offset + 1
        else:
            limit = default_limit
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return offset, limit


def coerce_patch_hunks(hunks: Any) -> list[dict[str, Any]] | None:
    """把模型常见的 hunks JSON 字符串洗成 list；无法解析则抛 ValueError（含收到的类型）。"""
    if hunks is None:
        return None
    if isinstance(hunks, list):
        return hunks
    if isinstance(hunks, str):
        text = hunks.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "fs_patch hunks 须为数组或可解析的 JSON 数组字符串；"
                f"收到 string 但 JSON 解析失败（{exc}）"
            ) from exc
        if parsed is None:
            return None
        if not isinstance(parsed, list):
            raise ValueError(
                "fs_patch hunks JSON 解析后须为数组，"
                f"收到 {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"fs_patch hunks 须为数组或 JSON 数组字符串，收到 {type(hunks).__name__}"
    )


def patch_anchor_quality_error(
    old: str | None,
    new: str | None,
    *,
    label: str = "old/new",
) -> str | None:
    """拒绝截断锚点：Unicode「…」或片段以 `...` 收尾（不拦源码中部合法 Python Ellipsis）。"""
    for side, text in (("old", old), ("new", new)):
        if text is None:
            continue
        sample = str(text)
        if "…" in sample:
            return (
                f"{label}: {side} 含省略号「…」，疑似截断臆造；"
                "请先 fs_read 核对原文后给出完整 exact 片段，禁止用 … 代替未读内容"
            )
        stripped = sample.rstrip()
        if stripped.endswith("..."):
            return (
                f"{label}: {side} 以 ... 结尾，疑似截断；"
                "请先 fs_read 后给出完整 exact old/new"
            )
    return None


def resolve_patch_ops(
    *,
    hunks: Any = None,
    old: str | None = None,
    new: str | None = None,
) -> dict[str, Any]:
    """统一解析 fs_patch 参数：coerce hunks + 质量门 → ops 列表。"""
    coerced = coerce_patch_hunks(hunks)
    ops: list[dict[str, str]] = []
    if coerced:
        for i, h in enumerate(coerced):
            if not isinstance(h, dict):
                raise ValueError(
                    f"fs_patch each hunk must be an object with old/new；"
                    f"hunk {i} 收到 {type(h).__name__}"
                )
            o = str(h.get("old") if h.get("old") is not None else "")
            n = str(h.get("new") if h.get("new") is not None else "")
            quality = patch_anchor_quality_error(o, n, label=f"hunk {i}")
            if quality:
                raise ValueError(quality)
            ops.append({"old": o, "new": n})
    elif old is not None and new is not None:
        quality = patch_anchor_quality_error(str(old), str(new), label="old/new")
        if quality:
            raise ValueError(quality)
        ops.append({"old": str(old), "new": str(new)})
    else:
        raise ValueError("fs_patch requires hunks or old+new")
    if not ops:
        raise ValueError("fs_patch requires at least one hunk")
    return {"ops": ops, "hunks": coerced}


class WorkspaceFs:
    """Background file tools scoped to a work root (no GUI)."""

    def __init__(self, work_root: str | Path) -> None:
        self._root = normalize_work_root(work_root)

    @property
    def work_root(self) -> Path:
        return self._root

    def glob(self, pattern: str, *, head_limit: int = 100) -> list[str]:
        pattern = (pattern or "*").strip()
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._root, topdown=True):
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
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if is_skipped_rel(rel):
                    continue
                if path_matches_glob(rel, pattern):
                    out.append(rel)
                    if len(out) >= head_limit:
                        return out
        return out

    def grep(
        self,
        pattern: str,
        *,
        path: str = ".",
        glob: str = "*",
        case_insensitive: bool = False,
        head_limit: int = 50,
        context_before: int = 0,
        context_after: int = 0,
    ) -> list[dict[str, Any]]:
        base = resolve_under_root(self._root, path)
        before = max(0, int(context_before))
        after = max(0, int(context_after))
        if shutil.which("rg"):
            return self._grep_ripgrep(
                pattern,
                base=base,
                glob=glob,
                case_insensitive=case_insensitive,
                head_limit=head_limit,
                context_before=before,
                context_after=after,
            )
        return self._grep_python(
            pattern,
            base=base,
            glob=glob,
            case_insensitive=case_insensitive,
            head_limit=head_limit,
            context_before=before,
            context_after=after,
        )

    def read_many(
        self,
        paths: list[str],
        *,
        offset: int = 1,
        limit: int = 120,
        max_files: int = 8,
    ) -> dict[str, Any]:
        files = [str(p).strip() for p in paths if str(p).strip()]
        if not files:
            raise ValueError("fs_read_many requires paths")
        if len(files) > max_files:
            raise ValueError(f"fs_read_many max_files={max_files}, got {len(files)}")
        items: list[dict[str, Any]] = []
        for p in files:
            items.append(self.read(p, offset=offset, limit=limit))
        return {"files": items, "count": len(items)}

    def read(
        self,
        path: str,
        *,
        offset: int = 1,
        limit: int = 200,
    ) -> dict[str, Any]:
        if offset < 1:
            raise ValueError("offset must be >= 1")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        file_path = resolve_under_root(self._root, path)
        rel = rel_posix(file_path, self._root)
        if is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        if not file_path.is_file():
            raise FileNotFoundError(rel)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        start = offset - 1
        chunk = all_lines[start : start + limit]
        truncated = start + len(chunk) < len(all_lines)
        lines = [{"n": offset + i, "text": line} for i, line in enumerate(chunk)]
        return {"path": rel, "lines": lines, "truncated": truncated}

    def write(
        self,
        path: str,
        content: str,
        *,
        create_dirs: bool = True,
        allow_env: bool = False,
    ) -> dict[str, Any]:
        file_path = resolve_under_root(self._root, path)
        rel = rel_posix(file_path, self._root)
        if is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        assert_writable_rel(rel, work_root=self._root, allow_env=allow_env)
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        file_path.write_bytes(data)
        return {"path": rel, "bytes": len(data)}

    def replace(
        self,
        path: str,
        old: str,
        new: str,
        *,
        count: int = 1,
        allow_env: bool = False,
    ) -> dict[str, Any]:
        if count < 1:
            raise ValueError("count must be >= 1")
        file_path = resolve_under_root(self._root, path)
        rel = rel_posix(file_path, self._root)
        if is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        assert_writable_rel(rel, work_root=self._root, allow_env=allow_env)
        if not file_path.is_file():
            raise FileNotFoundError(rel)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if old not in text:
            raise WorkspacePathError(
                f"substring not found in {rel}；请先 fs_read 核对 exact old，禁止臆造片段"
            )
        replacements = min(text.count(old), count)
        updated = text.replace(old, new, count)
        file_path.write_text(updated, encoding="utf-8")
        return {"path": rel, "replacements": replacements, "old_text": text, "new_text": updated}

    @staticmethod
    def apply_hunks_to_text(
        text: str,
        ops: list[dict[str, str]],
        *,
        rel: str,
        require_unique: bool = True,
    ) -> tuple[str, list[dict[str, Any]]]:
        updated = text
        applied: list[dict[str, Any]] = []
        for i, op in enumerate(ops):
            o, n = op["old"], op["new"]
            if not o:
                raise WorkspacePathError(f"hunk {i}: old must be non-empty")
            quality = patch_anchor_quality_error(o, n, label=f"hunk {i}")
            if quality:
                raise WorkspacePathError(quality)
            count = updated.count(o)
            if count == 0:
                raise WorkspacePathError(
                    f"hunk {i}: old not found in {rel}；请先 fs_read 核对原文后再 patch"
                )
            if require_unique and count > 1:
                raise WorkspacePathError(
                    f"hunk {i}: old matched {count} times in {rel}; require unique or split hunk"
                )
            updated = updated.replace(o, n, 1 if require_unique else count)
            applied.append(
                {"index": i, "matches": count, "replacements": 1 if require_unique else count}
            )
        if updated == text:
            raise WorkspacePathError(f"fs_patch produced no change in {rel}")
        return updated, applied

    def patch(
        self,
        path: str,
        hunks: list[dict[str, Any]] | None = None,
        *,
        old: str | None = None,
        new: str | None = None,
        allow_env: bool = False,
        require_unique: bool = True,
        write: bool = True,
    ) -> dict[str, Any]:
        """精准补丁：锚点 old→new；默认要求唯一匹配。write=False 仅预览。"""
        file_path = resolve_under_root(self._root, path)
        rel = rel_posix(file_path, self._root)
        if is_skipped_rel(rel):
            raise WorkspacePathError(f"path is skipped: {rel}")
        assert_writable_rel(rel, work_root=self._root, allow_env=allow_env)
        if not file_path.is_file():
            raise FileNotFoundError(rel)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            resolved = resolve_patch_ops(hunks=hunks, old=old, new=new)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        ops = resolved["ops"]
        updated, applied = self.apply_hunks_to_text(
            text, ops, rel=rel, require_unique=require_unique
        )
        if write:
            file_path.write_text(updated, encoding="utf-8")
        from fawai_avatar.workspace.pending_diff import unified_diff

        return {
            "path": rel,
            "hunks_applied": len(applied),
            "applied": applied,
            "old_text": text,
            "new_text": updated,
            "diff": unified_diff(rel, text, updated),
            "written": write,
        }

    def line_count(self, path: str) -> int:
        file_path = resolve_under_root(self._root, path)
        if not file_path.is_file():
            return 0
        return len(file_path.read_text(encoding="utf-8", errors="replace").splitlines())

    def _grep_ripgrep(
        self,
        pattern: str,
        *,
        base: Path,
        glob: str,
        case_insensitive: bool,
        head_limit: int,
        context_before: int = 0,
        context_after: int = 0,
    ) -> list[dict[str, Any]]:
        cmd: list[str] = ["rg", "-n", "--no-heading", "--color=never", "--json"]
        if case_insensitive:
            cmd.append("-i")
        if context_before:
            cmd.extend(["-B", str(context_before)])
        if context_after:
            cmd.extend(["-A", str(context_after)])
        for name in (".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"):
            cmd.extend(["--glob", f"!**/{name}/**"])
        cmd.extend(["--glob", "!**/data/versions/**"])
        if glob and glob != "*":
            cmd.extend(["--glob", glob])
        cmd.append(pattern)
        rel_base = rel_posix(base, self._root)
        cmd.append("." if rel_base == "." else rel_base)
        try:
            proc = subprocess.run(
                cmd,
                cwd=self._root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return self._grep_python(
                pattern,
                base=base,
                glob=glob,
                case_insensitive=case_insensitive,
                head_limit=head_limit,
                context_before=context_before,
                context_after=context_after,
            )
        if proc.returncode not in (0, 1):
            return self._grep_python(
                pattern,
                base=base,
                glob=glob,
                case_insensitive=case_insensitive,
                head_limit=head_limit,
                context_before=context_before,
                context_after=context_after,
            )
        return self._parse_rg_json(proc.stdout or "", head_limit=head_limit)

    def _parse_rg_json(self, stdout: str, *, head_limit: int) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        for raw in stdout.splitlines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            typ = obj.get("type")
            data = obj.get("data") or {}
            if typ == "match":
                if pending is not None:
                    hits.append(pending)
                    if len(hits) >= head_limit:
                        return hits
                path = str((data.get("path") or {}).get("text") or "")
                path = path.replace("\\", "/")
                if path.startswith("./"):
                    path = path[2:]
                if is_skipped_rel(path):
                    pending = None
                    continue
                line_no = int((data.get("line_number") or 0))
                text = str((data.get("lines") or {}).get("text") or "").rstrip("\n")
                pending = {
                    "path": path,
                    "line": line_no,
                    "text": text,
                    "before": [],
                    "after": [],
                }
            elif typ == "context" and pending is not None:
                line_no = int((data.get("line_number") or 0))
                text = str((data.get("lines") or {}).get("text") or "").rstrip("\n")
                if line_no < int(pending["line"]):
                    pending["before"].append({"line": line_no, "text": text})
                else:
                    pending["after"].append({"line": line_no, "text": text})
        if pending is not None and len(hits) < head_limit:
            hits.append(pending)
        return hits

    def _grep_python(
        self,
        pattern: str,
        *,
        base: Path,
        glob: str,
        case_insensitive: bool,
        head_limit: int,
        context_before: int = 0,
        context_after: int = 0,
    ) -> list[dict[str, Any]]:
        flags = re.IGNORECASE if case_insensitive else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid grep pattern: {pattern}") from exc
        hits: list[dict[str, Any]] = []
        for file_path in self._iter_files(base):
            if glob and glob != "*" and not Path(file_path.name).match(glob):
                continue
            rel = rel_posix(file_path, self._root)
            if is_skipped_rel(rel):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                if rx.search(line):
                    before = [
                        {"line": j, "text": lines[j - 1]}
                        for j in range(max(1, i - context_before), i)
                    ]
                    after = [
                        {"line": j, "text": lines[j - 1]}
                        for j in range(i + 1, min(len(lines), i + context_after) + 1)
                    ]
                    hits.append(
                        {
                            "path": rel,
                            "line": i,
                            "text": line,
                            "before": before,
                            "after": after,
                        }
                    )
                    if len(hits) >= head_limit:
                        return hits
        return hits

    def _iter_files(self, base: Path) -> list[Path]:
        if base.is_file():
            return [base]
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
                files.append(Path(dirpath) / name)
        return files


if __name__ == "__main__":
    import tempfile

    WORK_ROOT = tempfile.mkdtemp(prefix="fawai-ws-smoke-")  # 临时工作根
    fs = WorkspaceFs(WORK_ROOT)
    sample = Path(WORK_ROOT) / "hello.txt"
    sample.write_text("alpha\nbeta line\n", encoding="utf-8")
    print(json.dumps(fs.glob("*.txt"), ensure_ascii=False))
    print(json.dumps(fs.read("hello.txt"), ensure_ascii=False))
    print(json.dumps(fs.grep("beta"), ensure_ascii=False))
    print(json.dumps(fs.replace("hello.txt", "beta", "BETA"), ensure_ascii=False))
    print(json.dumps(fs.write("nested/out.md", "# ok"), ensure_ascii=False))
    try:
        fs.write(".env", "SECRET=1")
    except WorkspacePathError as exc:
        print({"blocked_env": str(exc)})
