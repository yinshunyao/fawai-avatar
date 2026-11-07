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
    """工具观察 soft 压缩阈值。默认 `0`=关闭（压缩会拖慢规划）；`>0` 才启用。"""
    return _env_int("FAWAI_TOOL_OBS_SOFT_CHARS", 0)


def hard_chars() -> int:
    return _env_int("FAWAI_TOOL_OBS_HARD_CHARS", 6000)


def hits_soft_chars() -> int:
    """fs_grep / explore hits 专用 soft。

    默认 `0`=不压缩（回灌与工具返回对齐；体量靠 head_limit/context）。
    `>0` 才按字符数压缩；勿再把 `0` 解释成沿用 stdout soft。
    """
    return _env_int("FAWAI_TOOL_OBS_HITS_SOFT_CHARS", 0)


def head_lines() -> int:
    return _env_int("FAWAI_TOOL_OBS_HEAD_LINES", 24)


def tail_lines() -> int:
    return _env_int("FAWAI_TOOL_OBS_TAIL_LINES", 40)


def keyword_hit_limit() -> int:
    return _env_int("FAWAI_TOOL_OBS_KEYWORD_HITS", 24)


def fs_read_line_preview() -> int:
    """fs_read 回灌展开行数硬顶。

    默认 `0`：与本次工具返回的 `lines` 对齐（读多少回灌多少；窗口由模型
    `offset`/`limit` 决定）。`>0` 才额外封顶（紧急刹车）。
    """
    return _env_int("FAWAI_TOOL_OBS_FS_READ_LINES", 0)


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
    soft_override: int | None = None,
    lead: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """压缩大段工具文本；返回 (展示串, meta)。

    soft_override：检索 hits 等可传入更高阈值，避免误用 stdout 紧门。
    lead：替换默认导语。默认导语要求读落盘全文；反省入模传入自己的非原文规则，避免再下令补读。
    """
    raw = text if isinstance(text, str) else str(text)
    meta: dict[str, Any] = {
        "observe_method": "obs_tool",
        "label": label,
        "bytes": len(raw.encode("utf-8", errors="replace")),
        "chars": len(raw),
        "lines": raw.count("\n") + (1 if raw else 0),
        "compressed": False,
    }
    soft = soft_chars() if soft_override is None else max(0, int(soft_override))
    # soft<=0：关闭压缩，全文回灌
    if soft <= 0 or len(raw) <= soft:
        return raw, meta

    hard = max(soft, hard_chars())
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

    rel_hint = ""
    try:
        from pathlib import Path as _P

        from fawai_avatar.config import RuntimeConfig

        root = _P(RuntimeConfig.from_env().work_root).expanduser().resolve()
        rel = str(_P(dump_path).resolve().relative_to(root))
        rel_hint = f"；工作区内相对路径 `{rel}`（优先用此 path 做 fs_read）"
    except Exception:  # noqa: BLE001 — 相对路径提示失败不影响压缩
        rel_hint = ""
    if lead is None:
        opening = (
            f"（已压缩：原 {meta['chars']} 字符 / {meta['lines']} 行；"
            f"全文落盘 {dump_path}{rel_hint}；"
            "请用 fs_read 读该文件后再总结，禁止 shell cat 代替；"
            "禁止臆造 grep_result_*.txt）"
        )
    else:
        opening = (
            lead.rstrip()
            + f"\n（压缩摘录：原 {meta['chars']} 字符 / {meta['lines']} 行。"
            "这不是原文，只用于分析处置策略，不能作为原文处理。）"
        )
    parts: list[str] = [
        opening,
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


def _render_hits(hits: list[Any], *, max_hits: int | None = None) -> str:
    """把 grep/explore hits 渲成 path:line 正文，供规划直接读。

    默认展开工具返回的全部命中（体量由工具 head_limit 控制）；
    仅当显式传入 max_hits>0 时才截断展开条数。
    """
    rows = hits if max_hits is None or max_hits <= 0 else hits[: max(1, max_hits)]
    rendered: list[str] = []
    for hit in rows:
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
    if max_hits is not None and max_hits > 0 and len(hits) > max_hits:
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

    from fawai_avatar.channel.redact import redact_secrets

    lines = ["执行结果（工具结果观察）："]
    for i, result in enumerate(results):
        action = actions[i] if i < len(actions) else None
        kind = action.kind if action else "?"
        args = action.args if action else {}
        cmd = args.get("cmd") or args.get("command") or args.get("argv")
        path = args.get("path")
        msg = redact_secrets(str(result.message or ""))
        head = f"- {kind}"
        if cmd:
            head += f" cmd={redact_secrets(str(cmd))!r}"
        if path:
            head += f" path={path!r}"
        # 头行只留短摘要，避免配置/口令全文直接进 history
        msg_preview = msg if len(msg) <= 240 else (msg[:239] + "…")
        head += f" → {'ok' if result.ok else 'FAIL'}: {msg_preview}"
        lines.append(head)

        data = result.data or {}
        # mutate 落盘后的 diff 小窗：expect 假失败或成功验收都能看到现状
        if data.get("write_landed_despite_expect") or (
            not result.ok and data.get("expect_pre_ok") and data.get("diff")
        ):
            diff = str(data.get("diff") or "").strip()
            if diff:
                preview = diff if len(diff) <= 480 else (diff[:479] + "…")
                lines.append(f"  已落盘 diff 小窗:\n{preview}")
        digested_keys = set()
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
                redact_secrets(str(val)), goal=goal, label=label, data_dir=data_dir
            )
            meta["parts"].append(part_meta)
            digested_keys.add(key)
            tag = "已压缩" if part_meta.get("compressed") else "全文"
            lines.append(f"  {label}（{tag}）:\n{digested}")

        # message 本体很长且未走 stdout 字段时，同样可 soft 压缩（soft<=0 则跳过）
        soft_n = soft_chars()
        if soft_n > 0 and len(msg) > soft_n and "stdout" not in digested_keys:
            digested, part_meta = digest_text(
                msg, goal=goal, label="stdout", data_dir=data_dir
            )
            meta["parts"].append(part_meta)
            tag = "已压缩" if part_meta.get("compressed") else "全文"
            lines.append(f"  stdout（{tag}）:\n{digested}")

        # fs_read：回灌与本次读到的行对齐（默认不再二次截断/ soft 压缩）；
        # 一次看多少行由模型 offset/limit 决定。
        file_lines = data.get("lines")
        if isinstance(file_lines, list) and file_lines:
            cap = fs_read_line_preview()
            shown_rows = file_lines if cap <= 0 else file_lines[:cap]
            rendered: list[str] = []
            for row in shown_rows:
                if isinstance(row, dict):
                    rendered.append(f"{row.get('n', '?')}: {row.get('text', '')}")
                else:
                    rendered.append(str(row))
            body = "\n".join(rendered)
            if cap > 0 and len(file_lines) > cap:
                body += (
                    f"\n…共 {len(file_lines)} 行已读，观察硬顶 {cap} 行"
                    "（FAWAI_TOOL_OBS_FS_READ_LINES）；请缩小 limit 或提高硬顶"
                )
            elif data.get("truncated"):
                body += "\n…文件后续未读完；需要时加大 offset 续读（本次已回灌全部已读行）"
            lines.append(f"  lines:\n{body}")
        if data.get("paths"):
            paths = data.get("paths")
            preview = paths if isinstance(paths, list) else [paths]
            shown = preview[:40]
            path_block = "\n".join(f"  {p}" for p in shown)
            if len(preview) > 40:
                path_block += f"\n  …共 {len(preview)} 条"
            lines.append(f"  paths ({len(preview)}):\n{path_block}")

        # fs_grep / explore hits：默认全文回灌（不压缩、不二次截断条数）；
        # 体量靠工具 head_limit/context；模型自行决定是否加大 limit 翻页。
        hit_rows = data.get("hits")
        if hit_rows is None:
            hit_rows = data.get("matches")
        hits_soft = hits_soft_chars()
        if isinstance(hit_rows, list) and hit_rows:
            hit_text = _render_hits(hit_rows)
            if hits_soft > 0 and len(hit_text) > hits_soft:
                digested, part_meta = digest_text(
                    hit_text,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                    soft_override=hits_soft,
                )
                meta["parts"].append(part_meta)
                lines.append(f"  hits（摘要，共 {len(hit_rows)}）:\n{digested}")
            else:
                lines.append(f"  hits（共 {len(hit_rows)}）:\n{hit_text}")
            try:
                action_limit = int(args.get("head_limit") or 40)
            except (TypeError, ValueError):
                action_limit = 40
            if action_limit > 0 and len(hit_rows) >= action_limit:
                lines.append(
                    f"  （已达本次 head_limit={action_limit}；"
                    "需要更多请加大 head_limit 或收窄 pattern 后再搜）"
                )
        elif hit_rows is not None and not isinstance(hit_rows, list):
            preview = str(hit_rows)
            if hits_soft > 0 and len(preview) > hits_soft:
                digested, part_meta = digest_text(
                    preview,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                    soft_override=hits_soft,
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
