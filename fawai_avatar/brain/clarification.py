# term:执行中澄清  doc:doc/功能/执行中澄清/说明.md
"""执行中澄清：need_input → waiting → 用户选择/短答 → 续跑。"""
from __future__ import annotations

import re
import time
from typing import Any

from fawai_avatar.task.models import TaskRecord
from fawai_avatar.types import Plan

CLARIFICATION_KEY = "clarification"
PHASE_AWAITING = "awaiting_input"


def options_from_plan(plan: Plan) -> list[dict[str, str]]:
    raw = plan.raw if isinstance(plan.raw, dict) else {}
    return normalize_options(raw.get("options"))


def normalize_options(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            label = item.strip()
            if not label:
                continue
            out.append({"id": str(i + 1), "label": label})
            continue
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("text") or item.get("name") or "").strip()
        oid = str(item.get("id") or item.get("value") or "").strip() or str(i + 1)
        if not label:
            continue
        out.append({"id": oid, "label": label})
    return out


def format_clarification_text(question: str, options: list[dict[str, str]]) -> str:
    q = (question or "").strip() or "需要你确认后才能继续。"
    if not options:
        return f"【澄清】{q}\n（请直接回复）"
    lines = [f"【澄清】{q}", "请回复编号或选项标签："]
    for i, opt in enumerate(options):
        oid = opt.get("id") or str(i + 1)
        label = opt.get("label") or ""
        lines.append(f"{i + 1}. [{oid}] {label}")
    return "\n".join(lines)


def build_clarification_meta(
    *,
    question: str,
    options: list[dict[str, str]],
    step: int | None = None,
) -> dict[str, Any]:
    return {
        "phase": PHASE_AWAITING,
        "question": (question or "").strip(),
        "options": options,
        "asked_at": time.time(),
        "step": int(step) if step is not None else None,
    }


def is_awaiting_clarification(task: TaskRecord | None) -> bool:
    if task is None:
        return False
    meta = task.meta or {}
    clar = meta.get(CLARIFICATION_KEY)
    if not isinstance(clar, dict):
        return False
    return str(clar.get("phase") or "") == PHASE_AWAITING


def should_skip_auto_recover(task: TaskRecord | None) -> bool:
    """进程恢复时勿自动拉 Worker：仅澄清等待跳过；复盘/取消任务允许追加执行。"""
    if task is None:
        return False
    if is_awaiting_clarification(task):
        return True
    # 取消复盘等待跳过限制：取消的复盘也可追加并按指令执行
    return False


def match_answer(
    text: str, options: list[dict[str, str]]
) -> tuple[str | None, dict[str, str] | None]:
    """返回 (resolved_label_or_raw, matched_option_or_None)。"""
    raw = (text or "").strip()
    if not raw:
        return None, None
    if not options:
        return raw, None
    compact = raw.strip()
    # 纯编号 1 / 1. / （1）
    m = re.match(r"^[\(（]?\s*(\d+)\s*[\)）]?[.、:：]?\s*$", compact)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(options):
            return options[idx]["label"], options[idx]
    low = compact.lower()
    for opt in options:
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or "").strip()
        if oid and low == oid.lower():
            return label or oid, opt
        if label and (low == label.lower() or label.lower() in low or low in label.lower()):
            return label, opt
    return raw, None


def format_answer_for_history(
    answer_text: str,
    *,
    matched: dict[str, str] | None,
    question: str,
) -> str:
    from fawai_avatar.brain.method_recovery import (
        format_remote_away_veto,
        looks_like_remote_away_text,
    )

    q = (question or "").strip()
    if matched:
        oid = matched.get("id") or ""
        label = matched.get("label") or answer_text
        base = (
            f"【用户澄清答复】针对「{q}」选择了 [{oid}] {label}。"
            f"请按该选择继续执行，不要再问同一问题。"
        )
    else:
        base = (
            f"【用户澄清答复】针对「{q}」：{answer_text}。"
            f"请按该答复继续执行（未精确匹配选项时以本条原文为准）。"
        )
    # 远程不在场：追加方法否决，禁止再要用户贴文件
    probe = f"{answer_text} {matched.get('label') if matched else ''}"
    if looks_like_remote_away_text(probe) or looks_like_remote_away_text(answer_text):
        return f"{base}\n{format_remote_away_veto(answer=answer_text)}"
    return base


def normalize_need_input_plan(plan: Plan) -> Plan:
    """need_input：清空动作；done=false；保留 thought/options。"""
    if plan.status != "need_input":
        return plan
    options = options_from_plan(plan)
    # 若问题本身在要「粘贴/拷进工作区」，去掉这类选项，避免远程场景误导
    from fawai_avatar.brain.method_recovery import strip_paste_options

    q = (plan.thought or "").strip()
    if any(tok in q for tok in ("粘贴", "拷贝", "拷进", "复制到工作区", "贴给")):
        options = strip_paste_options(options)
    raw = dict(plan.raw or {})
    if options:
        raw["options"] = options
    elif "options" in raw:
        raw.pop("options", None)
    return Plan(
        thought=plan.thought,
        actions=[],
        done=False,
        status="need_input",
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        delegate=plan.delegate,
    )
