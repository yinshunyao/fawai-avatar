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
    """工具观察 soft 压缩阈值。默认 `0`=关闭中等体量全文回灌；`>0` 才按 soft 压。

    即使 soft=0，仍受 `max_chars()` 绝对上限约束（防千万字符灌进 history）。
    """
    return _env_int("FAWAI_TOOL_OBS_SOFT_CHARS", 0)


def max_chars() -> int:
    """工具观察绝对上限（字符）。超过则强制头尾压缩+落盘。

    默认 `48000`；`0`=不设绝对上限（不推荐）。与 soft 独立：soft 管「中等也压」，
    max 管「再大也必须封顶」。大文件取证应靠 head/tail/中间起行，而非整份回灌。
    """
    return _env_int("FAWAI_TOOL_OBS_MAX_CHARS", 48000)


def hard_chars() -> int:
    return _env_int("FAWAI_TOOL_OBS_HARD_CHARS", 6000)


def shell_max_stdout_chars() -> int:
    """`shell` 源端 stdout/stderr 字符上限（头+尾保留）。默认 `120000`；`0`=不截。"""
    return _env_int("FAWAI_SHELL_MAX_STDOUT_CHARS", 120000)


def clip_head_tail(
    text: str,
    *,
    max_chars: int,
    head_ratio: float = 0.55,
    marker: str | None = None,
) -> tuple[str, bool]:
    """超长文本保留头+尾；返回 (裁切后文本, 是否裁切)。

    大文件取证常态是查开头/结尾/中间起行——裁切必须留尾，禁止只留前缀。
    """
    raw = text if isinstance(text, str) else str(text)
    limit = max(0, int(max_chars))
    if limit <= 0 or len(raw) <= limit:
        return raw, False
    note = marker or (
        f"\n…[已截断：原 {len(raw)} 字符；保留头+尾。"
        "大文件请用 head/tail/sed -n 起止行或 fs_read offset/limit，勿整文件 cat]…\n"
    )
    budget = limit - len(note)
    if budget < 64:
        # 上限过紧时仍尽量留头尾各一小段
        note = "…[截断]…"
        budget = max(0, limit - len(note))
    ratio = min(0.9, max(0.1, float(head_ratio)))
    head_n = max(1, int(budget * ratio))
    tail_n = max(1, budget - head_n)
    if head_n + tail_n > budget:
        tail_n = max(1, budget - head_n)
    return raw[:head_n] + note + raw[-tail_n:], True


def effective_compress_threshold(soft_override: int | None = None) -> int:
    """实际启用的压缩阈值：显式 soft_override > soft > max 绝对上限。

    返回 `0` 表示不压缩。
    """
    if soft_override is not None:
        return max(0, int(soft_override))
    soft = soft_chars()
    if soft > 0:
        return soft
    return max_chars()


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

    soft_override：检索 hits 等可传入更高/更低阈值；传入时不再套用绝对 max。
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
    threshold = effective_compress_threshold(soft_override)
    # threshold<=0：不压缩；未超阈值全文回灌
    if threshold <= 0 or len(raw) <= threshold:
        return raw, meta

    soft_n = soft_chars() if soft_override is None else max(0, int(soft_override))
    hard = max(threshold, hard_chars())
    meta["compressed"] = True
    if soft_override is None and soft_n <= 0:
        meta["max_capped"] = True
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
            "大文件取证用头/尾/中间起行（head/tail/sed -n、fs_read offset/limit）；"
            "本地落盘可用 fs_read，禁止 shell cat 整文件；"
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
        body, clipped = clip_head_tail(body, max_chars=hard)
        if clipped:
            meta["hard_clipped"] = True
    return body, meta


def _hit_body_text(hit: dict[str, Any]) -> str:
    """grep 用 text；lore / task_context 用 summary 或 snippet。"""
    for key in ("text", "summary", "snippet"):
        val = hit.get(key)
        if val is None:
            continue
        body = str(val).strip()
        if body:
            return body
    return ""


def _render_hits(hits: list[Any], *, max_hits: int | None = None) -> str:
    """把检索 hits 渲成可读正文，供规划直接读。

    兼容：
    - fs_grep：`path` + `line` + `text`（可含 before/after）
    - lore / task_context：`summary` / `snippet`，可选 `path` / `msg_id` / `id`

    默认展开工具返回的全部命中（体量由工具 head_limit 控制）；
    仅当显式传入 max_hits>0 时才截断展开条数。
    """
    rows = hits if max_hits is None or max_hits <= 0 else hits[: max(1, max_hits)]
    rendered: list[str] = []
    for hit in rows:
        if not isinstance(hit, dict):
            rendered.append(str(hit))
            continue
        body = _hit_body_text(hit)
        path = str(hit.get("path") or "").strip()
        msg_id = str(hit.get("msg_id") or hit.get("id") or "").strip()
        line_no = hit.get("line")
        before = hit.get("before") if isinstance(hit.get("before"), list) else []
        after = hit.get("after") if isinstance(hit.get("after"), list) else []
        outcome = hit.get("outcome") if isinstance(hit.get("outcome"), dict) else None
        # shovel 台账：有 outcome / when_to_use，无 grep 行号
        # invoke 必须进观察正文（调用侧契约；禁止只留 path/标题让模型猜 CLI）
        if outcome is not None or (
            hit.get("when_to_use") and line_no is None and not before and not after
        ):
            sid = str(hit.get("id") or msg_id or "?").strip() or "?"
            title = str(hit.get("title") or "").strip()
            when = str(hit.get("when_to_use") or "").strip()
            invoke = str(hit.get("invoke") or "").strip()
            rate = str((outcome or {}).get("rate") or "unknown")
            fail = str((outcome or {}).get("last_fail_one_liner") or "").strip()
            design = ",".join(
                str(hit.get(k) or "")
                for k in ("completeness", "generality", "efficiency")
                if hit.get(k) not in (None, "")
            )
            line = f"{sid}: {title} — {when}" if when else f"{sid}: {title}"
            line += f" | outcome={rate}"
            if fail:
                line += f" last_fail={fail[:120]}"
            if design:
                line += f" | design={design}"
            if path:
                line += f" | path={path}"
            if invoke:
                line += f" | invoke={invoke}"
            else:
                line += " | invoke=(missing)"
            rendered.append(line)
            continue
        # lore / task_context：无 path:line:text 时用摘要行，禁止渲染成 ?:?:
        if not path and line_no is None and not before and not after:
            label = msg_id or "?"
            kind = str(hit.get("kind") or "").strip()
            if kind:
                label = f"{label}[{kind}]"
            rendered.append(f"{label}: {body}" if body else label)
            continue
        loc = path or msg_id or "?"
        line_s = "?" if line_no is None else line_no
        for row in before:
            if isinstance(row, dict):
                rendered.append(
                    f"{loc}:{row.get('line', '?')}- {row.get('text', '')}"
                )
        rendered.append(f"{loc}:{line_s}: {body}")
        for row in after:
            if isinstance(row, dict):
                rendered.append(
                    f"{loc}:{row.get('line', '?')}+ {row.get('text', '')}"
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

        # message 本体很长且未走 stdout 字段时，同样走 soft / 绝对上限压缩
        threshold = effective_compress_threshold()
        if (
            threshold > 0
            and len(msg) > threshold
            and "stdout" not in digested_keys
        ):
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

        # skill_search 极性分栏
        imitate = data.get("imitate")
        avoid = data.get("avoid")
        if isinstance(imitate, list) or isinstance(avoid, list):
            im_rows = imitate if isinstance(imitate, list) else []
            av_rows = avoid if isinstance(avoid, list) else []
            lines.append(
                f"  polarity: imitate={len(im_rows)} avoid={len(av_rows)}"
                "（avoid=勿当正例照抄）"
            )
            for label, rows in (("imitate", im_rows[:8]), ("avoid", av_rows[:8])):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sid = row.get("id") or "?"
                    title = row.get("title") or ""
                    outcome = row.get("outcome") or row.get("polarity") or ""
                    src = str(row.get("source") or "").strip()
                    extra = f" source={src}" if src else ""
                    lines.append(f"  [{label}] {sid}: {title} ({outcome}){extra}")

        # fs_grep / explore / shovel hits：中等全文；超 soft 或绝对上限则头+尾压缩。
        # 体量优先靠工具 head_limit/context；模型自行决定是否加大 limit 翻页。
        hit_rows = data.get("hits")
        if hit_rows is None:
            hit_rows = data.get("matches")
        hits_soft = hits_soft_chars()
        if isinstance(hit_rows, list) and hit_rows:
            hit_text = _render_hits(hit_rows)
            if hits_soft > 0:
                digested, part_meta = digest_text(
                    hit_text,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                    soft_override=hits_soft,
                )
            else:
                digested, part_meta = digest_text(
                    hit_text,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                )
            meta["parts"].append(part_meta)
            if part_meta.get("compressed"):
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
            if hits_soft > 0:
                digested, part_meta = digest_text(
                    preview,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                    soft_override=hits_soft,
                )
            else:
                digested, part_meta = digest_text(
                    preview,
                    goal=goal,
                    label="hits",
                    data_dir=data_dir,
                )
            meta["parts"].append(part_meta)
            if part_meta.get("compressed"):
                lines.append(f"  hits（摘要）:\n{digested}")
            else:
                lines.append(f"  hits: {preview}")

    if len(results) < len(actions):
        lines.append(f"- （其余 {len(actions) - len(results)} 个动作未执行）")

    text = "\n".join(lines)
    meta["compressed"] = any(p.get("compressed") for p in meta["parts"])
    return text, meta
