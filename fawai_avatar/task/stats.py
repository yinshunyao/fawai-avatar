# term:任务统计  doc:doc/功能/任务统计/说明.md
"""任务级用量汇总：token / 墙钟 / 轮次 / LLM 次数 / 工具分布。"""
from __future__ import annotations

import time
from typing import Any

STATS_KEY = "stats"


def empty_stats() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "wall_ms": 0,
        "rounds": 0,
        "llm_calls": 0,
        "tools": {},
        "run_started_at": None,
    }


def normalize_stats(raw: Any) -> dict[str, Any]:
    base = empty_stats()
    if not isinstance(raw, dict):
        return base
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "wall_ms", "rounds", "llm_calls"):
        try:
            base[key] = max(0, int(raw.get(key) or 0))
        except (TypeError, ValueError):
            pass
    tools = raw.get("tools")
    if isinstance(tools, dict):
        cleaned: dict[str, int] = {}
        for k, v in tools.items():
            kind = str(k or "").strip()
            if not kind:
                continue
            try:
                cleaned[kind] = max(0, int(v or 0))
            except (TypeError, ValueError):
                continue
        base["tools"] = cleaned
    started = raw.get("run_started_at")
    if started is not None:
        try:
            base["run_started_at"] = float(started)
        except (TypeError, ValueError):
            base["run_started_at"] = None
    return base


def apply_usage(stats: dict[str, Any], usage: Any) -> None:
    if not isinstance(usage, dict) or not usage:
        return
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    try:
        if prompt is not None:
            stats["prompt_tokens"] = int(stats.get("prompt_tokens") or 0) + max(0, int(prompt))
        if completion is not None:
            stats["completion_tokens"] = int(stats.get("completion_tokens") or 0) + max(
                0, int(completion)
            )
        if total is not None:
            stats["total_tokens"] = int(stats.get("total_tokens") or 0) + max(0, int(total))
        elif prompt is not None or completion is not None:
            stats["total_tokens"] = int(stats.get("prompt_tokens") or 0) + int(
                stats.get("completion_tokens") or 0
            )
    except (TypeError, ValueError):
        return


def apply_llm_call(stats: dict[str, Any], *, usage: Any = None) -> None:
    stats["llm_calls"] = int(stats.get("llm_calls") or 0) + 1
    apply_usage(stats, usage)


def apply_tools(stats: dict[str, Any], actions: list[Any] | None) -> None:
    if not actions:
        return
    tools = dict(stats.get("tools") or {})
    for action in actions:
        kind = ""
        if isinstance(action, dict):
            kind = str(action.get("kind") or "").strip()
        else:
            kind = str(getattr(action, "kind", "") or "").strip()
        if not kind:
            continue
        tools[kind] = int(tools.get(kind) or 0) + 1
    stats["tools"] = tools


def apply_round(stats: dict[str, Any]) -> None:
    stats["rounds"] = int(stats.get("rounds") or 0) + 1


def begin_run_segment(stats: dict[str, Any], *, now: float | None = None) -> None:
    if stats.get("run_started_at") is not None:
        return
    stats["run_started_at"] = float(now if now is not None else time.time())


def end_run_segment(stats: dict[str, Any], *, now: float | None = None) -> None:
    started = stats.get("run_started_at")
    if started is None:
        return
    try:
        started_f = float(started)
    except (TypeError, ValueError):
        stats["run_started_at"] = None
        return
    end = float(now if now is not None else time.time())
    delta = max(0, int((end - started_f) * 1000))
    stats["wall_ms"] = int(stats.get("wall_ms") or 0) + delta
    stats["run_started_at"] = None


def live_elapsed_ms(stats: dict[str, Any], *, now: float | None = None) -> int:
    base = max(0, int(stats.get("wall_ms") or 0))
    started = stats.get("run_started_at")
    if started is None:
        return base
    try:
        started_f = float(started)
    except (TypeError, ValueError):
        return base
    end = float(now if now is not None else time.time())
    return base + max(0, int((end - started_f) * 1000))


def persistable(stats: dict[str, Any]) -> dict[str, Any]:
    """落盘形态（可保留 run_started_at，供 running 列表续算）。"""
    out = normalize_stats(stats)
    return out


def summary_for_api(stats: dict[str, Any] | None, *, now: float | None = None) -> dict[str, Any]:
    s = normalize_stats(stats)
    tools = dict(s.get("tools") or {})
    return {
        "prompt_tokens": int(s.get("prompt_tokens") or 0),
        "completion_tokens": int(s.get("completion_tokens") or 0),
        "total_tokens": int(s.get("total_tokens") or 0),
        "elapsed_ms": live_elapsed_ms(s, now=now),
        "rounds": int(s.get("rounds") or 0),
        "llm_calls": int(s.get("llm_calls") or 0),
        "tools": tools,
    }


def backfill_from_steps(steps: Any) -> dict[str, Any]:
    """从 meta.steps 尽力回填（截断后可能无 token）。"""
    stats = empty_stats()
    if not isinstance(steps, list):
        return stats
    for step in steps:
        if not isinstance(step, dict):
            continue
        apply_round(stats)
        plan = step.get("plan") if isinstance(step.get("plan"), dict) else {}
        raw = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        if usage or plan:
            apply_llm_call(stats, usage=usage)
        apply_tools(stats, plan.get("actions") if isinstance(plan, dict) else None)
    return stats


def ensure_task_stats(meta: dict[str, Any]) -> dict[str, Any]:
    """读取或从 steps 懒回填；写回 meta 引用侧由调用方 persist。"""
    existing = meta.get(STATS_KEY)
    if isinstance(existing, dict) and (
        existing.get("rounds")
        or existing.get("llm_calls")
        or existing.get("total_tokens")
        or existing.get("wall_ms")
        or existing.get("tools")
        or existing.get("run_started_at")
    ):
        return normalize_stats(existing)
    filled = backfill_from_steps(meta.get("steps"))
    if (
        filled["rounds"]
        or filled["llm_calls"]
        or filled["total_tokens"]
        or filled["tools"]
    ):
        meta[STATS_KEY] = persistable(filled)
        return filled
    return normalize_stats(existing)
