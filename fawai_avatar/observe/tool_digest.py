# term:工具结果观察  doc:doc/议题/观察选取策略/结论.md
# term:观察方式  doc:doc/功能/观察/说明.md
"""工具结果智能取证（obs_tool）：体量门禁、头尾窗、定向抽、落盘指针。"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fawai_avatar.types import Action, ExecResult

_ERROR_LINE = re.compile(
    r"(?i)(error|exception|traceback|failed|fatal|panic|拒绝|失败|报错|E\d{3,})"
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def soft_chars() -> int:
    # 默认收紧：大文件反复回灌时控制单条工具观察体积
    return _env_int("FAWAI_TOOL_OBS_SOFT_CHARS", 2000)


def hard_chars() -> int:
    return _env_int("FAWAI_TOOL_OBS_HARD_CHARS", 6000)


def head_lines() -> int:
    return _env_int("FAWAI_TOOL_OBS_HEAD_LINES", 24)


def tail_lines() -> int:
    return _env_int("FAWAI_TOOL_OBS_TAIL_LINES", 40)


def keyword_hit_limit() -> int:
    return _env_int("FAWAI_TOOL_OBS_KEYWORD_HITS", 24)


def fs_read_line_preview() -> int:
    """fs_read 回灌 history 时最多展开多少行（默认 40）。"""
    return _env_int("FAWAI_TOOL_OBS_FS_READ_LINES", 40)


def _observe_dir(data_dir: str | Path | None = None) -> Path:
    root = Path(data_dir or os.environ.get("FAWAI_DATA_DIR") or "data")
    path = root / "observe"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _goal_keywords(goal: str) -> list[str]:
    text = (goal or "").strip()
    if not text:
        return []
    parts = re.split(r"[\s,，。？?！!；;：:、/\\]+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        if p.lower() in {"不支持", "需要", "直接", "是否", "怎么", "什么"}:
            continue
        out.append(p)
        if len(out) >= 12:
            break
    return out


def _dump_full(text: str, *, kind: str, data_dir: str | Path | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"tool_{stamp}_{uuid.uuid4().hex[:8]}_{kind}.txt"
    path = _observe_dir(data_dir) / name
    path.write_text(text, encoding="utf-8", errors="replace")
    return str(path.resolve())


def digest_text(
    text: str,
    *,
    goal: str = "",
    label: str = "stdout",
    data_dir: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """压缩大段工具文本；返回 (展示串, meta)。"""
    raw = text if isinstance(text, str) else str(text)
    meta: dict[str, Any] = {
        "observe_method": "obs_tool",
        "label": label,
        "bytes": len(raw.encode("utf-8", errors="replace")),
        "chars": len(raw),
        "lines": raw.count("\n") + (1 if raw else 0),
        "compressed": False,
    }
    soft = soft_chars()
    hard = max(soft, hard_chars())
    if len(raw) <= soft:
        return raw, meta

    meta["compressed"] = True
    dump_path = _dump_full(raw, kind=label, data_dir=data_dir)
    meta["dump_path"] = dump_path

    lines = raw.splitlines()
    head_n = head_lines()
    tail_n = tail_lines()
    head = lines[:head_n]
    tail = lines[-tail_n:] if len(lines) > head_n else []

    keywords = _goal_keywords(goal)
    hits: list[str] = []
    hit_budget = keyword_hit_limit()
    if keywords:
        lowered_kws = [k.lower() for k in keywords]
        for i, line in enumerate(lines):
            low = line.lower()
            if any(k in low for k in lowered_kws) or _ERROR_LINE.search(line):
                hits.append(f"L{i + 1}: {line}")
                if len(hits) >= hit_budget:
                    break
    else:
        for i, line in enumerate(lines):
            if _ERROR_LINE.search(line):
                hits.append(f"L{i + 1}: {line}")
                if len(hits) >= hit_budget:
                    break

    parts: list[str] = [
        (
            f"（已压缩：原 {meta['chars']} 字符 / {meta['lines']} 行；"
            f"全文落盘 {dump_path}；请用 fs_read path 读该绝对路径，"
            "禁止臆造 grep_result_*.txt）"
        ),
        f"--- 头 {len(head)} 行 ---",
        "\n".join(head),
    ]
    if hits:
        parts.append(f"--- 定向/错误命中 {len(hits)} 行 ---")
        parts.append("\n".join(hits))
    if tail and (not head or tail[0] != head[0] or len(lines) > head_n + tail_n):
        # 避免与头完全重复时仍贴尾
        overlap = set(head[max(0, len(head) - 5) :])
        tail_use = [ln for ln in tail if ln not in overlap] or tail
        parts.append(f"--- 尾 {len(tail_use)} 行 ---")
        parts.append("\n".join(tail_use))

    body = "\n".join(parts)
    if len(body) > hard:
        body = body[: hard - 1] + "…"
        meta["hard_clipped"] = True
    return body, meta


def _render_hits(hits: list[Any], *, max_hits: int = 40) -> str:
    """把 grep/explore hits 渲成 path:line 正文，供规划直接读。"""
    rendered: list[str] = []
    for hit in hits[: max(1, max_hits)]:
        if not isinstance(hit, dict):
            rendered.append(str(hit))
            continue
        path = str(hit.get("path") or "?")
        line_no = hit.get("line", "?")
        text = str(hit.get("text") or "")
        before = hit.get("before") if isinstance(hit.get("before"), list) else []
        after = hit.get("after") if isinstance(hit.get("after"), list) else []
        for row in before:
            if isinstance(row, dict):
                rendered.append(
                    f"{path}:{row.get('line', '?')}- {row.get('text', '')}"
                )
        rendered.append(f"{path}:{line_no}: {text}")
        for row in after:
            if isinstance(row, dict):
                rendered.append(
                    f"{path}:{row.get('line', '?')}+ {row.get('text', '')}"
                )
    if len(hits) > max_hits:
        rendered.append(f"…另有 {len(hits) - max_hits} 条命中未展开")
    return "\n".join(rendered)


def format_tool_observation(
    actions: list[Action],
    results: list[ExecResult],
    *,
    goal: str = "",
    data_dir: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """生成回灌 history 的工具结果观察文案。"""
    meta: dict[str, Any] = {
        "observe_method": "obs_tool",
        "observe_label": "工具结果",
        "parts": [],
    }
    if not actions and not results:
        return "执行结果：本步无动作。", meta

    lines = ["执行结果（工具结果观察）："]
    for i, result in enumerate(results):
        action = actions[i] if i < len(actions) else None
        kind = action.kind if action else "?"
        args = action.args if action else {}
        cmd = args.get("cmd") or args.get("command") or args.get("argv")
        path = args.get("path")
        head = f"- {kind}"
        if cmd:
            head += f" cmd={cmd!r}"
        if path:
            head += f" path={path!r}"
        head += f" → {'ok' if result.ok else 'FAIL'}: {result.message}"
        lines.append(head)

        data = result.data or {}
        for key, label in (
            ("stdout", "stdout"),
            ("stderr", "stderr"),
            ("text", "content"),
            ("content", "content"),
        ):
            val = data.get(key)
            if val is None or not str(val).strip():
                continue
            digested, part_meta = digest_text(
                str(val), goal=goal, label=label, data_dir=data_dir
            )
            meta["parts"].append(part_meta)
            tag = "已压缩" if part_meta.get("compressed") else "全文"
            lines.append(f"  {label}（{tag}）:\n{digested}")

        file_lines = data.get("lines")
        if isinstance(file_lines, list) and file_lines:
            preview_n = fs_read_line_preview()
            rendered: list[str] = []
            for row in file_lines[:preview_n]:
                if isinstance(row, dict):
                    rendered.append(f"{row.get('n', '?')}: {row.get('text', '')}")
                else:
                    rendered.append(str(row))
            body = "\n".join(rendered)
            if len(file_lines) > preview_n:
                body += (
                    f"\n…共 {len(file_lines)} 行已读，仅展示前 {preview_n} 行；"
                    "下一刀请按行号小窗 fs_read，禁止无新问题整段重读"
                )
            # 大段 lines 再走 soft/hard 压缩，避免单步把规划打满
            if len(body) > soft_chars():
                digested, part_meta = digest_text(
                    body, goal=goal, label="lines", data_dir=data_dir
                )
                meta["parts"].append(part_meta)
                lines.append(f"  lines（已压缩）:\n{digested}")
            else:
                lines.append(f"  lines:\n{body}")
        if data.get("paths"):
            paths = data.get("paths")
            preview = paths if isinstance(paths, list) else [paths]
            shown = preview[:40]
            path_block = "\n".join(f"  {p}" for p in shown)
            if len(preview) > 40:
                path_block += f"\n  …共 {len(preview)} 条"
            lines.append(f"  paths ({len(preview)}):\n{path_block}")

        # fs_grep / explore 等返回 hits；兼容旧字段 matches
        hit_rows = data.get("hits")
        if hit_rows is None:
            hit_rows = data.get("matches")
        if isinstance(hit_rows, list) and hit_rows:
            hit_text = _render_hits(hit_rows)
            if len(hit_text) > soft_chars():
                digested, part_meta = digest_text(
                    hit_text, goal=goal, label="hits", data_dir=data_dir
                )
                meta["parts"].append(part_meta)
                lines.append(f"  hits（摘要，共 {len(hit_rows)}）:\n{digested}")
                lines.append(
                    "  【宽检索提示】命中已压缩：下一刀请收窄 pattern / 加 context，"
                    "或按 path:line 小窗 fs_read；禁止同范围再宽 grep。"
                )
            else:
                lines.append(f"  hits（共 {len(hit_rows)}）:\n{hit_text}")
                if len(hit_rows) >= 20:
                    lines.append(
                        "  【宽检索提示】命中偏多：优先收窄 pattern 或按行号定向，"
                        "勿重复同范围检索。"
                    )
        elif hit_rows is not None and not isinstance(hit_rows, list):
            preview = str(hit_rows)
            if len(preview) > soft_chars():
                digested, part_meta = digest_text(
                    preview, goal=goal, label="hits", data_dir=data_dir
                )
                meta["parts"].append(part_meta)
                lines.append(f"  hits（摘要）:\n{digested}")
            else:
                lines.append(f"  hits: {preview}")

    if len(results) < len(actions):
        lines.append(f"- （其余 {len(actions) - len(results)} 个动作未执行）")

    text = "\n".join(lines)
    meta["compressed"] = any(p.get("compressed") for p in meta["parts"])
    return text, meta
