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


def _clip_text(text: str, max_chars: int) -> str:
    """超长保留头+尾（大文件取证常看尾部指标，禁止只留前缀）。"""
    from fawai_avatar.observe.tool_digest import clip_head_tail

    clipped, _ = clip_head_tail(text, max_chars=max_chars)
    return clipped


def dump_checkpoint(state: dict[str, Any]) -> dict[str, Any]:
    """序列化可变闭环状态（调用方保证字段齐全）。"""
    history = [_clip_history_item(m) for m in (state.get("history") or [])]
    traces = []
    for i, t in enumerate(state.get("traces") or []):
        clipped = _clip_trace(t)
        if "step" not in clipped:
            clipped["step"] = int(i)
        traces.append(clipped)
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
        "explore_behavior_armed": bool(state.get("explore_behavior_armed")),
        "remote_idle_streak": int(state.get("remote_idle_streak") or 0),
        "remote_method_forced": bool(state.get("remote_method_forced")),
        "read_path_counts": {
            str(k): int(v)
            for k, v in dict(state.get("read_path_counts") or {}).items()
        },
        "read_windows": _dump_read_windows(state.get("read_windows")),
        "locate_anchors": [
            str(x) for x in (state.get("locate_anchors") or []) if str(x).strip()
        ][:24],
        "post_locate_streak": int(state.get("post_locate_streak") or 0),
        "locate_mutate_forced": bool(state.get("locate_mutate_forced")),
        "wide_grep_ban_paths": [
            str(x) for x in (state.get("wide_grep_ban_paths") or []) if str(x).strip()
        ][:16],
        "wide_grep_ban_patterns": {
            str(k).strip(): str(v)[:200]
            for k, v in dict(state.get("wide_grep_ban_patterns") or {}).items()
            if str(k).strip() and str(v).strip()
        },
        "awaiting_user_answer": bool(state.get("awaiting_user_answer")),
        "pending_observe_kinds": list(state.get("pending_observe_kinds") or []) or None,
        "pending_observe_args": (
            dict(state.get("pending_observe_args") or {})
            if isinstance(state.get("pending_observe_args"), dict)
            and state.get("pending_observe_args")
            else None
        ),
        "pending_step_kind": (
            str(state.get("pending_step_kind") or "").strip() or None
        ),
        "last_reflection": state.get("last_reflection"),
        "history_compact": (
            {
                "prefix_hash": str((state.get("history_compact") or {}).get("prefix_hash") or "")[:32],
                "text": str((state.get("history_compact") or {}).get("text") or "")[:1500],
            }
            if isinstance(state.get("history_compact"), dict)
            and str((state.get("history_compact") or {}).get("text") or "").strip()
            else {}
        ),
        "step_todos": [
            {"text": str(item.get("text") or "")[:40], "status": str(item.get("status") or "")}
            for item in (state.get("step_todos") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ][:8],
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


def history_from_step_dicts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由 traces / meta.steps 粗合成规划 history（步骤恢复用）。"""
    history: list[dict[str, Any]] = []
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
        thought = str(plan.get("thought") or "").strip()
        if thought:
            history.append(
                {
                    "role": "assistant",
                    "content": _clip_text(f"[step {i}] {thought}", _HISTORY_CONTENT_MAX),
                }
            )
        for r in raw.get("results") or []:
            if not isinstance(r, dict):
                continue
            ok = bool(r.get("ok"))
            msg = str(r.get("message") or "").strip()
            line = f"工具结果：{'成功' if ok else '失败'}"
            if msg:
                line = f"{line} {msg}"
            history.append(
                {"role": "user", "content": _clip_text(line, _HISTORY_CONTENT_MAX)}
            )
    return history


def _trace_step_index(item: dict[str, Any], fallback: int) -> int:
    """traces / meta.steps 条目上的步号；无字段则用列表下标。"""
    raw = item.get("step")
    if raw is None:
        return int(fallback)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(fallback)


def excise_step_from_context(
    meta: dict[str, Any] | None, *, step: int
) -> dict[str, Any]:
    """从规划上下文挖掉指定闭环步（保留更早与更晚；步号空洞保留）。

    返回可写回任务 meta 的补丁：{run_checkpoint, steps, excised_step}。
    不自动拉 Worker。步号按条目 `step` 字段匹配；无字段则按列表下标。
    """
    import uuid

    step_n = int(step)
    if step_n < 0:
        raise ValueError("step must be >= 0")

    base = dict(meta or {})
    existing = load_checkpoint(base)
    traces_src: list[dict[str, Any]] = []
    if existing is not None:
        traces_src = [t for t in (existing.get("traces") or []) if isinstance(t, dict)]
    if not traces_src:
        traces_src = [s for s in (base.get("steps") or []) if isinstance(s, dict)]

    kept_traces: list[dict[str, Any]] = []
    found = False
    for i, raw in enumerate(traces_src):
        idx = _trace_step_index(raw, i)
        if idx == step_n:
            found = True
            continue
        # 保留原 step 编号（空洞）；无字段则补上当前下标含义的编号
        item = dict(raw)
        if item.get("step") is None:
            item["step"] = idx
        kept_traces.append(item)

    if not found:
        raise KeyError(f"step {step_n} not found")

    meta_steps = [s for s in (base.get("steps") or []) if isinstance(s, dict)]
    kept_meta_steps: list[dict[str, Any]] = []
    for i, raw in enumerate(meta_steps):
        idx = _trace_step_index(raw, i)
        if idx == step_n:
            continue
        item = dict(raw)
        if item.get("step") is None:
            item["step"] = idx
        kept_meta_steps.append(item)

    old_next = int((existing or {}).get("next_step") or 0) if existing else 0
    kept_nums = [_trace_step_index(t, i) for i, t in enumerate(kept_traces)]
    if old_next == step_n:
        new_next = (max(kept_nums) + 1) if kept_nums else 0
    elif step_n < old_next:
        # 删的是 next 之前的一步：next 编号本身仍合法则保留；否则收到 max+1
        if old_next in kept_nums or not kept_nums:
            new_next = old_next
        else:
            new_next = max(kept_nums) + 1 if kept_nums else 0
    else:
        new_next = old_next

    state: dict[str, Any]
    if existing is not None:
        state = dict(existing)
    else:
        state = {
            "run_id": uuid.uuid4().hex,
            "replan_count": 0,
            "reflect_count": 0,
            "stagnation_count": 0,
            "fingerprint_fails": {},
            "prior_alternate_fps": [],
            "step_fp_counts": {},
            "prior_judged_step_fps": [],
            "verify_results": [],
            "write_set": list(base.get("write_set") or []),
            "saw_code_check_ok": False,
            "saw_architecture_ok": False,
            "explore_only_streak": 0,
            "explore_mutate_forced": False,
            "explore_behavior_armed": False,
            "remote_idle_streak": 0,
            "remote_method_forced": False,
            "read_path_counts": {},
            "read_windows": {},
            "locate_anchors": [],
            "post_locate_streak": 0,
            "locate_mutate_forced": False,
            "wide_grep_ban_paths": [],
            "wide_grep_ban_patterns": {},
            "last_reflection": None,
        }
    if not state.get("run_id"):
        state["run_id"] = uuid.uuid4().hex
    state["next_step"] = max(0, int(new_next))
    state["traces"] = kept_traces
    state["history"] = history_from_step_dicts(kept_traces)
    # 可重建字段：清空指纹计数，避免对着已删步的脏计数
    state["step_fp_counts"] = {}
    state["fingerprint_fails"] = {}
    state["awaiting_user_answer"] = False
    state["pending_observe_kinds"] = None
    state["pending_observe_args"] = None
    state["pending_step_kind"] = None

    return {
        CHECKPOINT_KEY: dump_checkpoint(state),
        "steps": kept_meta_steps,
        "excised_step": step_n,
        "next_step": state["next_step"],
    }


def prepare_resume_from_step(meta: dict[str, Any] | None, *, step: int) -> dict[str, Any]:
    """构造从步骤 `step` 继续的 run_checkpoint。

    优先截断已有检查点；否则用 meta.steps（终态落盘、最多尾部若干步）粗合成。
    """
    import uuid

    step_n = max(0, int(step))
    existing = load_checkpoint(meta)
    if existing is not None:
        traces_raw = [t for t in (existing.get("traces") or []) if isinstance(t, dict)]
        # 优先按步号字段截断；无字段则回退列表下标（兼容旧检查点）
        kept: list[dict[str, Any]] = []
        for i, t in enumerate(traces_raw):
            idx = _trace_step_index(t, i)
            if idx >= step_n:
                break
            kept.append(t)
        # 若全部无有效 step 字段且 break 未触发（全 < step_n），上面逻辑已正确；
        # 若条目无 step 且用下标：idx==i，与 traces_raw[:step_n] 等价。
        state = dict(existing)
        state["next_step"] = step_n
        state["traces"] = kept
        state["history"] = history_from_step_dicts(kept) or list(
            existing.get("history") or []
        )
        state["awaiting_user_answer"] = False
        state["pending_observe_kinds"] = None
        state["pending_observe_args"] = None
        state["pending_step_kind"] = None
        if not state.get("run_id"):
            state["run_id"] = uuid.uuid4().hex
        return dump_checkpoint(state)

    steps = [s for s in ((meta or {}).get("steps") or []) if isinstance(s, dict)]
    # meta.steps 为尾部裁剪：步号超出长度时仍用全部作先验，next_step 用用户指定值
    prior = steps[:step_n] if step_n <= len(steps) else steps
    return dump_checkpoint(
        {
            "run_id": uuid.uuid4().hex,
            "next_step": step_n,
            "history": history_from_step_dicts(prior),
            "traces": prior,
            "replan_count": 0,
            "reflect_count": 0,
            "stagnation_count": 0,
            "fingerprint_fails": {},
            "prior_alternate_fps": [],
            "step_fp_counts": {},
            "prior_judged_step_fps": [],
            "verify_results": [],
            "write_set": list((meta or {}).get("write_set") or []),
            "saw_code_check_ok": False,
            "saw_architecture_ok": False,
            "explore_only_streak": 0,
            "explore_mutate_forced": False,
            "explore_behavior_armed": False,
            "remote_idle_streak": 0,
            "remote_method_forced": False,
            "read_path_counts": {},
            "read_windows": {},
            "locate_anchors": [],
            "post_locate_streak": 0,
            "locate_mutate_forced": False,
            "wide_grep_ban_paths": [],
            "wide_grep_ban_patterns": {},
            "awaiting_user_answer": False,
            "pending_observe_kinds": None,
            "pending_observe_args": None,
            "pending_step_kind": None,
            "last_reflection": None,
        }
    )


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
        "explore_behavior_armed": bool(raw.get("explore_behavior_armed")),
        "remote_idle_streak": int(raw.get("remote_idle_streak") or 0),
        "remote_method_forced": bool(raw.get("remote_method_forced")),
        "read_path_counts": {
            str(k): int(v)
            for k, v in dict(raw.get("read_path_counts") or {}).items()
        },
        "read_windows": _load_read_windows(raw.get("read_windows")),
        "locate_anchors": [
            str(x) for x in (raw.get("locate_anchors") or []) if str(x).strip()
        ][:24],
        "post_locate_streak": int(raw.get("post_locate_streak") or 0),
        "locate_mutate_forced": bool(raw.get("locate_mutate_forced")),
        "wide_grep_ban_paths": [
            str(x) for x in (raw.get("wide_grep_ban_paths") or []) if str(x).strip()
        ][:16],
        "wide_grep_ban_patterns": {
            str(k).strip(): str(v)
            for k, v in dict(raw.get("wide_grep_ban_patterns") or {}).items()
            if str(k).strip() and str(v).strip()
        },
        "awaiting_user_answer": bool(raw.get("awaiting_user_answer")),
        "pending_observe_kinds": pending_kinds,
        "pending_observe_args": (
            dict(raw.get("pending_observe_args") or {})
            if isinstance(raw.get("pending_observe_args"), dict)
            and raw.get("pending_observe_args")
            else None
        ),
        "pending_step_kind": (
            str(raw.get("pending_step_kind") or "").strip() or None
        ),
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
    content = _clip_text(str(item.get("content") or ""), _HISTORY_CONTENT_MAX)
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
                        (
                            a.get("write_resources")
                            if isinstance(a, dict)
                            else getattr(a, "write_resources", None)
                        )
                        or []
                    ),
                }
                for a in (plan.get("actions") or [])
            ],
            "observe_kinds": list(plan.get("observe_kinds") or []),
            "observe_args": dict(plan.get("observe_args") or {})
            if isinstance(plan.get("observe_args"), dict)
            else {},
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
    out: dict[str, Any] = {
        "observation": obs_d,
        "plan": plan_d or None,
        "results": res_out,
    }
    # 保留步号（挖步 / 恢复按编号匹配；无字段则调用方用列表下标）
    step_raw = None
    if isinstance(tr, dict):
        step_raw = tr.get("step")
    else:
        step_raw = getattr(tr, "step", None)
    if step_raw is not None:
        try:
            out["step"] = int(step_raw)
        except (TypeError, ValueError):
            pass
    return out


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
    raw_delegate = data.get("delegate")
    delegate = dict(raw_delegate) if isinstance(raw_delegate, dict) else None
    observe_args = (
        dict(data.get("observe_args") or {})
        if isinstance(data.get("observe_args"), dict)
        else {}
    )
    return Plan(
        thought=str(data.get("thought") or ""),
        actions=actions,
        done=bool(data.get("done")),
        status=str(data.get("status") or "continue"),
        raw=dict(data.get("raw") or {}) if isinstance(data.get("raw"), dict) else {},
        observe_kinds=[str(x) for x in (data.get("observe_kinds") or [])],
        observe_args=observe_args,
        title=str(data.get("title") or ""),
        step_label=str(data.get("step_label") or ""),
        step_kind=str(data.get("step_kind") or ""),
        step_run=str(data.get("step_run") or ""),
        delegate=delegate,
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
