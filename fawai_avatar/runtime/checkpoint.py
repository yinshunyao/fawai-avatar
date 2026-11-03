# term:进程中断续跑  doc:doc/功能/进程中断续跑/说明.md
"""工作任务闭环检查点：进程重启后从断点继续，而非整任务重开。"""
from __future__ import annotations

from typing import Any

from fawai_avatar.types import Action, ExecResult, Observation, Plan

CHECKPOINT_KEY = "run_checkpoint"
CHECKPOINT_VERSION = 1

# 启动后应自动拉 Worker 的状态（paused 除外）
RECOVER_STATUSES: frozenset[str] = frozenset(
    {"running", "waiting", "blocked", "pending"}
)

_HISTORY_CONTENT_MAX = 8000
_TRACE_THOUGHT_MAX = 800
_TRACE_MSG_MAX = 400


def dump_checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    """序列化可变闭环状态（调用方保证字段齐全）。"""
    history = [_clip_history_item(m) for m in (state.get("history") or [])]
    traces = [_clip_trace(t) for t in (state.get("traces") or [])]
    verify = []
    for item in state.get("verify_results") or []:
        if isinstance(item, ExecResult):
            verify.append(
                {
                    "ok": item.ok,
                    "message": str(item.message or "")[:_TRACE_MSG_MAX],
                    "data": {},
                }
            )
        elif isinstance(item, dict):
            verify.append(
                {
                    "ok": bool(item.get("ok")),
                    "message": str(item.get("message") or "")[:_TRACE_MSG_MAX],
                    "data": {},
                }
            )
    return {
        "version": CHECKPOINT_VERSION,
        "run_id": state.get("run_id"),
        "next_step": int(state.get("next_step") or 0),
        "history": history,
        "traces": traces,
        "replan_count": int(state.get("replan_count") or 0),
        "reflect_count": int(state.get("reflect_count") or 0),
        "stagnation_count": int(state.get("stagnation_count") or 0),
        "fingerprint_fails": {
            str(k): int(v) for k, v in dict(state.get("fingerprint_fails") or {}).items()
        },
        "prior_alternate_fps": [str(x) for x in (state.get("prior_alternate_fps") or [])],
        "step_fp_counts": {
            str(k): int(v) for k, v in dict(state.get("step_fp_counts") or {}).items()
        },
        "prior_judged_step_fps": [
            str(x) for x in (state.get("prior_judged_step_fps") or [])
        ],
        "verify_results": verify,
        "write_set": [str(x) for x in (state.get("write_set") or [])],
        "saw_code_check_ok": bool(state.get("saw_code_check_ok")),
        "saw_architecture_ok": bool(state.get("saw_architecture_ok")),
        "explore_only_streak": int(state.get("explore_only_streak") or 0),
        "explore_mutate_forced": bool(state.get("explore_mutate_forced")),
        "read_path_counts": {
            str(k): int(v)
            for k, v in dict(state.get("read_path_counts") or {}).items()
        },
        "read_windows": _dump_read_windows(state.get("read_windows")),
        "locate_anchors": [
            str(x) for x in (state.get("locate_anchors") or []) if str(x).strip()
        ][:24],
        "post_locate_streak": int(state.get("post_locate_streak") or 0),
        "wide_grep_ban_paths": [
            str(x) for x in (state.get("wide_grep_ban_paths") or []) if str(x).strip()
        ][:16],
        "awaiting_user_answer": bool(state.get("awaiting_user_answer")),
        "pending_observe_kinds": list(state.get("pending_observe_kinds") or []) or None,
        "last_reflection": state.get("last_reflection"),
    }


def load_checkpoint(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    raw = (meta or {}).get(CHECKPOINT_KEY)
    if not isinstance(raw, dict):
        return None
    if int(raw.get("version") or 0) != CHECKPOINT_VERSION:
        return None
    return raw


def clear_checkpoint_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    out = dict(meta or {})
    out.pop(CHECKPOINT_KEY, None)
    return out


def restore_runtime_state(raw: dict[str, Any]) -> dict[str, Any]:
    """检查点 → 可灌入 WorkerLoop 的字段（traces 为 dict，由 loop 转 StepTrace）。"""
    from collections import Counter

    history = []
    for m in raw.get("history") or []:
        if isinstance(m, dict) and m.get("role"):
            history.append(
                {"role": str(m.get("role")), "content": str(m.get("content") or "")}
            )
    traces_raw = [item for item in (raw.get("traces") or []) if isinstance(item, dict)]
    pending = raw.get("pending_observe_kinds")
    if isinstance(pending, list):
        pending_kinds = [str(x) for x in pending] or None
    else:
        pending_kinds = None
    return {
        "run_id": str(raw.get("run_id") or "") or None,
        "next_step": max(0, int(raw.get("next_step") or 0)),
        "history": history,
        "traces_raw": traces_raw,
        "replan_count": int(raw.get("replan_count") or 0),
        "reflect_count": int(raw.get("reflect_count") or 0),
        "stagnation_count": int(raw.get("stagnation_count") or 0),
        "fingerprint_fails": Counter(
            {str(k): int(v) for k, v in dict(raw.get("fingerprint_fails") or {}).items()}
        ),
        "prior_alternate_fps": set(str(x) for x in (raw.get("prior_alternate_fps") or [])),
        "step_fp_counts": Counter(
            {str(k): int(v) for k, v in dict(raw.get("step_fp_counts") or {}).items()}
        ),
        "prior_judged_step_fps": set(
            str(x) for x in (raw.get("prior_judged_step_fps") or [])
        ),
        "verify_results": [
            _result_from_dict(r) for r in (raw.get("verify_results") or [])
        ],
        "write_set": [str(x) for x in (raw.get("write_set") or [])],
        "saw_code_check_ok": bool(raw.get("saw_code_check_ok")),
        "saw_architecture_ok": bool(raw.get("saw_architecture_ok")),
        "explore_only_streak": int(raw.get("explore_only_streak") or 0),
        "explore_mutate_forced": bool(raw.get("explore_mutate_forced")),
        "read_path_counts": {
            str(k): int(v)
            for k, v in dict(raw.get("read_path_counts") or {}).items()
        },
        "read_windows": _load_read_windows(raw.get("read_windows")),
        "locate_anchors": [
            str(x) for x in (raw.get("locate_anchors") or []) if str(x).strip()
        ][:24],
        "post_locate_streak": int(raw.get("post_locate_streak") or 0),
        "wide_grep_ban_paths": [
            str(x) for x in (raw.get("wide_grep_ban_paths") or []) if str(x).strip()
        ][:16],
        "awaiting_user_answer": bool(raw.get("awaiting_user_answer")),
        "pending_observe_kinds": pending_kinds,
        "last_reflection": raw.get("last_reflection")
        if isinstance(raw.get("last_reflection"), dict)
        else None,
    }


def step_trace_parts(item: dict[str, Any]) -> tuple[Observation | None, Plan | None, list[ExecResult]]:
    """单条 traces_raw → Observation / Plan / results。"""
    return (
        _obs_from_dict(item.get("observation")),
        _plan_from_dict(item.get("plan")),
        [_result_from_dict(r) for r in (item.get("results") or [])],
    )


def _dump_read_windows(raw: Any) -> dict[str, list[list[int]]]:
    out: dict[str, list[list[int]]] = {}
    if not isinstance(raw, dict):
        return out
    for path, wins in raw.items():
        key = str(path or "").strip()
        if not key or not isinstance(wins, list):
            continue
        items: list[list[int]] = []
        for item in wins:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                items.append([int(item[0]), int(item[1])])
            except (TypeError, ValueError):
                continue
        if items:
            out[key] = items[-16:]
    return out


def _load_read_windows(raw: Any) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    if not isinstance(raw, dict):
        return out
    for path, wins in raw.items():
        key = str(path or "").strip()
        if not key or not isinstance(wins, list):
            continue
        items: list[tuple[int, int]] = []
        for item in wins:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                items.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
        if items:
            out[key] = items[-16:]
    return out


def _clip_history_item(item: dict[str, Any]) -> dict[str, Any]:
    content = str(item.get("content") or "")
    if len(content) > _HISTORY_CONTENT_MAX:
        content = content[: _HISTORY_CONTENT_MAX - 1] + "…"
    return {"role": str(item.get("role") or "user"), "content": content}


def _clip_trace(tr: Any) -> dict[str, Any]:
    """接受 StepTrace 或已是 dict。"""
    if isinstance(tr, dict):
        obs = tr.get("observation")
        plan = tr.get("plan")
        results = tr.get("results") or []
    else:
        obs = tr.observation.to_dict() if getattr(tr, "observation", None) else None
        plan = tr.plan.to_dict() if getattr(tr, "plan", None) else None
        results = [r.to_dict() for r in (getattr(tr, "results", None) or [])]
    plan_d: dict[str, Any] = {}
    if isinstance(plan, dict):
        plan_d = {
            "thought": str(plan.get("thought") or "")[:_TRACE_THOUGHT_MAX],
            "status": plan.get("status"),
            "done": plan.get("done"),
            "actions": [
                {
                    "kind": a.get("kind") if isinstance(a, dict) else getattr(a, "kind", None),
                    "args": (a.get("args") if isinstance(a, dict) else getattr(a, "args", {}))
                    or {},
                    "needs_foreground": bool(
                        a.get("needs_foreground")
                        if isinstance(a, dict)
                        else getattr(a, "needs_foreground", False)
                    ),
                    "write_resources": list(
                        a.get("write_resources")
                        if isinstance(a, dict)
                        else getattr(a, "write_resources", [])
                        or []
                    ),
                }
                for a in (plan.get("actions") or [])
            ],
            "observe_kinds": list(plan.get("observe_kinds") or []),
        }
    obs_d = None
    if isinstance(obs, dict):
        obs_d = {
            "summary": str(obs.get("summary") or "")[:_TRACE_THOUGHT_MAX],
            "image_path": obs.get("image_path"),
            "width": obs.get("width"),
            "height": obs.get("height"),
            "meta": {},
        }
    res_out = []
    for r in results:
        if isinstance(r, dict):
            res_out.append(
                {
                    "ok": bool(r.get("ok")),
                    "message": str(r.get("message") or "")[:_TRACE_MSG_MAX],
                    "data": {},
                }
            )
        else:
            res_out.append(
                {
                    "ok": bool(getattr(r, "ok", False)),
                    "message": str(getattr(r, "message", "") or "")[:_TRACE_MSG_MAX],
                    "data": {},
                }
            )
    return {"observation": obs_d, "plan": plan_d or None, "results": res_out}


def _obs_from_dict(data: Any) -> Observation | None:
    if not isinstance(data, dict):
        return None
    return Observation(
        summary=str(data.get("summary") or ""),
        image_path=data.get("image_path"),
        width=data.get("width"),
        height=data.get("height"),
        meta=dict(data.get("meta") or {}),
    )


def _plan_from_dict(data: Any) -> Plan | None:
    if not isinstance(data, dict):
        return None
    actions = []
    for a in data.get("actions") or []:
        if isinstance(a, dict):
            actions.append(Action.from_dict(a))
    return Plan(
        thought=str(data.get("thought") or ""),
        actions=actions,
        done=bool(data.get("done")),
        status=str(data.get("status") or "continue"),
        raw={},
        observe_kinds=[str(x) for x in (data.get("observe_kinds") or [])],
    )


def _result_from_dict(data: Any) -> ExecResult:
    if isinstance(data, ExecResult):
        return data
    if not isinstance(data, dict):
        return ExecResult(ok=False, message="invalid result")
    return ExecResult(
        ok=bool(data.get("ok")),
        message=str(data.get("message") or ""),
        data=dict(data.get("data") or {}),
    )
