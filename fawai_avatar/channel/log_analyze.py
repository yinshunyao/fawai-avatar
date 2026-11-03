# term:系统日志  doc:doc/功能/系统日志/说明.md
"""系统日志失败 → 整改任务目标（一键分析）。"""
from __future__ import annotations

import json
import time
from typing import Any

from fawai_avatar.channel.system_log import (
    SystemLogEntry,
    SystemLogStore,
    action_label,
    category_label,
)

_MAX_ENTRIES = 12
_DETAIL_CHARS = 600


def resolve_failure_entries(
    store: SystemLogStore,
    *,
    log_ids: list[str] | None = None,
    limit: int = 8,
    skip_analyzed: bool = True,
) -> list[SystemLogEntry]:
    """按 id 取失败日志；未给 id 则取最近失败。默认跳过已分析，避免重复开任务。"""
    limit = max(1, min(int(limit or 8), _MAX_ENTRIES))
    if log_ids:
        found = store.get_logs_by_ids([str(x) for x in log_ids if str(x).strip()])
        fails = [e for e in found if not e.ok]
        if skip_analyzed:
            fails = [e for e in fails if not e.analyzed]
        order = {str(i): n for n, i in enumerate(log_ids)}
        fails.sort(key=lambda e: order.get(e.log_id, 10_000))
        return fails[:limit]
    return store.list_logs(
        limit=limit,
        ok=False,
        analyzed=False if skip_analyzed else None,
    )


def already_analyzed_entries(
    store: SystemLogStore,
    log_ids: list[str],
) -> list[SystemLogEntry]:
    """指定 id 中已标记分析的失败条目（用于复开原整改任务）。"""
    found = store.get_logs_by_ids([str(x) for x in log_ids if str(x).strip()])
    return [e for e in found if not e.ok and e.analyzed]


def build_failure_analysis_goal(entries: list[SystemLogEntry]) -> str:
    """自动写清目标 / 要求 / 边界，复用任务对话与编码闭环整改。"""
    if not entries:
        raise ValueError("没有可分析的失败日志")
    blocks = [_format_entry(e, index=i + 1) for i, e in enumerate(entries[:_MAX_ENTRIES])]
    related = sorted(
        {str(e.task_id) for e in entries if e.task_id},
        key=lambda x: x,
    )
    related_line = (
        "、".join(t[:12] + ("…" if len(t) > 12 else "") for t in related[:6])
        if related
        else "无"
    )
    return f"""【系统日志失败整改】

## 目标
分析下列失败的系统日志操作，判定根因属于：① 工具实现 bug；② 工具描述 / 规划调度提示不清；③ 调用方误用或环境限制。对①②做工具代码与能力描述/planner 规则整改并回证；对③写清边界，可加失败提示，不强行改业务逻辑。

## 失败条目（共 {len(entries)} 条）
{chr(10).join(blocks)}

关联原任务 id：{related_line}

## 要求
1. 先用系统日志与（若有）关联任务上下文核对失败参数与回执，禁止凭印象改代码。
2. 分类落结论：工具 bug / 描述或调度不足 / 环境或误用。
3. 属工具或描述问题：改对应实现（如 `control/executor.py`、`control/background.py`、`workspace/`）与 `brain/capabilities.py` when_to_use、`brain/planner.py` 规则；必要时同步 `doc/功能/` 与术语表。
4. 改后 `code_check`（或针对性 `shell_verify`）回证；能复现的失败路径尽量用 dry_run/小脚本自测。
5. 结束时在 success 的 thought 用中文说明：根因、改了什么文件、如何防复发。

## 边界
- 只改与本次失败相关的工具 / 描述 / 调度；禁止无关大重构。
- 遵守自我进化最少核：禁止改保活/回退/闸门等禁改路径；禁止动 `.env` 与密钥。
- 偶发环境超时（如 AppleEvent）优先加失败提示与选用替代动作，不要假装能根治外部 App。
- 复用现有任务对话与编码闭环：`fs_grep` / `fs_read` / `fs_patch` / `code_check`；禁止臆造 `grep_result_*.txt`。
- 不要只口头建议；能改的直接改并回证。"""


def analysis_title(entries: list[SystemLogEntry]) -> str:
    if not entries:
        return "失败日志整改"
    if len(entries) == 1:
        e = entries[0]
        label = action_label(e.category, e.action) or e.action
        return f"整改失败·{label}"[:16]
    return f"整改失败·{len(entries)}条"[:16]


def analysis_intro(entries: list[SystemLogEntry]) -> str:
    lines = [
        "已根据系统日志失败条目自动创建整改任务（目标/要求/边界已写入任务）。",
        "将分析根因并增强工具或调度描述；请在本对话跟踪进度。",
        "",
        "纳入分析：",
    ]
    for i, e in enumerate(entries[:_MAX_ENTRIES], start=1):
        when = time.strftime("%H:%M:%S", time.localtime(e.ts)) if e.ts else "?"
        label = action_label(e.category, e.action) or e.action
        msg = (e.message or "").strip().replace("\n", " ")
        if len(msg) > 80:
            msg = msg[:79] + "…"
        lines.append(f"{i}. [{when}] {label} — {msg or '（无 message）'}")
    return "\n".join(lines)


def _format_entry(entry: SystemLogEntry, *, index: int) -> str:
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.ts))
        if entry.ts
        else "?"
    )
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    try:
        detail_s = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        detail_s = str(detail)
    if len(detail_s) > _DETAIL_CHARS:
        detail_s = detail_s[: _DETAIL_CHARS - 1] + "…"
    cat = category_label(entry.category)
    act = action_label(entry.category, entry.action)
    tid = entry.task_id or "无"
    return (
        f"{index}. log_id={entry.log_id}\n"
        f"   时间={when} 大类={cat}/{entry.category} 操作={act}/{entry.action}\n"
        f"   actor={entry.actor} task_id={tid}\n"
        f"   message={entry.message or '（空）'}\n"
        f"   detail={detail_s}"
    )


def meta_for_analysis(entries: list[SystemLogEntry]) -> dict[str, Any]:
    return {
        "source": "system_log_analyze",
        "title_source": "system_log_analyze",
        "log_ids": [e.log_id for e in entries],
        "source_task_ids": sorted({str(e.task_id) for e in entries if e.task_id}),
        "failure_count": len(entries),
    }
