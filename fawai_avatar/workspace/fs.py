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
    display_path,
    is_skipped_rel,
    normalize_work_root,
    parse_read_roots,
    path_under_any,
    rel_posix,
    resolve_for_read,
    resolve_under_root,
    should_prune_dir,
)

_BRACE_RE = re.compile(r"\{([^{}]+)\}")
SKIP_DIR_NAMES_OUTSIDE: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        "mybak",
        "other3rd",
        ".Trash",
        "Library",
        "Applications",
        "DerivedData",
    }
)


def _pattern_literal_len(pattern: str) -> int:
    lit = re.sub(r"[*?{}\[\]]", "", (pattern or "").replace("**", ""))
    return len(lit.strip("/."))


def _is_shallow_search_root(resolved: Path) -> bool:
    """整盘、用户目录层、家目录等浅根——禁止当检索根。"""
    try:
        if resolved == Path("/"):
            return True
        if resolved in {Path("/Users"), Path("/home"), Path("/Volumes")}:
            return True
        home = Path.home().resolve()
        if resolved == home:
            return True
    except OSError:
        return False
    return False


def pattern_has_escaped_alternation(pattern: str) -> bool:
    """pattern 是否含字面 ``\\|``（模型常把正则「或」写成转义竖线）。"""
    return "\\|" in (pattern or "")


def grep_empty_escaped_pipe_hint(pattern: str, *, hit_count: int) -> str | None:
    """0 命中且 pattern 含 ``\\|`` 时返回旁注，避免假归因「文件无匹配」。"""
    if int(hit_count or 0) > 0:
        return None
    if not pattern_has_escaped_alternation(pattern):
        return None
    return (
        "hint: pattern 含字面 \\| ；在正则里 | 表示「或」，写成 \\| 会变成匹配竖线本身。"
        "若本意多选一，请用 send|stop 或 button\\.send，勿用 send\\|stop。"
    )


def grep_pattern_tokens(pattern: str, *, max_tokens: int = 6) -> list[str]:
    """从 grep pattern 抽出可用于定目录的字面量片段。"""
    raw = (pattern or "").strip()
    if not raw:
        return []
    parts = re.split(r"[|/\s\\]+", raw)
    out: list[str] = []
    for part in parts:
        tok = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "", part)
        if len(tok) < 3:
            continue
        if tok.lower() in {"the", "and", "for", "with", "from", "path", "file"}:
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= max_tokens:
            break
    return out


def allow_broad_search(*, args: dict[str, Any] | None = None) -> bool:
    """显式放行宽根：args.allow_broad_root 或 FAWAI_ALLOW_BROAD_SEARCH=1。"""
    if args:
        raw = args.get("allow_broad_root")
        if isinstance(raw, str):
            if raw.strip().lower() in {"1", "true", "yes", "on"}:
                return True
        elif raw:
            return True
    return (os.environ.get("FAWAI_ALLOW_BROAD_SEARCH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def search_root_too_broad(
    base: Path,
    *,
    work_root: Path,
    kind: str,
    pattern: str = "",
    allow_broad: bool = False,
) -> str | None:
    """检索根过宽则返回拒因；单文件 path 不拒。

    - 浅根（``/``、``/Users``、家目录等）：grep/glob 均拒
    - 整棵 ``work_root``：``fs_grep`` 拒磁盘现扫；``fs_glob`` 在 pattern 字面量过短
      或仅扩展名花括号时拒
    """
    if allow_broad:
        return None
    try:
        resolved = base.resolve()
        root = work_root.resolve()
    except OSError:
        return None
    if resolved.is_file():
        return None
    kind_l = (kind or "").strip().lower()
    guide = (
        "索引未命中时禁止整仓磁盘扫：先 lore_search(work_name)/定目录，"
        "再对子目录分批 fs_grep；禁止 shell grep -r 顶替。"
        "确需宽扫时传 allow_broad_root=true。"
    )
    if _is_shallow_search_root(resolved):
        return (
            f"【检索根过宽】{kind_l or 'search'} path={resolved} 过浅，拒绝磁盘扫。"
            f"{guide}"
        )
    if resolved == root:
        if kind_l in {"grep", "fs_grep", "explore_search"}:
            return (
                "【检索根过宽】禁止对整棵工作区做磁盘正文扫（path=. 或 work_root）。"
                f"{guide}"
            )
        # glob：整根仅允许带足够字面量的定目录模式
        lit = _pattern_literal_len(pattern)
        ext_only = bool(
            re.fullmatch(
                r"(\*\*/)?\*\.(\{[^}]+\}|[a-zA-Z0-9]+)",
                (pattern or "").strip() or "*",
            )
        )
        if lit < 3 or ext_only or (pattern or "").strip() in {"*", "**", "**/*"}:
            return (
                "【检索根过宽】整工作区 fs_glob 须含明确文件名片段（≥3 字面字符），"
                "禁止 **/*.{yaml,yml,json} 一类扩扩展名扫整仓。"
                f"{guide}"
            )
    return None


def _glob_root_too_broad(base: Path, pattern: str) -> str | None:
    """兼容旧调用：仅 path=/ 且字面量过短。新逻辑见 search_root_too_broad。"""
    try:
        resolved = base.resolve()
    except OSError:
        return None
    if resolved != Path("/"):
        return None
    if _pattern_literal_len(pattern) < 2:
        return (
            "fs_glob path=/ 时 pattern 过宽；请把 path 收到具体目录，"
            "或让 pattern 含明确文件名片段（≥2 字面字符）"
        )
    return None


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
    模型常见 0-based 习惯：offset/start_line/line=0 视为文件头，收成 1。
    """
    raw = args or {}
    offset = _first_int(raw.get("offset"), raw.get("start_line"), raw.get("line"), default=1)
    assert offset is not None
    if offset == 0:
        # 与「从第 0 行起」的 0-based 习惯对齐，避免反复 FAIL 烧轮次
        offset = 1
    elif offset < 1:
        raise ValueError(
            "offset/start_line must be >= 1（1-based 起始行；"
            "文件头请省略 offset 或写 1，勿写负数）"
        )

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


_OLD_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")
_OLD_HINT_MAX_CHARS = 520
# 单条命中行正文硬顶：防超长行把一次 grep 打到数十万字符；观察侧默认不压缩
_HIT_LINE_MAX_CHARS = 500


def _clip_hit_text(text: str, *, max_chars: int = _HIT_LINE_MAX_CHARS) -> str:
    raw = text if isinstance(text, str) else str(text or "")
    if max_chars <= 0 or len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1] + "…"


def _old_search_tokens(old: str, *, limit: int = 8) -> list[str]:
    """从臆造/错位的 old 里抽出可检索记号（标识符与中文词）。"""
    seen: set[str] = set()
    out: list[str] = []
    for tok in _OLD_TOKEN_RE.findall(old or ""):
        if tok in seen:
            continue
        # 过泛停用词跳过
        if tok.lower() in {
            "return",
            "true",
            "false",
            "none",
            "self",
            "this",
            "from",
            "import",
            "def",
            "class",
            "status",
            "message",
        }:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def suggest_exact_old_window(file_text: str, old: str, *, context: int = 2) -> str | None:
    """当 old 不在文件中时，按记号命中行给出可复制的原文小窗（含 1-based 行号）。"""
    lines = (file_text or "").splitlines()
    if not lines:
        return None
    tokens = _old_search_tokens(old)
    if not tokens:
        return None
    scores: list[tuple[int, int]] = []
    lower_lines = [ln.lower() for ln in lines]
    tok_l = [t.lower() for t in tokens]
    for i, ln in enumerate(lower_lines):
        hit = sum(1 for t in tok_l if t in ln)
        if hit:
            scores.append((hit, i))
    if not scores:
        return None
    scores.sort(key=lambda x: (-x[0], x[1]))
    best_hit, best_i = scores[0]
    # 取同分命中的最小行号簇，扩成连续窗
    peers = [i for h, i in scores if h == best_hit][:12]
    lo = max(0, min(peers) - context)
    hi = min(len(lines), max(peers) + context + 1)
    # 窗不要过大
    if hi - lo > 16:
        lo = max(0, best_i - context)
        hi = min(len(lines), best_i + context + 1)
    numbered = [f"{idx + 1}:{lines[idx]}" for idx in range(lo, hi)]
    block = "\n".join(numbered)
    if len(block) > _OLD_HINT_MAX_CHARS:
        block = block[: _OLD_HINT_MAX_CHARS - 1] + "…"
    return (
        f"文件中与 old 记号最接近的原文小窗（须逐字作 old，勿改写）：\n{block}"
    )


def format_old_not_found_message(
    rel: str,
    old: str,
    file_text: str,
    *,
    label: str = "old",
) -> str:
    """old 未命中：钉死「未接地/臆造」拒因，并附可复制原文窗。"""
    head = (
        f"{label} not found in {rel}；"
        "【补丁锚点未接地】禁止按目标意图编造 old（仓内不存在的闸门/文案一律无效）；"
        "须从小窗 fs_read / 下方原文窗逐字复制后再 fs_patch/fs_replace"
    )
    hint = suggest_exact_old_window(file_text, old)
    if hint:
        return f"{head}\n{hint}"
    return (
        f"{head}；若无提示窗：先 fs_grep 钉 path:line，再对该行附近 fs_read 后复制 exact 片段"
    )


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
    """Background file tools：写限 work_root；读可扩 FAWAI_READ_ROOTS（默认 `/`）。"""

    def __init__(
        self,
        work_root: str | Path,
        *,
        read_roots: list[str | Path] | None = None,
    ) -> None:
        self._root = normalize_work_root(work_root)
        if read_roots is None:
            self._read_roots = parse_read_roots(None)
        else:
            self._read_roots = []
            for item in read_roots:
                try:
                    p = Path(item).expanduser().resolve()
                except OSError:
                    continue
                if p not in self._read_roots:
                    self._read_roots.append(p)

    @property
    def work_root(self) -> Path:
        return self._root

    @property
    def read_roots(self) -> list[Path]:
        return list(self._read_roots)

    def glob(
        self,
        pattern: str,
        *,
        path: str = ".",
        head_limit: int = 100,
        allow_broad: bool = False,
    ) -> list[str]:
        """按 glob 列路径。

        - 默认 ``path=.``：只扫 work_root（与历史行为一致）。
        - 传入 ``path``：经 ``resolve_for_read``（可在 FAWAI_READ_ROOTS 下）后从该目录扫；
          **禁止**静默忽略 path 仍只扫 work_root。
        - 整 work_root / 浅根：过宽则拒绝（见 ``search_root_too_broad``）。
        - 命中：work_root 内返回相对路径，否则返回绝对路径。
        """
        pattern = (pattern or "*").strip() or "*"
        limit = max(1, int(head_limit))
        base = resolve_for_read(self._root, path or ".", read_roots=self._read_roots)
        broad = search_root_too_broad(
            base,
            work_root=self._root,
            kind="glob",
            pattern=pattern,
            allow_broad=allow_broad,
        )
        if broad:
            raise ValueError(broad)
        # 兼容旧 path=/ 字面量过短闸（search_root 已覆盖浅根；保留双保险）
        legacy = _glob_root_too_broad(base, pattern)
        if legacy:
            raise ValueError(legacy)
        if base.is_file():
            shown = display_path(base, self._root)
            rel_to_base = base.name
            if path_matches_glob(rel_to_base, pattern) or path_matches_glob(shown, pattern):
                return [shown]
            return []
        if not base.is_dir():
            raise FileNotFoundError(display_path(base, self._root))

        in_work = path_under_any(base, [self._root])
        # 仓外树大：优先 rg --files；仓内保持 walk，避免 rg glob 语义与 path_matches_glob 不一致
        if not in_work:
            via_rg = self._glob_ripgrep(
                pattern, base=base, head_limit=limit, in_work=False
            )
            if via_rg is not None:
                return via_rg
        return self._glob_walk(
            pattern, base=base, head_limit=limit, in_work=in_work
        )

    def _glob_ripgrep(
        self,
        pattern: str,
        *,
        base: Path,
        head_limit: int,
        in_work: bool,
    ) -> list[str] | None:
        """用 rg --files 加速；不可用则返回 None 走 walk。"""
        if not shutil.which("rg"):
            return None
        pats = expand_glob_braces(pattern)
        cmd: list[str] = [
            "rg",
            "--files",
            "--color",
            "never",
            "--hidden",
            "--no-messages",
        ]
        for d in sorted(SKIP_DIR_NAMES_OUTSIDE):
            cmd.extend(["-g", f"!{d}/**"])
        for pat in pats:
            cmd.extend(["-g", pat])
            # 仅文件名模式时也匹配任意深度
            if "/" not in pat.rstrip("/"):
                cmd.extend(["-g", f"**/{pat}"])
        cmd.append(str(base.resolve()))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        # 0=有命中 1=无命中；2=部分权限错误但仍可能有 stdout —— 有输出则用，勿退回慢 walk
        if proc.returncode not in (0, 1, 2):
            return None
        if proc.returncode == 2 and not lines:
            return None
        out: list[str] = []
        for raw in lines:
            try:
                # 勿 resolve：仓外大树上 resolve 可能极慢；rg 已给绝对/相对可用路径
                file_p = Path(raw)
                if not file_p.is_absolute():
                    file_p = (base / raw)
            except OSError:
                continue
            shown = display_path(file_p, self._root)
            if in_work and is_skipped_rel(shown):
                continue
            if shown not in out:
                out.append(shown)
            if len(out) >= head_limit:
                break
        return out

    def _glob_walk(
        self,
        pattern: str,
        *,
        base: Path,
        head_limit: int,
        in_work: bool,
    ) -> list[str]:
        out: list[str] = []
        base_resolved = base.resolve()
        for dirpath, dirnames, filenames in os.walk(base_resolved, topdown=True):
            dir_p = Path(dirpath)
            if in_work:
                try:
                    rel_dir = rel_posix(dir_p, self._root)
                except ValueError:
                    dirnames.clear()
                    continue
                if rel_dir != "." and is_skipped_rel(rel_dir):
                    dirnames.clear()
                    continue
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if not should_prune_dir("" if rel_dir == "." else rel_dir, d)
                ]
            else:
                try:
                    rel_dir = dir_p.relative_to(base_resolved).as_posix()
                except ValueError:
                    rel_dir = "."
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if d not in SKIP_DIR_NAMES_OUTSIDE and not d.startswith("._")
                ]
            for name in sorted(filenames):
                if name.startswith("._"):
                    continue
                file_p = dir_p / name
                if in_work:
                    rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                    if is_skipped_rel(rel):
                        continue
                    match_key = rel
                    shown = rel
                else:
                    match_key = name if rel_dir == "." else f"{rel_dir}/{name}"
                    shown = display_path(file_p, self._root)
                if path_matches_glob(match_key, pattern) or path_matches_glob(
                    Path(match_key).name, pattern
                ):
                    out.append(shown)
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
        allow_broad: bool = False,
    ) -> list[dict[str, Any]]:
        base = resolve_for_read(self._root, path, read_roots=self._read_roots)
        broad = search_root_too_broad(
            base,
            work_root=self._root,
            kind="grep",
            pattern=pattern,
            allow_broad=allow_broad,
        )
        if broad:
            raise ValueError(broad)
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
        file_path = resolve_for_read(self._root, path, read_roots=self._read_roots)
        shown = display_path(file_path, self._root)
        in_work = path_under_any(file_path, [self._root])
        if in_work and is_skipped_rel(shown):
            raise WorkspacePathError(f"path is skipped: {shown}")
        if not file_path.is_file():
            raise FileNotFoundError(shown)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        start = offset - 1
        chunk = all_lines[start : start + limit]
        truncated = start + len(chunk) < len(all_lines)
        lines = [{"n": offset + i, "text": line} for i, line in enumerate(chunk)]
        return {"path": shown, "lines": lines, "truncated": truncated}

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
                format_old_not_found_message(
                    rel, str(old), text, label="substring"
                )
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
                    format_old_not_found_message(
                        rel, o, updated, label=f"hunk {i}: old"
                    )
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
        in_work = path_under_any(base, [self._root])
        if in_work:
            rel_base = rel_posix(base, self._root)
            cmd.append("." if rel_base == "." else rel_base)
            cwd: str | None = str(self._root)
        else:
            # 只读根外目录：对绝对路径直接 rg，cwd 用该目录
            cmd.append(str(base))
            cwd = str(base if base.is_dir() else base.parent)
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
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
        return self._parse_rg_json(
            proc.stdout or "",
            head_limit=head_limit,
            path_base=base if not in_work else None,
        )

    def _parse_rg_json(
        self,
        stdout: str,
        *,
        head_limit: int,
        path_base: Path | None = None,
    ) -> list[dict[str, Any]]:
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
                if path_base is not None:
                    abs_hit = Path(path) if Path(path).is_absolute() else (path_base / path)
                    path = display_path(abs_hit, self._root)
                elif is_skipped_rel(path):
                    pending = None
                    continue
                line_no = int((data.get("line_number") or 0))
                text = _clip_hit_text(
                    str((data.get("lines") or {}).get("text") or "").rstrip("\n")
                )
                pending = {
                    "path": path,
                    "line": line_no,
                    "text": text,
                    "before": [],
                    "after": [],
                }
            elif typ == "context" and pending is not None:
                line_no = int((data.get("line_number") or 0))
                text = _clip_hit_text(
                    str((data.get("lines") or {}).get("text") or "").rstrip("\n")
                )
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
        in_work = path_under_any(base, [self._root])
        for file_path in self._iter_files(base):
            if glob and glob != "*" and not Path(file_path.name).match(glob):
                continue
            shown = display_path(file_path, self._root)
            if in_work and is_skipped_rel(shown):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                if rx.search(line):
                    before = [
                        {"line": j, "text": _clip_hit_text(lines[j - 1])}
                        for j in range(max(1, i - context_before), i)
                    ]
                    after = [
                        {"line": j, "text": _clip_hit_text(lines[j - 1])}
                        for j in range(i + 1, min(len(lines), i + context_after) + 1)
                    ]
                    hits.append(
                        {
                            "path": shown,
                            "line": i,
                            "text": _clip_hit_text(line),
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
        in_work = path_under_any(base, [self._root])
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            if in_work:
                rel_dir = rel_posix(Path(dirpath), self._root)
                if rel_dir != "." and is_skipped_rel(rel_dir):
                    dirnames.clear()
                    continue
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if not should_prune_dir("" if rel_dir == "." else rel_dir, d)
                ]
            else:
                # 仓外只读：仍跳过常见噪声目录名
                dirnames[:] = [
                    d for d in sorted(dirnames) if d not in SKIP_DIR_NAMES_OUTSIDE
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
