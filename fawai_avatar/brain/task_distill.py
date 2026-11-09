# term:自我进化  doc:doc/功能/自我进化/说明.md
"""任务终态条件短卡蒸馏：多法成功 / 已明确失败 → 价值台账 → 过门 lore。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from fawai_avatar.brain.ledger_gate import GateNotify, parse_gate_json

logger = logging.getLogger(__name__)

# 配置键名（真源仅 memory_settings.sqlite，不进 env）
KEY_TASK_DISTILL = "FAWAI_TASK_DISTILL"
# 字面量供 channel.selfcheck 扫 phase.*（勿只留变量）
PHASE = "task_distill"
_PHASE_LITERAL_FOR_I18N = 'phase="task_distill"'  # noqa: F841 — 自检锚点

ProgressFn = Callable[[str, dict[str, Any]], None]
UsageFn = Callable[[dict[str, Any] | None, int], None]


def distill_enabled() -> bool:
    """默认开。真源：记忆配置库 ``FAWAI_TASK_DISTILL``（不读 / 不写 env）。"""
    try:
        from fawai_avatar.memory_settings import get_memory_store

        return bool(get_memory_store().task_distill_enabled(default=True))
    except Exception:
        return True


def _as_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _tool_kinds_from_steps(steps: list[Any]) -> set[str]:
    kinds: set[str] = set()
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        plan = st.get("plan") if isinstance(st.get("plan"), dict) else {}
        for a in plan.get("actions") or []:
            if isinstance(a, dict):
                k = str(a.get("kind") or "").strip()
                if k:
                    kinds.add(k)
    return kinds


def _had_fail_then_ok(steps: list[Any]) -> bool:
    saw_fail = False
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        for r in st.get("results") or []:
            if not isinstance(r, dict):
                continue
            if r.get("ok") is False:
                saw_fail = True
            elif r.get("ok") is True and saw_fail:
                return True
    return False


def _rounds_from_meta(meta: dict[str, Any], steps: list[Any]) -> int:
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    if stats.get("rounds") is not None:
        return max(0, _as_int(stats.get("rounds")))
    return len(steps or [])


def task_already_promoted(data_dir: str, task_id: str) -> bool:
    """本任务任一条成功/失败台账已带 lore_id → 跳过终态蒸馏。"""
    tid = (task_id or "").strip()
    if not tid:
        return False
    try:
        from fawai_avatar.brain import failure_ledger, success_ledger

        for row in success_ledger.list_entries(data_dir, tid):
            if str(row.get("lore_id") or "").strip():
                return True
        for row in failure_ledger.list_entries(data_dir, tid):
            if str(row.get("lore_id") or "").strip():
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_distill promoted check failed: %s", exc)
    return False


def eligible_for_distill(
    *,
    ok: bool,
    meta: dict[str, Any] | None,
    steps: list[Any] | None = None,
) -> dict[str, Any]:
    """
    启发式：是否值得终态短卡蒸馏。

    - completed：rounds≥6 或 (replan+reflect+stagnation)≥1 或 工具 kind≥4
      或 先失败后成功动作
    - failed：rounds≥2 且 reflect/stagnation/final_thought/error 任一非空
    """
    m = dict(meta or {})
    st = list(steps if steps is not None else (m.get("steps") or []))
    rounds = _rounds_from_meta(m, st)
    replan = _as_int(m.get("replan_count"))
    reflect = _as_int(m.get("reflect_count"))
    stagn = _as_int(m.get("stagnation_count"))
    kinds = _tool_kinds_from_steps(st)
    fail_then_ok = _had_fail_then_ok(st)
    final_thought = str(m.get("final_thought") or "").strip()
    error = str(m.get("error") or "").strip()
    has_reflect = bool(m.get("reflection")) or reflect > 0
    has_narrative = bool(final_thought or error or has_reflect or stagn > 0)

    if ok:
        reasons: list[str] = []
        if rounds >= 6:
            reasons.append(f"rounds>={rounds}")
        if (replan + reflect + stagn) >= 1:
            reasons.append(
                f"guards=replan{replan}+reflect{reflect}+stagn{stagn}"
            )
        if len(kinds) >= 4:
            reasons.append(f"tool_kinds={len(kinds)}")
        if fail_then_ok:
            reasons.append("fail_then_ok")
        if reasons:
            return {
                "eligible": True,
                "kind": "success",
                "reason": ";".join(reasons),
                "rounds": rounds,
                "tool_kinds": sorted(kinds),
            }
        return {
            "eligible": False,
            "kind": "success",
            "reason": "simple_success",
            "rounds": rounds,
            "tool_kinds": sorted(kinds),
        }

    if rounds >= 2 and has_narrative:
        return {
            "eligible": True,
            "kind": "failure",
            "reason": "failed_with_narrative",
            "rounds": rounds,
            "tool_kinds": sorted(kinds),
        }
    return {
        "eligible": False,
        "kind": "failure",
        "reason": "failed_thin",
        "rounds": rounds,
        "tool_kinds": sorted(kinds),
    }


def _sample_steps(steps: list[Any], *, head: int = 4, tail: int = 4) -> list[Any]:
    """头尾采样；中间用占位，禁止只喂前缀。"""
    n = len(steps or [])
    if n <= head + tail:
        return list(steps or [])
    mid = n - head - tail
    out = list(steps[:head])
    out.append(
        {
            "plan": {
                "thought": f"…省略中间 {mid} 步（头尾采样）…",
                "status": "continue",
                "actions": [],
            },
            "results": [],
        }
    )
    out.extend(steps[-tail:])
    return out


def _compact_step(st: dict[str, Any]) -> dict[str, Any]:
    plan = st.get("plan") if isinstance(st.get("plan"), dict) else {}
    actions_out = []
    for a in plan.get("actions") or []:
        if not isinstance(a, dict):
            continue
        args = dict(a.get("args") or {})
        # 截断过长 cmd / 正文
        if "cmd" in args and isinstance(args["cmd"], str) and len(args["cmd"]) > 240:
            args["cmd"] = args["cmd"][:240] + "…"
        for k in ("content", "text", "body"):
            if k in args and isinstance(args[k], str) and len(args[k]) > 200:
                args[k] = args[k][:200] + "…"
        actions_out.append({"kind": a.get("kind"), "args": args})
    results_out = []
    for r in st.get("results") or []:
        if not isinstance(r, dict):
            continue
        msg = str(r.get("message") or "")
        if len(msg) > 280:
            msg = msg[:280] + "…"
        results_out.append({"ok": r.get("ok"), "message": msg})
    return {
        "thought": str(plan.get("thought") or "")[:240],
        "status": plan.get("status"),
        "actions": actions_out,
        "results": results_out,
    }


def _redact(text: str) -> str:
    """蒸馏材料与短卡进 lore 前强制脱敏（规划侧 redact_secrets 是透传）。"""
    try:
        from fawai_avatar.channel import redact as redact_mod

        impl = getattr(redact_mod, "_redact_secrets_impl", None)
        if callable(impl):
            return str(impl(text or ""))
        return str(redact_mod.redact_for_client(text or ""))
    except Exception:
        return text or ""


def build_distill_materials(
    *,
    goal: str,
    ok: bool,
    meta: dict[str, Any],
    steps: list[Any] | None = None,
) -> str:
    st = list(steps if steps is not None else (meta.get("steps") or []))
    sampled = [_compact_step(s) if isinstance(s, dict) else s for s in _sample_steps(st)]
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    reflection = meta.get("reflection")
    blob = {
        "goal": goal,
        "ok": ok,
        "final_thought": str(meta.get("final_thought") or "")[:800],
        "error": str(meta.get("error") or "")[:400],
        "replan_count": meta.get("replan_count"),
        "reflect_count": meta.get("reflect_count"),
        "stagnation_count": meta.get("stagnation_count"),
        "stats": {
            k: stats.get(k)
            for k in ("rounds", "llm_calls", "tools", "prompt_tokens", "completion_tokens")
            if k in stats
        },
        "reflection": reflection if isinstance(reflection, dict) else None,
        "steps_sampled": sampled,
    }
    return _redact(json.dumps(blob, ensure_ascii=False, indent=2))


def distill_system_prompt(*, kind: str) -> str:
    if kind == "failure":
        return (
            "你是任务终态短卡蒸馏器。从材料提炼**一张**失败避坑卡（不是整段轨迹）。\n"
            "要求：换掉主机名/绝对路径专名后仍可复用；禁止口令/Token/带密 ssh 命令。\n"
            "goal_slice / symptom / cause / do_not_retry 必须是给人读的短句（建议≤40字），"
            "禁止 true/false/null/空布尔；它们会直接用作知识库标题。\n"
            "只输出一个 JSON 对象，字段：\n"
            "goal_slice, symptom, cause, do_not_retry, recovery, "
            "commands_tried (数组), config_refs (数组), evidence, tool_id\n"
            "config_refs 项可用 {path, keys, excerpt}；excerpt 须非密。\n"
        )
    return (
        "你是任务终态短卡蒸馏器。从材料提炼**一张**成功复用卡（不是整段轨迹）。\n"
        "聚焦：多法试错后真正奏效的路径、配置落点、命令族、勿再踩的坑。\n"
        "要求：换掉主机名/单文件专名后仍成立；禁止口令/Token/带密 ssh 命令。\n"
        "acceptance / goal_slice 必须是给人读的验收短句（建议≤40字），"
        "禁止 true/false/null/空布尔；它们会直接用作知识库标题。\n"
        "只输出一个 JSON 对象，字段：\n"
        "acceptance, goal_slice, commands (数组), config_refs (数组), "
        "code_refs (数组), targets (数组), evidence, avoid, tool_id, source_refs (数组)\n"
        "config_refs 项可用 {path, keys, excerpt}；excerpt 须非密。\n"
    )


def distill_card_with_llm(
    *,
    kind: str,
    materials: str,
    on_delta: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """一次短卡 LLM；返回 {ok, card?, error?, usage?, elapsed_ms?, model?}。"""
    from fawai_avatar.llm.client import LlmClient

    prompt = distill_system_prompt(kind=kind) + f"\n材料 JSON：\n{materials}\n"
    t0 = time.perf_counter()
    result = LlmClient().chat(
        [{"role": "user", "content": prompt}],
        tools=None,
        route_role="fast",
        thinking_role="fast",
        enable_thinking=False,
        on_delta=on_delta,
    )
    elapsed_ms = max(0, int((time.perf_counter() - t0) * 1000))
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    model = result.get("model")
    if not result.get("ok"):
        return {
            "ok": False,
            "error": str(result.get("error") or "llm failed"),
            "elapsed_ms": elapsed_ms,
            "usage": usage or None,
            "model": model,
        }
    parsed = parse_gate_json(str(result.get("content") or ""))
    if not parsed:
        return {
            "ok": False,
            "error": "无法解析蒸馏 JSON",
            "raw": str(result.get("content") or "")[:500],
            "elapsed_ms": elapsed_ms,
            "usage": usage or None,
            "model": model,
        }
    # 脱敏整卡
    card = json.loads(_redact(json.dumps(parsed, ensure_ascii=False)))
    if not isinstance(card, dict):
        return {
            "ok": False,
            "error": "蒸馏结果非对象",
            "elapsed_ms": elapsed_ms,
            "usage": usage or None,
            "model": model,
        }
    card["tool_id"] = str(card.get("tool_id") or "task_distill")[:64]
    return {
        "ok": True,
        "card": card,
        "elapsed_ms": elapsed_ms,
        "usage": usage or None,
        "model": model,
    }


def _notify(
    on_progress: ProgressFn | None,
    text: str,
    *,
    status: str,
    **extra: Any,
) -> None:
    if on_progress is None:
        return
    meta: dict[str, Any] = {"phase": PHASE, "status": status}
    meta.update({k: v for k, v in extra.items() if v is not None})
    try:
        on_progress(text, meta)
    except Exception:
        pass


def run_task_distill(
    *,
    data_dir: str,
    task_id: str,
    goal: str,
    ok: bool,
    meta: dict[str, Any],
    steps: list[Any] | None = None,
    on_progress: ProgressFn | None = None,
    on_usage: UsageFn | None = None,
    llm_distill: Callable[..., dict[str, Any]] | None = None,
    run_gate: bool = True,
    gate_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    终态蒸馏主入口。失败不抛；返回可写入 ``meta.task_distill`` 的摘要。
    """
    tid = (task_id or "").strip()
    out: dict[str, Any] = {
        "ran": False,
        "skipped": False,
        "ok": False,
        "ts": time.time(),
    }
    if not distill_enabled():
        out.update({"skipped": True, "reason": "distill_off"})
        return out
    if not tid:
        out.update({"skipped": True, "reason": "no_task_id"})
        return out
    if task_already_promoted(data_dir, tid):
        out.update({"skipped": True, "reason": "already_promoted"})
        return out

    elig = eligible_for_distill(ok=ok, meta=meta, steps=steps)
    out["eligible"] = elig
    if not elig.get("eligible"):
        out.update({"skipped": True, "reason": elig.get("reason") or "not_eligible"})
        return out

    kind = str(elig.get("kind") or ("success" if ok else "failure"))
    _notify(on_progress, "终态短卡蒸馏：提炼中…", status="running", distill_kind=kind)
    materials = build_distill_materials(goal=goal, ok=ok, meta=meta, steps=steps)
    distill_fn = llm_distill or distill_card_with_llm
    distilled = distill_fn(kind=kind, materials=materials)
    if on_usage and isinstance(distilled.get("usage"), dict):
        try:
            on_usage(distilled.get("usage"), int(distilled.get("elapsed_ms") or 0))
        except Exception:
            pass
    if not distilled.get("ok"):
        out.update(
            {
                "ran": True,
                "ok": False,
                "kind": kind,
                "error": distilled.get("error") or "distill failed",
                "elapsed_ms": distilled.get("elapsed_ms"),
            }
        )
        _notify(
            on_progress,
            f"终态短卡蒸馏失败：{out['error']}"[:160],
            status="done",
            distill_kind=kind,
        )
        return out

    card = dict(distilled.get("card") or {})
    try:
        card = json.loads(_redact(json.dumps(card, ensure_ascii=False)))
        if not isinstance(card, dict):
            card = dict(distilled.get("card") or {})
    except Exception:
        card = dict(distilled.get("card") or {})
    card["evaluate"] = True
    notify = GateNotify(
        on_progress=on_progress,
        on_usage=on_usage,
        phase=PHASE,
    )
    try:
        if kind == "failure":
            from fawai_avatar.brain import failure_ledger as ledger

            entry = ledger.upsert_entry(
                data_dir,
                tid,
                card,
                schedule_gate=False,
                notify=notify,
            )
            eval_gate = gate_fn or ledger.evaluate_gate_with_llm
            apply = ledger.apply_gate_result
        else:
            from fawai_avatar.brain import success_ledger as ledger

            entry = ledger.upsert_entry(
                data_dir,
                tid,
                card,
                schedule_gate=False,
                notify=notify,
            )
            eval_gate = gate_fn or ledger.evaluate_gate_with_llm
            apply = ledger.apply_gate_result
    except Exception as exc:  # noqa: BLE001
        out.update(
            {
                "ran": True,
                "ok": False,
                "kind": kind,
                "error": f"ledger upsert failed: {exc}",
            }
        )
        _notify(on_progress, out["error"][:160], status="done", distill_kind=kind)
        return out

    eid = str(entry.get("id") or "")
    out.update(
        {
            "ran": True,
            "ok": True,
            "kind": kind,
            "entry_id": eid,
            "distill_elapsed_ms": distilled.get("elapsed_ms"),
            "distill_model": distilled.get("model"),
        }
    )

    if not run_gate:
        out["gate_status"] = "pending"
        _notify(
            on_progress,
            f"终态短卡已写入台账（{eid}），门控稍后",
            status="done",
            distill_kind=kind,
            entry_id=eid,
        )
        return out

    _notify(
        on_progress,
        f"终态短卡蒸馏：三维评估中… {eid}",
        status="running",
        distill_kind=kind,
        entry_id=eid,
    )
    try:
        gate = eval_gate(entry)
        if on_usage and isinstance(gate.get("usage"), dict):
            try:
                on_usage(gate.get("usage"), int(gate.get("elapsed_ms") or 0))
            except Exception:
                pass
        applied = apply(data_dir, tid, eid, gate)
        entry2 = (applied.get("entry") if isinstance(applied, dict) else None) or {}
        lore_id = entry2.get("lore_id") if isinstance(entry2, dict) else None
        out["gate_status"] = str(gate.get("status") or "")
        out["gate_pass"] = bool(gate.get("pass"))
        out["lore_id"] = lore_id
        if lore_id:
            msg = f"终态短卡已晋升 lore（{lore_id}）"
        elif gate.get("pass"):
            msg = "终态短卡过门（lore 写入待确认）"
        else:
            msg = "终态短卡未过门，仅留本任务台账"
        rationale = str(gate.get("rationale") or gate.get("error") or "").strip()
        if rationale:
            msg = f"{msg} — {rationale[:120]}"
        _notify(
            on_progress,
            msg,
            status="done",
            distill_kind=kind,
            entry_id=eid,
            lore_id=lore_id,
            gate_pass=bool(gate.get("pass")),
        )
    except Exception as exc:  # noqa: BLE001
        out["gate_error"] = str(exc)
        _notify(
            on_progress,
            f"终态短卡门控失败：{exc}"[:160],
            status="done",
            distill_kind=kind,
            entry_id=eid,
        )
    return out


def parse_distill_json_for_tests(content: str) -> dict[str, Any] | None:
    """测试辅助：与门控相同的 JSON 抽取。"""
    return parse_gate_json(content)


# 供启发式单测直接引用，避免循环导入噪音
_FAIL_THEN_OK = _had_fail_then_ok
_SAMPLE = _sample_steps
