# term:工作任务  doc:doc/议题/操作分身三层/结论.md
# term:会话通道  doc:doc/功能/会话通道/说明.md
# term:执行反省  doc:doc/功能/执行反省/说明.md
# term:观察反省  doc:doc/功能/观察反省/说明.md
# term:步骤分类与执行流  doc:doc/功能/步骤分类与执行流/说明.md
# term:步骤重复  doc:doc/功能/步骤重复/说明.md
# term:进程中断续跑  doc:doc/功能/进程中断续跑/说明.md
# term:守规约束  doc:doc/功能/守规约束/说明.md
# term:探索进度闸门  doc:doc/功能/编码闭环/说明.md
# term:弱写入闸门  doc:doc/功能/编码闭环/说明.md
# term:可见副作用闸门  doc:doc/功能/编码闭环/说明.md
# term:任务统计  doc:doc/功能/任务统计/说明.md
# term:执行中澄清  doc:doc/功能/执行中澄清/说明.md
# term:步骤业务名  doc:doc/功能/会话通道/说明.md
# term:脚本铲子生命周期  doc:doc/功能/脚本铲子生命周期/说明.md
# term:Effort  doc:doc/功能/Effort/说明.md
from __future__ import annotations

import itertools
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from fawai_avatar.brain.capabilities import looks_like_coding_goal
from fawai_avatar.brain.clarification import (
    CLARIFICATION_KEY,
    build_clarification_meta,
    format_clarification_text,
    normalize_need_input_plan,
    options_from_plan,
)
from fawai_avatar.brain.compliance import (
    architecture_paths_in,
    plan_satisfies_architecture,
    write_paths_from_plan,
)
from fawai_avatar.brain.explore_progress import (
    explore_gate_should_abort_after_force,
    explore_gate_should_force_mutate,
    explore_gate_should_inject,
    explore_only_abort_threshold,
    explore_only_gate_threshold,
    extract_locate_anchors,
    format_explore_gate_message,
    format_locate_budget_message,
    format_locate_mutate_force_message,
    format_mutate_force_message,
    format_compressed_hits_message,
    format_compressed_unread_ban_message,
    format_wide_grep_message,
    hot_repeated_paths,
    is_explore_only_step,
    locate_budget_should_abort_after_force,
    locate_budget_should_force_mutate,
    locate_budget_should_inject,
    locate_budget_threshold,
    locate_force_threshold,
    merge_locate_anchors,
    normalize_read_windows,
    preferred_locate_anchors,
    step_has_search_actions,
    step_repeats_banned_wide_grep,
    step_wide_grep_paths,
    step_wide_grep_patterns,
    update_explore_only_streak,
    update_post_locate_streak,
    update_read_path_counts,
    update_read_windows,
    wide_grep_hit_threshold,
)
from fawai_avatar.brain.method_recovery import (
    format_method_switch_history,
    format_remote_away_veto,
    format_remote_force_message,
    format_remote_gate_message,
    looks_like_remote_away_text,
    looks_like_remote_ops_goal,
    method_constraints_for_failure,
    remote_gate_should_abort_after_force,
    remote_gate_should_force,
    remote_gate_should_inject,
    remote_idle_abort_threshold,
    remote_idle_gate_threshold,
    update_remote_idle_streak,
)
from fawai_avatar.brain.shovel_lifecycle import (
    format_outcome_gate_message,
    format_promote_hint_message,
    format_search_gate_message,
    hard_reject_invent_without_search,
    should_inject_outcome_gate,
    should_inject_promote_hint,
    should_inject_search_gate,
)
from fawai_avatar.brain.answer_effect import (
    answer_coverage_reject_reason,
    compose_answer_goal,
    delivery_payload_reject_reason,
    fail_message_for_answer_coverage,
    format_answer_coverage_prompt,
    format_delivery_payload_prompt,
    format_unread_dump_prompt,
    goal_needs_answer_coverage_gate,
    goal_needs_delivery_payload_gate,
    prefer_fs_read_path,
    prune_hits_dumps_after_source_evidence,
    should_suppress_compressed_unread_ban,
    thought_is_delivery_intention,
    unread_compressed_dumps,
)
from fawai_avatar.brain.necessary_paths import (
    failed_needs_path_ledger_retry,
    format_path_ledger_fail_prompt,
)
from fawai_avatar.brain.false_beyond import (
    failed_needs_false_beyond_retry,
    format_false_beyond_fail_prompt,
    should_promote_false_beyond_success,
    thought_claims_false_beyond,
)
from fawai_avatar.brain.false_missing_source import (
    collect_source_evidence,
    failed_needs_false_missing_retry,
    format_false_missing_fail_prompt,
)
from fawai_avatar.brain.write_effect import (
    format_visible_effect_prompt,
    format_weak_write_message,
    goal_needs_visible_effect_gate,
    results_have_weak_write,
    thought_admits_no_visible_effect,
    thought_ok_for_visible_effect,
)
from fawai_avatar.brain.action_segment import (
    apply_expect_to_result,
    format_mechanical_obs_summary,
    should_skip_obs_reflect_llm,
)
from fawai_avatar.brain.step_run import (
    act_rule_ok,
    format_act_obs_summary,
    normalize_step_run,
    step_run_label,
    step_tier_from_meta,
)
from fawai_avatar.brain.effort import effort_from_meta
from fawai_avatar.brain.fast import fast_from_meta
from fawai_avatar.brain.obs_reflect import (
    format_obs_reflection_for_history,
    obs_reflect_enabled,
    run_obs_reflect,
)
from fawai_avatar.brain.delegate import (
    fast_model_from_env,
    format_delegate_feedback,
    format_delegate_handoff,
    plan_delegate,
)
from fawai_avatar.brain.planner import Brain, format_soft_fallback_history_message
from fawai_avatar.brain.reflect import (
    abort_should_deliver_commands,
    coerce_reflection_no_abort,
    failed_action_fingerprints,
    format_abort_delivery,
    format_reflection_for_history,
    format_stagnation_for_history,
    run_reflect,
    run_stagnation,
    plan_skips_step_repeat,
    step_fingerprint,
    step_repeat_enabled,
    step_repeat_threshold,
)
from fawai_avatar.brain.step_kind import (
    coerce_observe_kinds_skip_shallow,
    parallel_worker_count,
    should_execute_parallel,
    should_skip_shallow_env_observe,
    step_kind_label,
)
from fawai_avatar.control.executor import Controller
from fawai_avatar.observe.methods import (
    choose_observe_methods,
    collect_observation,
    is_env_observe_skipped,
    observe_label,
    progress_observe_text,
    progress_tool_observe_text,
    resolve_observe_methods,
    skipped_env_observation,
)
from fawai_avatar.observe.tool_digest import format_tool_observation
from fawai_avatar.brain.step_todos import normalize_todos, task_keeps_step_todos
from fawai_avatar.runtime.checkpoint import (
    CHECKPOINT_KEY,
    dump_checkpoint,
    load_checkpoint,
    restore_runtime_state,
    step_trace_parts,
)
from fawai_avatar.task import stats as task_stats
from fawai_avatar.task.models import ResourceItem
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.task.title import normalize_task_title, suggest_task_title
from fawai_avatar.types import Action, ExecResult, Observation, Plan

# text, meta（含 phase）
ProgressCallback = Callable[[str, dict[str, Any]], None]

# 同批动作失败后仍继续：只读探仓/检索族。写/shell/GUI/带 write_resources 仍 fail-fast。
# 避免「短词 grep 失败 → 同批多字 grep 未执行」这类误伤（见任务复盘 fail-fast 案例）。
_BATCH_CONTINUE_ON_FAIL_KINDS = frozenset(
    {
        "fs_grep",
        "fs_glob",
        "fs_read",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
        "git_status",
        "git_diff",
        "version_list",
        "version_get",
        "skill_search",
        "skill_get",
        "lore_search",
        "lore_get",
        "lore_buffer_list",
        "diff_list",
        "platform_info",
    }
)


def should_break_action_batch(action: Action, result: ExecResult) -> bool:
    """本动作失败后是否掐掉同批后续动作。

    只读失败 → 继续；写/前台/已报写清单/未知副作用 → 停。
    """
    if result.ok:
        return False
    if action.needs_foreground or list(action.write_resources or []):
        return True
    kind = (action.kind or "").strip().lower()
    return kind not in _BATCH_CONTINUE_ON_FAIL_KINDS


@dataclass
class StepTrace:
    observation: Observation | None
    plan: Plan | None
    results: list[ExecResult] = field(default_factory=list)


class WorkerLoop:
    """单工作任务闭环：观察 → 大脑 → 控制。"""

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        brain: Brain | None = None,
        controller: Controller | None = None,
        max_steps: int = 0,
        capture: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.brain = brain or Brain()
        self.controller = controller or Controller(supervisor=supervisor)
        # 0 = 不限制步数（见 FAWAI_MAX_STEPS）
        self.max_steps = max(0, int(max_steps))
        self.capture = capture
        self.on_progress = on_progress
        self._current_run_id: str | None = None
        self._current_task_id: str | None = None
        self._run_state: dict[str, Any] | None = None
        self._run_stats: dict[str, Any] = task_stats.empty_stats()
        self._stats_lock = threading.Lock()
        # 最近一次截屏帧（图内坐标 → 全局 click：origin + 尺寸 + Retina scale）
        self._last_screen_origin: dict[str, float] | None = None
        self._last_screen_frame: dict[str, float] | None = None

    def _step_indices(self, *, start: int = 0) -> Iterator[int]:
        start = max(0, int(start))
        if self.max_steps <= 0:
            return itertools.count(start)
        if start >= self.max_steps:
            return iter(())
        return iter(range(start, self.max_steps))

    def _sync_run_state(self, **fields: Any) -> None:
        if self._run_state is None:
            self._run_state = {}
        self._run_state.update(fields)

    def _take_plan_memory(
        self,
        plan: Any,
        *,
        history_compact: dict[str, Any],
        step_todos: list[dict[str, str]],
        allow_todos: bool,
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        if plan is None:
            return history_compact, step_todos
        cache = getattr(self.brain, "_history_compact_out", None)
        if isinstance(cache, dict):
            history_compact = dict(cache)
        raw = plan.raw if isinstance(getattr(plan, "raw", None), dict) else {}
        if allow_todos and "todos" in raw:
            step_todos = normalize_todos(raw.get("todos"))
        return history_compact, step_todos

    def _save_run_checkpoint(self, task_id: str, *, next_step: int) -> None:
        """步末/暂停前落盘，供进程重启后继续。"""
        try:
            state = dict(self._run_state or {})
            state["run_id"] = self._current_run_id
            state["next_step"] = int(next_step)
            payload = dump_checkpoint(state)
            task = self.supervisor.get_task(task_id)
            meta = dict(task.meta) if task else {}
            meta[CHECKPOINT_KEY] = payload
            self.supervisor.merge_task_meta(task_id, meta)
        except Exception as exc:  # noqa: BLE001 — 检查点失败不打崩步骤
            logger.warning("checkpoint save failed task=%s: %s", task_id, exc)

    def _clear_run_checkpoint(self, task_id: str) -> None:
        try:
            task = self.supervisor.get_task(task_id)
            if task is None:
                return
            meta = dict(task.meta or {})
            if CHECKPOINT_KEY not in meta:
                return
            meta.pop(CHECKPOINT_KEY, None)
            self.supervisor.replace_task_meta(task_id, meta)
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkpoint clear failed task=%s: %s", task_id, exc)

    def _load_run_checkpoint(self, task: Any) -> dict[str, Any] | None:
        raw = load_checkpoint(task.meta if task else None)
        if raw is None:
            return None
        restored = restore_runtime_state(raw)
        traces: list[StepTrace] = []
        for item in restored.pop("traces_raw", []) or []:
            obs, plan, results = step_trace_parts(item)
            traces.append(StepTrace(observation=obs, plan=plan, results=results))
        restored["traces"] = traces
        return restored

    def _can_continue(self, step: int) -> bool:
        if self.max_steps <= 0:
            return True
        return step + 1 < self.max_steps

    def _task_control(self, task_id: str) -> str:
        """读总管状态：ok | cancelled | paused | requeued | gone | other。

        pending 不算 ok：追问 reopen 会把终态打回 pending；旧 Worker 须停，
        由新 Worker 经 set_running 再跑，避免在 pending 下抢前台租约失败却假跑。
        """
        task = self.supervisor.get_task(task_id)
        if task is None:
            return "gone"
        status = (task.status or "").strip()
        if status == "cancelled":
            return "cancelled"
        if status == "paused":
            return "paused"
        if status == "pending":
            return "requeued"
        if status in {"running", "waiting", "blocked"}:
            return "ok"
        # completed/failed/archived 等：工作线程应停
        return "other"

    def _call_cancellable(
        self,
        task_id: str,
        fn: Callable[[], Any],
        *,
        label: str = "work",
    ) -> tuple[Any | None, bool]:
        """在后台跑阻塞调用并轮询取消。

        返回 `(value, cancelled)`。`cancelled=True` 时丢弃尚未返回的结果（线程继续跑完但不被使用），
        使「大脑规划中」等长等待不必等 LLM/本地假脑睡完才停步。
        """
        box: dict[str, Any] = {}
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 — 交回主线程
                errors.append(exc)

        thread = threading.Thread(
            target=_run,
            name=f"fawai-{label}-{task_id[:8]}",
            daemon=True,
        )
        thread.start()
        while thread.is_alive():
            if self._task_control(task_id) == "cancelled":
                self._progress(
                    "已收到停止，中断当前等待…",
                    phase="cancelled",
                    status="cancelled",
                )
                return None, True
            thread.join(0.2)
        if errors:
            raise errors[0]
        return box.get("value"), False

    def _halt_if_stopped(
        self,
        task_id: str,
        *,
        step: int,
        traces: list[StepTrace],
        replan_count: int,
        reflect_count: int,
        verify_results: list[ExecResult],
        last_reflection: dict[str, Any] | None,
        write_set: list[str],
        final_thought: str | None = None,
        stagnation_count: int = 0,
    ) -> dict[str, Any] | None:
        """
        协作式停步：取消则立刻收束；暂停则等到恢复或取消。
        返回非 None 表示本 run 应结束（不再执行后续 observe/plan/act）。
        """
        paused_announced = False
        while True:
            state = self._task_control(task_id)
            if state == "ok":
                if paused_announced:
                    self._stats_begin_segment(task_id)
                return None
            if state == "paused":
                if not paused_announced:
                    # 暂停前落检查点：进程若在暂停中被杀，恢复后可续
                    self._save_run_checkpoint(task_id, next_step=step)
                    self._stats_end_segment(task_id)
                    self._progress(
                        "任务已暂停，等待恢复…",
                        phase="paused",
                        step=step,
                        status="paused",
                        **self._stats_progress_extra(),
                    )
                    paused_announced = True
                time.sleep(0.25)
                continue
            if state == "cancelled":
                self._progress(
                    "任务已取消，停止执行",
                    phase="cancelled",
                    step=step,
                    status="cancelled",
                    **self._stats_progress_extra(),
                )
                return self._finish(
                    task_id,
                    ok=False,
                    error="cancelled",
                    traces=traces,
                    replan_count=replan_count,
                    reflect_count=reflect_count,
                    verify_results=verify_results,
                    final_thought=final_thought,
                    last_reflection=last_reflection,
                    write_set=write_set,
                    stagnation_count=stagnation_count,
                )
            if state == "requeued":
                # 追问 reopen→pending：本轮让出，保留 pending 给新 Worker；勿 supervisor.fail
                self._progress(
                    "任务已重新入队，本轮执行让出…",
                    phase="requeued",
                    step=step,
                    status="pending",
                    **self._stats_progress_extra(),
                )
                out = self._finish(
                    task_id,
                    ok=False,
                    error="stopped:requeued",
                    traces=traces,
                    replan_count=replan_count,
                    reflect_count=reflect_count,
                    verify_results=verify_results,
                    final_thought=final_thought,
                    last_reflection=last_reflection,
                    write_set=write_set,
                    stagnation_count=stagnation_count,
                )
                out["superseded"] = True
                return out
            # gone / other
            self._progress(
                f"任务状态已变更为不可继续（{state}），停止执行",
                phase="stopped",
                step=step,
                status=state,
            )
            return self._finish(
                task_id,
                ok=False,
                error=f"stopped:{state}",
                traces=traces,
                replan_count=replan_count,
                reflect_count=reflect_count,
                verify_results=verify_results,
                final_thought=final_thought,
                last_reflection=last_reflection,
                write_set=write_set,
                stagnation_count=stagnation_count,
            )

    def _seed_followup_history(
        self, task: Any, history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """终态追问续跑：把 meta.followups 写入 history（goal 只保留首条目标）。"""
        followups = list((task.meta or {}).get("followups") or [])
        if not followups:
            return history
        # 旧 goal 可能仍含【追问】正文；剥掉以免与 history 重复
        goal = str(getattr(task, "goal", "") or "")
        if "【追问】" in goal:
            cleaned = re.split(r"\n\s*【追问】\s*", goal, maxsplit=1)[0].strip()
            if cleaned and cleaned != goal.strip():
                tid = str(getattr(task, "task_id", "") or "")
                if tid:
                    self.supervisor.update_task_goal(tid, cleaned)
                task.goal = cleaned
        for item in followups:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            mode = str(item.get("mode") or "").strip()
            if mode == "clarification":
                prefix = "【澄清答复】"
            elif mode == "append":
                prefix = "【用户追加】"
            else:
                prefix = "【追问】"
            content = f"{prefix}{text}"
            if looks_like_remote_away_text(text):
                content = f"{content}\n{format_remote_away_veto(answer=text)}"
            history.append({"role": "user", "content": content[:1600]})
        return history

    def _drain_pending_appends(
        self,
        task_id: str,
        task: Any,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """消费 meta.pending_appends，注入 history；清空 pending。"""
        pending = list((task.meta or {}).get("pending_appends") or [])
        if not pending:
            return history
        for item in pending:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            instruction = str(item.get("instruction") or "").strip()
            reason = str(item.get("reason") or "").strip()
            parts = ["【用户追加】"]
            if text:
                parts.append(text)
            if instruction and instruction != text:
                parts.append(f"执行约束：{instruction}")
            if reason:
                parts.append(f"（分析：{reason}）")
            blob = "\n".join(parts)
            if looks_like_remote_away_text(text) or looks_like_remote_away_text(instruction):
                blob = f"{blob}\n{format_remote_away_veto(answer=text or instruction)}"
            history.append({"role": "user", "content": blob[:1600]})
        self.supervisor.merge_task_meta(task_id, {"pending_appends": []})
        # 刷新本地 task.meta，避免同一步再读到旧 pending
        refreshed = self.supervisor.get_task(task_id)
        if refreshed is not None:
            task.meta = refreshed.meta
            task.goal = refreshed.goal
        self._progress(
            f"已吸收 {len(pending)} 条用户追加",
            phase="append",
            step=getattr(self, "_current_step", 0),
            append_count=len(pending),
        )
        return history

    def _progress(self, text: str, *, phase: str, **extra: Any) -> None:
        """推送执行阶段（会话时间线）；回调失败不影响闭环。"""
        from fawai_avatar.channel.redact import redact_secrets

        cb = self.on_progress
        if cb is None:
            return
        safe_text = redact_secrets(text or "")
        meta: dict[str, Any] = {"phase": phase}
        if self._current_run_id:
            meta["run_id"] = self._current_run_id
        for k, v in extra.items():
            if k == "cmd" and isinstance(v, str):
                meta[k] = redact_secrets(v)
            else:
                meta[k] = v
        try:
            cb(safe_text, meta)
        except Exception:  # noqa: BLE001 — 进度出站不得打断任务
            pass

    def _llm_live(
        self,
        task_id: str,
        *,
        phase: str,
        step: int,
        **base_extra: Any,
    ) -> Any:
        """规划/反省等待模型时的节流直播（思考正文 + 普通输出）。"""
        from fawai_avatar.runtime.llm_live import LlmLiveProgress

        return LlmLiveProgress(
            self._progress,
            phase=phase,
            step=step,
            cancelled=lambda: self._task_control(task_id) == "cancelled",
            **base_extra,
        )

    def _task_title_for_step(self, task_id: str) -> str:
        """规划前簇头兜底：任务短标题。"""
        from fawai_avatar.task.title import normalize_step_label

        try:
            task = self.supervisor.get_task(task_id)
        except Exception:  # noqa: BLE001
            return ""
        if task is None:
            return ""
        meta = task.meta if isinstance(task.meta, dict) else {}
        return normalize_step_label(str(meta.get("title") or ""))

    def _task_meta(self, task: Any | None = None, *, task_id: str = "") -> dict[str, Any] | None:
        if task is not None and isinstance(getattr(task, "meta", None), dict):
            return task.meta
        if task_id:
            try:
                t = self.supervisor.get_task(task_id)
            except Exception:  # noqa: BLE001
                t = None
            if t is not None and isinstance(getattr(t, "meta", None), dict):
                return t.meta
        return None

    def _task_fast(self, task: Any | None = None, *, task_id: str = "") -> bool:
        """任务 meta.fast（默认关）。"""
        return fast_from_meta(self._task_meta(task, task_id=task_id))

    def _task_effort(self, task: Any | None = None, *, task_id: str = "") -> bool:
        """任务 meta.effort（默认关）。与 Fast 解耦，可同时开。"""
        return effort_from_meta(self._task_meta(task, task_id=task_id))

    def _task_step_tier(self, task: Any | None = None, *, task_id: str = "") -> bool:
        """任务 meta.step_tier（分档，默认关）。与 Fast/Effort 解耦。"""
        return step_tier_from_meta(self._task_meta(task, task_id=task_id))

    def _resolve_progress_step_label(
        self, task_id: str, plan: Plan | None = None
    ) -> str:
        """本步业务名：优先 plan.step_label，否则任务 title。"""
        from fawai_avatar.task.title import resolve_step_label

        if plan is not None:
            labeled = resolve_step_label(
                step_label=plan.step_label or "",
                thought=plan.thought or "",
                actions=list(plan.actions),
            )
            if labeled:
                return labeled
        return self._task_title_for_step(task_id)

    def _step_label_extra(
        self, task_id: str, plan: Plan | None = None
    ) -> dict[str, Any]:
        labeled = self._resolve_progress_step_label(task_id, plan)
        out: dict[str, Any] = {}
        if labeled:
            out["step_label"] = labeled
        kind = ""
        run = ""
        if plan is not None:
            kind = str(getattr(plan, "step_kind", "") or "").strip()
            # facts 仍展示规划声明的档；是否生效看任务 step_tier
            run = normalize_step_run(getattr(plan, "step_run", "") or "")
        if kind:
            out["step_kind"] = kind
            out["step_kind_label"] = step_kind_label(kind)
        if run:
            out["step_run"] = run
            out["step_run_label"] = step_run_label(run)
        return out

    def _stats_progress_extra(self) -> dict[str, Any]:
        return {"stats": task_stats.summary_for_api(self._run_stats)}

    def _stats_load(self, task_id: str) -> None:
        try:
            with self._stats_lock:
                task = self.supervisor.get_task(task_id)
                meta = dict(task.meta) if task else {}
                self._run_stats = task_stats.ensure_task_stats(meta)
                # 进程重启/新 run：丢弃未闭合墙钟段，避免脏时间
                self._run_stats["run_started_at"] = None
                # ensure_task_stats 可能懒回填，写回
                cur = self.supervisor.get_task(task_id)
                if cur is not None:
                    merged = dict(cur.meta or {})
                    merged[task_stats.STATS_KEY] = task_stats.persistable(self._run_stats)
                    self.supervisor.replace_task_meta(task_id, merged)
        except Exception as exc:  # noqa: BLE001 — 统计读写失败不阻断执行
            logger.warning("stats load failed task=%s: %s", task_id, exc)
            with self._stats_lock:
                self._run_stats = task_stats.empty_stats()

    def _stats_persist(self, task_id: str) -> None:
        """调用方若已持 ``_stats_lock`` 可直接调；未持锁时请走 note_* / begin/end。"""
        try:
            task = self.supervisor.get_task(task_id)
            if task is None:
                return
            meta = dict(task.meta or {})
            meta[task_stats.STATS_KEY] = task_stats.persistable(self._run_stats)
            self.supervisor.replace_task_meta(task_id, meta)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats persist failed task=%s: %s", task_id, exc)

    def _stats_begin_segment(self, task_id: str) -> None:
        with self._stats_lock:
            task_stats.begin_run_segment(self._run_stats)
            self._stats_persist(task_id)

    def _stats_end_segment(self, task_id: str) -> None:
        with self._stats_lock:
            task_stats.end_run_segment(self._run_stats)
            self._stats_persist(task_id)

    def _stats_note_plan(self, task_id: str, plan: Plan) -> None:
        raw = plan.raw if isinstance(plan.raw, dict) else {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        with self._stats_lock:
            task_stats.apply_llm_call(self._run_stats, usage=usage)
            self._stats_persist(task_id)

    def _stats_note_reflection(self, task_id: str, reflection: Any) -> None:
        raw = getattr(reflection, "raw", None)
        if not isinstance(raw, dict):
            raw = {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        with self._stats_lock:
            task_stats.apply_llm_call(self._run_stats, usage=usage)
            self._stats_persist(task_id)

    def _stats_note_step(self, task_id: str, actions: list[Action] | None) -> None:
        with self._stats_lock:
            task_stats.apply_round(self._run_stats)
            task_stats.apply_tools(self._run_stats, actions)
            self._stats_persist(task_id)

    def _stats_note_side_llm(
        self,
        task_id: str,
        usage: dict[str, Any] | None,
        *,
        elapsed_ms: int = 0,
    ) -> None:
        """旁路 LLM（如成功价值台账门控）：计入任务 stats，线程安全。"""
        with self._stats_lock:
            task_stats.apply_llm_call(self._run_stats, usage=usage)
            self._stats_persist(task_id)

    def _bind_success_ledger_hooks(self, task_id: str) -> None:
        """把台账门控旁路接到会话进度与任务用量。"""
        bg = getattr(self.controller, "_background", None)
        if bg is None:
            return
        step = int(getattr(self, "_current_step", 0) or 0)
        run_id = self._current_run_id

        def _on_progress(text: str, meta: dict[str, Any]) -> None:
            payload = dict(meta or {})
            phase = str(
                payload.pop("phase", None)
                or "success_ledger_gate"
            )
            if "step" not in payload:
                payload["step"] = step
            if run_id and "run_id" not in payload:
                payload["run_id"] = run_id
            # 完成后附带累计 stats，侧栏同步
            if str(payload.get("status") or "") == "done":
                with self._stats_lock:
                    payload.update(self._stats_progress_extra())
            self._progress(text, phase=phase, **payload)

        def _on_usage(usage: dict[str, Any] | None, elapsed_ms: int) -> None:
            self._stats_note_side_llm(task_id, usage, elapsed_ms=elapsed_ms)

        bg.gate_on_progress = _on_progress
        bg.gate_on_usage = _on_usage
        bg.progress_step = step
        bg.progress_run_id = run_id

    def _report_safe(
        self, task_id: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        try:
            self.supervisor.report(task_id, message, data or {})
        except Exception as exc:  # noqa: BLE001 — 事件落盘失败不打崩步骤
            logger.warning("supervisor.report failed task=%s: %s", task_id, exc)

    @staticmethod
    def _format_step_crash(exc: BaseException, *, phase: str) -> str:
        raw = str(exc).strip() or type(exc).__name__
        return f"步骤运行时异常[{phase}]：{type(exc).__name__}: {raw[:400]}"

    @staticmethod
    def _synthetic_crash_action(*, phase: str, exc: BaseException) -> Action:
        return Action(
            kind="step_runtime",
            args={
                "phase": phase,
                "exc_type": type(exc).__name__,
                "message": str(exc)[:240],
            },
            needs_foreground=False,
            write_resources=[],
        )

    def run(self, task_id: str) -> dict[str, Any]:
        task = self.supervisor.get_task(task_id)
        if task is None:
            return {"ok": False, "error": f"unknown task {task_id}"}
        if not self.supervisor.set_running(task_id):
            return {"ok": False, "error": f"cannot set running: {task.status}"}

        self._current_task_id = task_id
        self._stats_load(task_id)
        self._stats_begin_segment(task_id)
        resumed = self._load_run_checkpoint(task)
        start_step = 0
        if resumed:
            # 进程中断续跑：复用检查点 run_id / history，不从零重跑
            self._current_run_id = resumed.get("run_id") or uuid.uuid4().hex
            traces = list(resumed.get("traces") or [])
            history = list(resumed.get("history") or [])
            replan_count = int(resumed.get("replan_count") or 0)
            reflect_count = int(resumed.get("reflect_count") or 0)
            stagnation_count = int(resumed.get("stagnation_count") or 0)
            last_reflection = resumed.get("last_reflection")
            fingerprint_fails = Counter(resumed.get("fingerprint_fails") or {})
            prior_alternate_fps = set(resumed.get("prior_alternate_fps") or set())
            step_fp_counts = Counter(resumed.get("step_fp_counts") or {})
            prior_judged_step_fps = set(resumed.get("prior_judged_step_fps") or set())
            verify_results = list(resumed.get("verify_results") or [])
            write_set = list(resumed.get("write_set") or [])
            saw_code_check_ok = bool(resumed.get("saw_code_check_ok"))
            saw_architecture_ok = bool(resumed.get("saw_architecture_ok"))
            explore_only_streak = int(resumed.get("explore_only_streak") or 0)
            remote_idle_streak = int(resumed.get("remote_idle_streak") or 0)
            remote_method_forced = bool(resumed.get("remote_method_forced"))
            explore_mutate_forced = bool(resumed.get("explore_mutate_forced"))
            explore_behavior_armed = bool(resumed.get("explore_behavior_armed"))
            raw_reads = resumed.get("read_path_counts") or {}
            read_path_counts = (
                {str(k): int(v) for k, v in raw_reads.items()}
                if isinstance(raw_reads, dict)
                else {}
            )
            read_windows = normalize_read_windows(resumed.get("read_windows"))
            locate_anchors = [
                str(x) for x in (resumed.get("locate_anchors") or []) if str(x).strip()
            ]
            post_locate_streak = int(resumed.get("post_locate_streak") or 0)
            locate_mutate_forced = bool(resumed.get("locate_mutate_forced"))
            wide_grep_ban_paths = [
                str(x)
                for x in (resumed.get("wide_grep_ban_paths") or [])
                if str(x).strip()
            ]
            raw_ban_patterns = resumed.get("wide_grep_ban_patterns") or {}
            wide_grep_ban_patterns = (
                {
                    str(k).strip(): str(v)
                    for k, v in raw_ban_patterns.items()
                    if str(k).strip() and str(v).strip()
                }
                if isinstance(raw_ban_patterns, dict)
                else {}
            )
            awaiting_user_answer = bool(resumed.get("awaiting_user_answer"))
            weak_write_pending = bool(resumed.get("weak_write_pending"))
            visible_effect_retries = int(resumed.get("visible_effect_retries") or 0)
            answer_coverage_retries = int(
                resumed.get("answer_coverage_retries") or 0
            )
            path_ledger_retries = int(resumed.get("path_ledger_retries") or 0)
            false_beyond_retries = int(resumed.get("false_beyond_retries") or 0)
            false_missing_retries = int(resumed.get("false_missing_retries") or 0)
            unread_dump_forced = bool(resumed.get("unread_dump_forced"))
            compressed_dump_paths = [
                str(x)
                for x in (resumed.get("compressed_dump_paths") or [])
                if str(x).strip()
            ]
            read_ok_paths = [
                str(x)
                for x in (resumed.get("read_ok_paths") or [])
                if str(x).strip()
            ]
            pending_observe_kinds = resumed.get("pending_observe_kinds")
            pending_observe_args = resumed.get("pending_observe_args")
            if not isinstance(pending_observe_args, dict):
                pending_observe_args = None
            pending_step_kind = str(resumed.get("pending_step_kind") or "").strip() or None
            format_abort_softened = bool(resumed.get("format_abort_softened"))
            history_compact = (
                dict(resumed.get("history_compact"))
                if isinstance(resumed.get("history_compact"), dict)
                else {}
            )
            step_todos = normalize_todos(resumed.get("step_todos"))
            start_step = int(resumed.get("next_step") or 0)
            self._progress(
                f"进程恢复，从步骤 {start_step} 继续执行…",
                phase="resumed",
                step=start_step,
                status="running",
            )
        else:
            # 新开 run：独立 run_id（追问续跑开新时间线段）
            self._current_run_id = uuid.uuid4().hex
            traces = []
            history = []
            replan_count = 0
            reflect_count = 0
            stagnation_count = 0
            last_reflection = None
            fingerprint_fails = Counter()
            prior_alternate_fps = set()
            step_fp_counts = Counter()
            prior_judged_step_fps = set()
            verify_results = []
            write_set = []
            saw_code_check_ok = False
            saw_architecture_ok = False
            explore_only_streak = 0
            explore_mutate_forced = False
            explore_behavior_armed = False
            remote_idle_streak = 0
            remote_method_forced = False
            read_path_counts = {}
            read_windows = {}
            locate_anchors = []
            post_locate_streak = 0
            locate_mutate_forced = False
            wide_grep_ban_paths = []
            wide_grep_ban_patterns: dict[str, str] = {}
            awaiting_user_answer = False
            weak_write_pending = False
            visible_effect_retries = 0
            answer_coverage_retries = 0
            path_ledger_retries = 0
            false_beyond_retries = 0
            false_missing_retries = 0
            unread_dump_forced = False
            compressed_dump_paths = []
            read_ok_paths = []
            pending_observe_kinds = None
            pending_observe_args = None
            pending_step_kind = None
            format_abort_softened = False
            history_compact = {}
            step_todos = []
            # 追问不再并入 goal：新 run 把 meta.followups 注入 history，大脑仍能看到增量
            history = self._seed_followup_history(task, history)
            # 终态 reopen 无检查点：垫入此前结论/steps/会话记录，供规划与 task_context_*
            from fawai_avatar.brain.task_context import merge_prior_into_history

            history = merge_prior_into_history(
                history,
                meta=getattr(task, "meta", None),
                data_dir=self.supervisor.data_dir,
                task_id=task_id,
            )
            # status=running 给会话页左侧徽章实时切「执行中」
            self._progress("开始执行任务", phase="started", step=0, status="running")

        self._run_stagnation_count = stagnation_count
        step_repeat_n = step_repeat_threshold()
        explore_gate_n = explore_only_gate_threshold(goal=task.goal)
        explore_abort_n = explore_only_abort_threshold(goal=task.goal)
        remote_gate_n = remote_idle_gate_threshold(goal=task.goal)
        remote_abort_n = remote_idle_abort_threshold(goal=task.goal)
        locate_budget_n = locate_budget_threshold()
        wide_grep_n = wide_grep_hit_threshold()

        def persist(*, next_step: int) -> None:
            self._sync_run_state(
                history=history,
                traces=traces,
                replan_count=replan_count,
                reflect_count=reflect_count,
                stagnation_count=stagnation_count,
                fingerprint_fails=fingerprint_fails,
                prior_alternate_fps=prior_alternate_fps,
                step_fp_counts=step_fp_counts,
                prior_judged_step_fps=prior_judged_step_fps,
                verify_results=verify_results,
                write_set=write_set,
                saw_code_check_ok=saw_code_check_ok,
                saw_architecture_ok=saw_architecture_ok,
                explore_only_streak=explore_only_streak,
                explore_mutate_forced=explore_mutate_forced,
                explore_behavior_armed=explore_behavior_armed,
                remote_idle_streak=remote_idle_streak,
                remote_method_forced=remote_method_forced,
                read_path_counts=read_path_counts,
                read_windows=read_windows,
                locate_anchors=locate_anchors,
                post_locate_streak=post_locate_streak,
                locate_mutate_forced=locate_mutate_forced,
                wide_grep_ban_paths=wide_grep_ban_paths,
                wide_grep_ban_patterns=wide_grep_ban_patterns,
                awaiting_user_answer=awaiting_user_answer,
                weak_write_pending=weak_write_pending,
                visible_effect_retries=visible_effect_retries,
                answer_coverage_retries=answer_coverage_retries,
                path_ledger_retries=path_ledger_retries,
                false_beyond_retries=false_beyond_retries,
                false_missing_retries=false_missing_retries,
                unread_dump_forced=unread_dump_forced,
                compressed_dump_paths=compressed_dump_paths,
                read_ok_paths=read_ok_paths,
                pending_observe_kinds=pending_observe_kinds,
                pending_observe_args=pending_observe_args,
                pending_step_kind=pending_step_kind,
                last_reflection=last_reflection,
                format_abort_softened=format_abort_softened,
                history_compact=history_compact,
                step_todos=step_todos,
            )
            self._save_run_checkpoint(task_id, next_step=next_step)

        persist(next_step=start_step)

        def _recover_step_crash(
            exc: BaseException,
            *,
            step: int,
            phase: str,
            plan_hint: Plan | None = None,
            results_hint: list[ExecResult] | None = None,
            obs_hint: Observation | None = None,
        ) -> dict[str, Any] | None:
            """步骤未捕获异常 → 合成失败 → 执行反省 → 重规划或止损。

            返回值：`None` 表示已注入约束并应 continue；否则为 `_finish` 结果须 return。
            """
            nonlocal replan_count, reflect_count, last_reflection, awaiting_user_answer
            nonlocal fingerprint_fails, prior_alternate_fps
            err_msg = self._format_step_crash(exc, phase=phase)
            logger.exception(
                "worker step crashed task=%s step=%s phase=%s", task_id, step, phase
            )
            self._progress(
                f"步骤异常，转入反省：{err_msg[:160]}",
                phase="reflect",
                step=step,
                crash_phase=phase,
            )
            crash_action = self._synthetic_crash_action(phase=phase, exc=exc)
            if plan_hint is not None and plan_hint.actions:
                actions = list(plan_hint.actions)
                # 保证有一条失败结果对上动作；若提示结果全 ok / 空，则附加 crash 结果
                results = list(results_hint or [])
                if not results or all(r.ok for r in results):
                    results = list(results) + [
                        ExecResult(
                            ok=False,
                            message=err_msg,
                            data={"phase": phase, "exc_type": type(exc).__name__},
                        )
                    ]
                plan_for_reflect = Plan(
                    thought=plan_hint.thought or err_msg,
                    actions=actions,
                    done=False,
                    status="continue",
                    raw=dict(plan_hint.raw or {}),
                    observe_kinds=list(plan_hint.observe_kinds),
                    observe_args=dict(plan_hint.observe_args or {}),
                )
            else:
                actions = [crash_action]
                results = [
                    ExecResult(
                        ok=False,
                        message=err_msg,
                        data={"phase": phase, "exc_type": type(exc).__name__},
                    )
                ]
                plan_for_reflect = Plan(
                    thought=err_msg,
                    actions=actions,
                    done=False,
                    status="continue",
                    raw={"crash_phase": phase},
                )
            traces.append(
                StepTrace(
                    observation=obs_hint,
                    plan=plan_for_reflect,
                    results=results,
                )
            )
            history.append(
                {
                    "role": "assistant",
                    "content": plan_for_reflect.thought or err_msg,
                }
            )
            history.append(
                {
                    "role": "user",
                    "content": (
                        f"执行结果（步骤异常）：{err_msg}\n"
                        "请按【执行反省】裁决换道或止损；能继续完成目标则 alternate/retry。"
                    ),
                }
            )
            fps = failed_action_fingerprints(actions, results)
            primary_fp = fps[0] if fps else ""
            if not primary_fp:
                from fawai_avatar.brain.reflect import action_fingerprint

                primary_fp = action_fingerprint(crash_action)
            if primary_fp:
                fingerprint_fails[primary_fp] += 1
            fail_count = int(fingerprint_fails[primary_fp]) if primary_fp else 1
            can_replan = self._can_continue(step)
            t_reflect = time.perf_counter()
            crash_live = self._llm_live(
                task_id,
                phase="reflect",
                step=step,
                fingerprint=primary_fp or None,
            )
            reflect_fn = getattr(self.brain, "reflect", None)
            effort_on = self._task_effort(task)
            fast_on = self._task_fast(task)
            if callable(reflect_fn):
                reflection = reflect_fn(
                    task.goal,
                    actions,
                    results,
                    fingerprint=primary_fp,
                    fail_count=fail_count,
                    history=history,
                    prior_alternate_fps=prior_alternate_fps,
                    on_llm_delta=crash_live.on_delta,
                    effort=effort_on,
                    fast=fast_on,
                )
            else:
                reflection = run_reflect(
                    task.goal,
                    actions,
                    results,
                    fingerprint=primary_fp,
                    fail_count=fail_count,
                    history=history,
                    prior_alternate_fps=prior_alternate_fps,
                    llm=None,
                    effort=effort_on,
                    fast=fast_on,
                )
            crash_live.flush()
            reflect_ms = int((time.perf_counter() - t_reflect) * 1000)
            reflection = coerce_reflection_no_abort(reflection)
            reflect_count += 1
            last_reflection = reflection.to_dict()
            last_reflection["trigger"] = "step_crash"
            last_reflection["crash_phase"] = phase
            self._stats_note_reflection(task_id, reflection)
            if reflection.decision == "alternate" and primary_fp:
                prior_alternate_fps.add(primary_fp)
            history.append(
                {
                    "role": "user",
                    "content": format_reflection_for_history(reflection),
                }
            )
            # 【反省开读】/【开读须改写】硬闸已退役：不再注入禁定位标记
            self._report_safe(
                task_id,
                f"step_crash reflect: {reflection.decision}",
                {
                    "reflect_count": reflect_count,
                    "reflection": last_reflection,
                    "phase": phase,
                },
            )
            self._progress(
                f"反省完成：{reflection.decision} — "
                f"{(reflection.thought or reflection.cause)[:160]}",
                phase="reflect",
                step=step,
                decision=reflection.decision,
                reflect_count=reflect_count,
                **_llm_raw_progress_meta(
                    getattr(reflection, "raw", None), elapsed_ms=reflect_ms
                ),
                **self._stats_progress_extra(),
            )
            # 反省不再 abort：仅步数用尽时失败；止损由下一轮规划 status=failed
            if not can_replan:
                abort_msg = reflection.cause or err_msg
                abort_thought = plan_for_reflect.thought
                if abort_should_deliver_commands(reflection):
                    abort_msg = format_abort_delivery(
                        reflection, fallback=abort_msg
                    )
                    abort_thought = abort_msg
                if self._task_control(task_id) == "cancelled":
                    return self._finish(
                        task_id,
                        ok=False,
                        error="cancelled",
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=abort_thought,
                        last_reflection=last_reflection,
                        write_set=write_set,
                    )
                try:
                    self.supervisor.fail(task_id, message=abort_msg)
                except Exception as fail_exc:  # noqa: BLE001
                    logger.exception(
                        "fail after step crash also failed task=%s: %s",
                        task_id,
                        fail_exc,
                    )
                return self._finish(
                    task_id,
                    ok=False,
                    error=abort_msg,
                    traces=traces,
                    replan_count=replan_count,
                    reflect_count=reflect_count,
                    verify_results=verify_results,
                    final_thought=abort_thought,
                    last_reflection=last_reflection,
                    write_set=write_set,
                )
            replan_count += 1
            awaiting_user_answer = False
            self._progress(
                f"步骤异常后按反省重规划（第 {replan_count} 次，{reflection.decision}）…",
                phase="replan",
                step=step,
                replan_count=replan_count,
                reflect_count=reflect_count,
                decision=reflection.decision,
            )
            persist(next_step=step + 1)
            return None

        for step in self._step_indices(start=start_step):
            self._current_step = step
            persist(next_step=step)
            halted = self._halt_if_stopped(
                task_id,
                step=step,
                traces=traces,
                replan_count=replan_count,
                reflect_count=reflect_count,
                verify_results=verify_results,
                last_reflection=last_reflection,
                write_set=write_set,
            )
            if halted is not None:
                return halted

            # 执行中追加：刷新 goal，消费 pending_appends 写入 history（不打断上一工具）
            refreshed = self.supervisor.get_task(task_id)
            if refreshed is not None:
                task = refreshed
            history = self._drain_pending_appends(task_id, task, history)
            self._live_history = list(history)

            plan: Plan | None = None
            results: list[ExecResult] = []
            obs: Observation | None = None
            methods: list[str] = []
            crash_phase = "step"
            try:
                if awaiting_user_answer:
                    self._progress("整理面向用户的结论…", phase="finalize", step=step)

                crash_phase = "observe"
                skipped_shallow = should_skip_shallow_env_observe(
                    pending_observe_kinds,
                    prev_step_kind=pending_step_kind,
                )
                obs_requested = coerce_observe_kinds_skip_shallow(
                    pending_observe_kinds,
                    prev_step_kind=pending_step_kind,
                )
                methods = resolve_observe_methods(
                    task.goal,
                    capture=self.capture,
                    requested=obs_requested,
                )
                observe_extra: dict[str, Any] = {}
                if pending_step_kind:
                    observe_extra["step_kind"] = pending_step_kind
                    observe_extra["step_kind_label"] = step_kind_label(
                        pending_step_kind
                    )
                if skipped_shallow:
                    observe_extra["skipped_shallow_env"] = True
                step_label_extra = self._step_label_extra(task_id)
                # none 真跳过：不探前台、不推观察进度卡
                if is_env_observe_skipped(methods):
                    skip_meta: dict[str, Any] = {}
                    if skipped_shallow:
                        skip_meta["skipped_shallow_env"] = True
                    obs = skipped_env_observation(
                        goal=task.goal,
                        capture=self.capture,
                        extra_meta=skip_meta or None,
                    )
                    obs = self._attach_title_context(task_id, obs)
                else:
                    self._progress(
                        progress_observe_text(methods, pending=True),
                        phase="observe",
                        step=step,
                        observe_methods=methods,
                        observe_label="、".join(
                            observe_label(m) for m in methods
                        ),
                        **observe_extra,
                        **step_label_extra,
                    )
                    t_obs = time.perf_counter()
                    progress_base = {
                        "step": step,
                        **observe_extra,
                        **step_label_extra,
                    }

                    def _observe_progress(
                        text: str, *, phase: str, **extra: Any
                    ) -> None:
                        self._progress(text, phase=phase, **extra)

                    obs = self._observe(
                        task.goal,
                        methods=methods,
                        observe_args=pending_observe_args,
                        on_progress=_observe_progress,
                        progress_base=progress_base,
                    )
                    obs = self._attach_title_context(task_id, obs)
                    obs_ms = int((time.perf_counter() - t_obs) * 1000)
                    obs_meta = obs.meta or {}
                    done_methods = list(
                        obs_meta.get("observe_methods") or methods
                    )
                    stages = {
                        str(x)
                        for x in (obs_meta.get("progress_stages") or [])
                        if x
                    }
                    # 截屏本机完成（及 OCR API）已由 collect 分卡发射时，勿再合成「观察完成」
                    if "observe" not in stages:
                        self._progress(
                            progress_observe_text(
                                done_methods,
                                pending=False,
                                preview=str(obs.summary or ""),
                            ),
                            phase="observe",
                            step=step,
                            elapsed_ms=obs_ms,
                            observe_methods=done_methods,
                            observe_method=obs_meta.get("observe_method"),
                            observe_label=obs_meta.get("observe_label"),
                            paths=_paths_from_observation(obs),
                            frontmost_app=obs_meta.get("frontmost_app"),
                            frontmost_window=obs_meta.get(
                                "frontmost_window"
                            ),
                            frontmost_ok=obs_meta.get("frontmost_ok"),
                            **observe_extra,
                            **step_label_extra,
                        )
                    else:
                        # 刷新截屏卡：补前台窗 facts；正文不含 OCR 表
                        self._progress(
                            progress_observe_text(
                                ["obs_screen"],
                                pending=False,
                                preview=str(
                                    (obs.summary or "").split("OCR 观察")[0]
                                ).strip(),
                            ),
                            phase="observe",
                            step=step,
                            elapsed_ms=int(
                                obs_meta.get("capture_elapsed_ms") or obs_ms
                            ),
                            observe_methods=["obs_screen"],
                            observe_method="obs_screen",
                            observe_label=observe_label("obs_screen"),
                            paths=_paths_from_observation(obs),
                            frontmost_app=obs_meta.get("frontmost_app"),
                            frontmost_window=obs_meta.get(
                                "frontmost_window"
                            ),
                            frontmost_ok=obs_meta.get("frontmost_ok"),
                            **observe_extra,
                            **step_label_extra,
                        )

                crash_phase = "plan"
                self._progress(
                    "大脑规划中（等待模型）…",
                    phase="plan",
                    step=step,
                    **self._step_label_extra(task_id),
                )
                t_plan = time.perf_counter()
                plan_live = self._llm_live(
                    task_id,
                    phase="plan",
                    step=step,
                    **self._step_label_extra(task_id),
                )
                allow_todos = task_keeps_step_todos(task.meta)
                effort_on = self._task_effort(task)
                fast_on = self._task_fast(task)
                step_tier_on = self._task_step_tier(task)
                setattr(self.brain, "_plan_effort", effort_on)
                setattr(self.brain, "_plan_fast", fast_on)
                setattr(self.brain, "_plan_step_tier", step_tier_on)
                plan, plan_cancelled = self._call_cancellable(
                    task_id,
                    lambda hc=dict(history_compact), td=list(step_todos), allow=allow_todos: self.brain.plan(
                        task.goal,
                        obs,
                        history=history,
                        on_llm_delta=plan_live.on_delta,
                        history_compact=hc,
                        step_todos=td,
                        allow_todos=allow,
                    ),
                    label="plan",
                )
                history_compact, step_todos = self._take_plan_memory(
                    plan,
                    history_compact=history_compact,
                    step_todos=step_todos,
                    allow_todos=allow_todos,
                )
                plan_live.flush()
                if plan_cancelled:
                    return self._finish(
                        task_id,
                        ok=False,
                        error="cancelled",
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=None,
                        last_reflection=last_reflection,
                        write_set=write_set,
                    )
                assert plan is not None
                plan_wall_ms = int((time.perf_counter() - t_plan) * 1000)
                self._maybe_commit_task_title(task_id, plan, step=step)
                # 收尾结论轮：忽略多余动作，只取 thought
                if awaiting_user_answer and plan.actions:
                    plan = Plan(
                        thought=plan.thought or "完成",
                        actions=[],
                        done=True,
                        status="success" if plan.status != "failed" else "failed",
                        raw=plan.raw,
                        observe_kinds=list(plan.observe_kinds) or ["none"],
                        observe_args=dict(plan.observe_args or {}),
                        title=plan.title or "",
                        step_label=plan.step_label or "整理结论",
                        step_kind="finalize",
                        delegate=None,
                    )
                plan_write_paths = write_paths_from_plan(plan)
                if architecture_paths_in(plan_write_paths) and plan_satisfies_architecture(
                    plan, paths=plan_write_paths
                ):
                    saw_architecture_ok = True
                elif architecture_paths_in(write_set) and plan_satisfies_architecture(
                    plan, paths=write_set
                ):
                    # 收尾 success 轮：用累计 write_set + 本步 doc_refs/skip
                    saw_architecture_ok = True
                pending_observe_kinds = list(plan.observe_kinds) or None
                pending_observe_args = (
                    dict(plan.observe_args) if plan.observe_args else None
                )
                pending_step_kind = (
                    str(plan.step_kind or "").strip() or None
                )
                thought_preview = (plan.thought or "").strip()
                if len(thought_preview) > 200:
                    thought_preview = thought_preview[:200] + "…"
                planned_text = f"规划完成（{plan.status}）"
                if plan.step_kind:
                    planned_text = (
                        f"{planned_text}；步型：{step_kind_label(plan.step_kind)}"
                    )
                _sr = (
                    normalize_step_run(getattr(plan, "step_run", "") or "")
                    if self._task_step_tier(task)
                    else ""
                )
                if _sr:
                    planned_text = (
                        f"{planned_text}；执行档：{step_run_label(_sr)}"
                    )
                if plan.observe_kinds:
                    labels = "、".join(observe_label(m) for m in plan.observe_kinds)
                    planned_text = f"{planned_text}；下一步观察：{labels}"
                    st = (plan.observe_args or {}).get("obs_screen")
                    if isinstance(st, dict) and st.get("screen_target"):
                        planned_text = (
                            f"{planned_text}（screen_target={st.get('screen_target')}）"
                        )
                if thought_preview:
                    planned_text = f"{planned_text}：{thought_preview}"
                llm_meta = _llm_progress_meta(plan, fallback_elapsed_ms=plan_wall_ms)
                self._stats_note_plan(task_id, plan)
                self._progress(
                    planned_text,
                    phase="planned",
                    step=step,
                    status=plan.status,
                    observe_kinds=list(plan.observe_kinds),
                    **self._step_label_extra(task_id, plan),
                    **llm_meta,
                    **self._stats_progress_extra(),
                )
                self.supervisor.report(
                    task_id,
                    f"step {step}: {plan.status}",
                    {"thought": plan.thought, "actions": [a.to_dict() for a in plan.actions]},
                )

                # 规划可能较慢：规划后再闸一次，避免取消后仍执行动作
                halted = self._halt_if_stopped(
                    task_id,
                    step=step,
                    traces=traces,
                    replan_count=replan_count,
                    reflect_count=reflect_count,
                    verify_results=verify_results,
                    last_reflection=last_reflection,
                    write_set=write_set,
                    final_thought=plan.thought,
                )
                if halted is not None:
                    return halted

                # 主脑委派快模型：段内子闭环，摘要回灌后再由主脑规划
                delegate_spec = plan_delegate(plan)
                if delegate_spec is not None and not awaiting_user_answer:
                    fast_model = fast_model_from_env()
                    history.append(
                        {
                            "role": "assistant",
                            "content": plan.thought or f"委派：{delegate_spec.goal}",
                        }
                    )
                    traces.append(StepTrace(observation=obs, plan=plan, results=[]))
                    if not fast_model:
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "【委派未执行】未配置快模型路由（或供应商无 API Key）。"
                                    "请主脑自行规划 actions 完成子目标，勿再输出 delegate。"
                                    f"原委派：{delegate_spec.goal}"
                                ),
                            }
                        )
                        self._progress(
                            "委派跳过：未配置快模型",
                            phase="delegate_skip",
                            step=step,
                        )
                        persist(next_step=step + 1)
                        step += 1
                        continue
                    history.append(
                        {
                            "role": "user",
                            "content": format_delegate_handoff(
                                delegate_spec, model=fast_model
                            ),
                        }
                    )
                    self._progress(
                        f"委派快模型：{delegate_spec.goal[:80]}",
                        phase="delegate_fast",
                        step=step,
                        model=fast_model,
                        delegate=delegate_spec.to_dict(),
                    )
                    seg = self._run_fast_delegate(
                        task_id,
                        task_goal=task.goal,
                        spec=delegate_spec,
                        model=fast_model,
                        history=history,
                        traces=traces,
                        write_set=write_set,
                        start_step=step + 1,
                        pending_observe_kinds=pending_observe_kinds,
                        pending_observe_args=pending_observe_args,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        last_reflection=last_reflection,
                        persist=persist,
                    )
                    history = seg["history"]
                    traces = seg["traces"]
                    write_set = seg["write_set"]
                    step = int(seg["step"])
                    pending_observe_kinds = seg.get("pending_observe_kinds")
                    pending_observe_args = seg.get("pending_observe_args")
                    if seg.get("halted") is not None:
                        return seg["halted"]
                    history.append(
                        {
                            "role": "user",
                            "content": format_delegate_feedback(
                                delegate_spec,
                                outcome=str(seg.get("outcome") or "done"),
                                summary=str(seg.get("summary") or ""),
                                steps_used=int(seg.get("steps_used") or 0),
                                model=fast_model,
                            ),
                        }
                    )
                    self._progress(
                        f"快模型回灌主脑（{seg.get('outcome')}）",
                        phase="delegate_done",
                        step=step,
                        **self._stats_progress_extra(),
                    )
                    persist(next_step=step + 1)
                    step += 1
                    continue

                if plan.status == "need_input":
                    plan = normalize_need_input_plan(plan)
                    history.append(
                        {
                            "role": "assistant",
                            "content": plan.thought or "need_input",
                        }
                    )
                    traces.append(StepTrace(observation=obs, plan=plan, results=[]))
                    persist(next_step=step + 1)
                    return self._pause_for_clarification(
                        task_id,
                        plan=plan,
                        step=step,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        stagnation_count=stagnation_count,
                    )

                # 脚本铲子：写前软闸（未搜铲却造脚本 → 注入并重规划）
                if should_inject_search_gate(
                    history=history,
                    actions=plan.actions,
                    goal=task.goal,
                ):
                    gate_msg = format_search_gate_message()
                    history.append({"role": "user", "content": gate_msg})
                    self.supervisor.report(
                        task_id,
                        "shovel_lifecycle: search_gate",
                        {"kinds": [a.kind for a in plan.actions]},
                    )
                    self._progress(
                        gate_msg[:200],
                        phase="shovel_gate",
                        step=step,
                        decision="inject_search",
                    )
                    persist(next_step=step + 1)
                    continue
                hard_msg = hard_reject_invent_without_search(
                    history=history, actions=plan.actions
                )
                if hard_msg:
                    history.append({"role": "user", "content": hard_msg})
                    self.supervisor.report(
                        task_id,
                        "shovel_lifecycle: search_hard",
                        {"message": hard_msg},
                    )
                    self._progress(
                        hard_msg[:200],
                        phase="shovel_gate",
                        step=step,
                        decision="hard_reject",
                    )
                    persist(next_step=step + 1)
                    continue

                # 差评铲原样调用：成败对照软闸
                if should_inject_outcome_gate(
                    history=history,
                    actions=plan.actions,
                    data_dir=self.supervisor.data_dir,
                    goal=task.goal,
                ):
                    outcome_msg = format_outcome_gate_message(
                        actions=plan.actions,
                        data_dir=self.supervisor.data_dir,
                        goal=task.goal,
                    )
                    history.append({"role": "user", "content": outcome_msg})
                    self.supervisor.report(
                        task_id,
                        "shovel_lifecycle: outcome_gate",
                        {"kinds": [a.kind for a in plan.actions]},
                    )
                    self._progress(
                        outcome_msg[:200],
                        phase="shovel_gate",
                        step=step,
                        decision="inject_outcome",
                    )
                    persist(next_step=step + 1)
                    continue

                run_parallel = should_execute_parallel(
                    plan.step_kind or "", plan.actions
                )
                act_waiter = None
                if plan.actions:
                    from fawai_avatar.runtime.api_wait import ApiWaitProgress

                    act_extra = _actions_progress_meta(plan.actions)
                    act_text = _format_act_progress(plan.actions)
                    if run_parallel:
                        act_text = f"并行（{len(plan.actions)}）· {act_text}"
                        act_extra["act_parallel"] = True
                    act_waiter = ApiWaitProgress(
                        self._progress,
                        phase="act",
                        step=step,
                        waiting_text=act_text,
                        interval_sec=2.0,
                        cancelled=lambda: self._task_control(task_id)
                        == "cancelled",
                        mark_api_wait=False,
                        **act_extra,
                        **self._step_label_extra(task_id, plan),
                    )
                    act_waiter.start()
                crash_phase = "act"
                t_act = time.perf_counter()
                try:
                    results = self._execute_actions(
                        task_id,
                        plan.actions,
                        write_set=write_set,
                        parallel=run_parallel,
                    )
                finally:
                    if act_waiter is not None:
                        act_waiter.stop()
                act_total_ms = int((time.perf_counter() - t_act) * 1000)
                for action in plan.actions:
                    for p in action.write_resources:
                        if p not in write_set:
                            write_set.append(p)
                read_path_counts = update_read_path_counts(
                    read_path_counts,
                    actions=plan.actions,
                    results=results,
                )
                # 记录成功 fs_read 路径，供「压缩全文未读」闸比对
                for i, action in enumerate(plan.actions):
                    kind = (action.kind or "").strip().lower()
                    if kind not in {"fs_read", "fs_read_many"}:
                        continue
                    ok = i < len(results) and bool(results[i].ok)
                    if not ok:
                        continue
                    if kind == "fs_read":
                        p = str((action.args or {}).get("path") or "").strip()
                        if p and p not in read_ok_paths:
                            read_ok_paths.append(p)
                    else:
                        for raw in (action.args or {}).get("paths") or []:
                            p = str(raw or "").strip()
                            if p and p not in read_ok_paths:
                                read_ok_paths.append(p)
                # 源码小窗 / 已有写入：丢掉未读 hits dump，勿用禁宽搜拴住 mutate
                compressed_dump_paths = prune_hits_dumps_after_source_evidence(
                    compressed_dump_paths,
                    write_set=write_set,
                    read_ok_paths=read_ok_paths,
                )
                if is_explore_only_step(plan.actions, results) and not write_set:
                    explore_behavior_armed = True
                explore_only_streak = update_explore_only_streak(
                    explore_only_streak,
                    goal=task.goal,
                    write_set=write_set,
                    actions=plan.actions,
                    read_path_counts=read_path_counts,
                    read_windows=read_windows,
                    results=results,
                    locate_anchors=locate_anchors,
                    behavior_armed=explore_behavior_armed,
                )
                remote_idle_streak = update_remote_idle_streak(
                    remote_idle_streak,
                    goal=task.goal,
                    actions=plan.actions,
                    results=results,
                )
                if remote_idle_streak == 0:
                    remote_method_forced = False
                read_windows = update_read_windows(
                    read_windows,
                    actions=plan.actions,
                    results=results,
                )
                new_anchors = extract_locate_anchors(plan.actions, results)
                if new_anchors:
                    locate_anchors = merge_locate_anchors(locate_anchors, new_anchors)
                post_locate_streak = update_post_locate_streak(
                    post_locate_streak,
                    goal=task.goal,
                    locate_anchors=locate_anchors,
                    actions=plan.actions,
                    results=results,
                    wide_grep_ban_paths=wide_grep_ban_paths,
                    wide_grep_ban_patterns=wide_grep_ban_patterns,
                    behavior_armed=explore_behavior_armed,
                )
                if write_set:
                    locate_mutate_forced = False
                wide_paths = step_wide_grep_paths(
                    plan.actions, results, hit_threshold=wide_grep_n
                )
                pending_wide_grep_msg: str | None = None
                pending_wide_grep_repeat = False
                pending_compressed_hits_msg: str | None = None
                pending_compressed_unread_msg: str | None = None
                if wide_paths:
                    for p in wide_paths:
                        if p not in wide_grep_ban_paths:
                            wide_grep_ban_paths.append(p)
                    for path_key, pattern in step_wide_grep_patterns(
                        plan.actions, results, hit_threshold=wide_grep_n
                    ).items():
                        wide_grep_ban_patterns[path_key] = pattern
                    pending_wide_grep_msg = format_wide_grep_message(
                        paths=wide_paths, hit_threshold=wide_grep_n
                    )
                elif wide_grep_ban_paths and step_repeats_banned_wide_grep(
                    actions=plan.actions,
                    banned_paths=wide_grep_ban_paths,
                    banned_patterns=wide_grep_ban_patterns,
                ):
                    pending_wide_grep_msg = format_wide_grep_message(
                        paths=list(wide_grep_ban_paths),
                        hit_threshold=wide_grep_n,
                    )
                    pending_wide_grep_repeat = True
                # 压缩全文未读却仍仓检索：禁宽搜取证闸（优先于仅 hits 提示）
                # 已源码小窗或 mutate 后不因 hits dump 再注入（压错对象时的误拦）
                unread_before_obs = unread_compressed_dumps(
                    compressed_dump_paths, read_ok_paths
                )
                if (
                    unread_before_obs
                    and step_has_search_actions(plan.actions)
                    and not should_suppress_compressed_unread_ban(
                        write_set=write_set, read_ok_paths=read_ok_paths
                    )
                ):
                    pending_compressed_unread_msg = format_compressed_unread_ban_message(
                        dump_paths=unread_before_obs
                    )
                    post_locate_streak = int(post_locate_streak) + 1
                # 文件态回证（窄场景：写成功后核对磁盘）
                file_verifies = self._verify_file_state(plan.actions, results)
                verify_results.extend(file_verifies)
                if any(not v.ok for v in file_verifies):
                    err = next(v.message for v in file_verifies if not v.ok)
                    results.append(ExecResult(ok=False, message=f"verify failed: {err}"))
                # C3 界面回证：ui_type/type 成功后核对 AX；失败不得当验收通过
                ui_verifies = self._verify_ui_ax(plan.actions, results)
                verify_results.extend(ui_verifies)
                if any(not v.ok for v in ui_verifies):
                    err = next(v.message for v in ui_verifies if not v.ok)
                    results.append(ExecResult(ok=False, message=f"ui_verify failed: {err}"))
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                f"界面回证失败：{err}。"
                                "请换 ref / 再 obs_ax，或 status=failed；禁止未回证 success。"
                            ),
                        }
                    )
                # 弱写入：有 mutate 成功但仅死 CSS 变量/空白 → 不得当作验收进展
                weak_hit, weak_path, weak_reasons = results_have_weak_write(results)
                if weak_hit:
                    weak_write_pending = True
                    weak_msg = format_weak_write_message(
                        path=weak_path, reasons=weak_reasons
                    )
                    history.append({"role": "user", "content": weak_msg})
                    self._progress(
                        weak_msg[:400],
                        phase="weak_write",
                        step=step,
                        path=weak_path,
                    )
                    self.supervisor.report(
                        task_id,
                        "weak_write: inject",
                        {
                            "path": weak_path,
                            "reasons": weak_reasons,
                        },
                    )
                else:
                    wrote_ok = False
                    for i, r in enumerate(results):
                        if i >= len(plan.actions) or not r.ok:
                            continue
                        kind = (plan.actions[i].kind or "").strip().lower()
                        if kind in {"fs_write", "fs_replace", "fs_patch"}:
                            wrote_ok = True
                            break
                    if wrote_ok:
                        weak_write_pending = False
                for i, r in enumerate(results):
                    action = plan.actions[i] if i < len(plan.actions) else None
                    if (
                        r.ok
                        and action
                        and (action.kind or "").strip().lower() in {"code_check", "shell_verify"}
                    ):
                        saw_code_check_ok = True
                    msg = (r.message or "").strip()
                    if not msg:
                        continue
                    head = _summarize_action(action) if action else ""
                    prefix = "执行: " if r.ok else "失败: "
                    body = f"{head}\n{msg}" if head else msg
                    acted_extra = _action_progress_meta(action) if action else {}
                    # 单动作耗时无法拆分时，末条带上本批总耗时
                    if i == len(results) - 1 and "elapsed_ms" not in acted_extra:
                        acted_extra["elapsed_ms"] = act_total_ms
                    diff = ""
                    if isinstance(r.data, dict) and r.data.get("diff"):
                        diff = str(r.data.get("diff") or "")
                        self._progress(
                            f"Diff {r.data.get('path') or ''}\n{diff[:2500]}",
                            phase="diff",
                            step=step,
                            ok=r.ok,
                            pending=bool(r.data.get("pending")),
                            pending_id=r.data.get("pending_id"),
                            path=r.data.get("path"),
                            **acted_extra,
                            **self._step_label_extra(task_id, plan),
                        )
                    self._progress(
                        prefix + body[:1200],
                        phase="acted",
                        step=step,
                        ok=r.ok,
                        **acted_extra,
                        **self._step_label_extra(task_id, plan),
                    )
                    # py_run 等交付物 → 会话附件卡（预览/跨端下载）
                    if (
                        r.ok
                        and isinstance(r.data, dict)
                        and isinstance(r.data.get("deliverables"), list)
                        and r.data.get("deliverables")
                    ):
                        n_del = len(r.data["deliverables"])
                        self._progress(
                            f"产物就绪（{n_del}）",
                            phase="file_deliver",
                            step=step,
                            ok=True,
                            deliverables=r.data["deliverables"],
                            **self._step_label_extra(task_id, plan),
                        )
                traces.append(StepTrace(observation=obs, plan=plan, results=results))
                self._stats_note_step(task_id, plan.actions)
                history.append(
                    {
                        "role": "assistant",
                        "content": plan.thought or plan.status,
                    }
                )
                tool_feedback_for_obs = ""
                if plan.actions or results:
                    self._progress(
                        progress_tool_observe_text(pending=True),
                        phase="observe_tool",
                        step=step,
                        observe_method="obs_tool",
                        observe_label="工具结果",
                    )
                    t_tool_obs = time.perf_counter()
                    tool_feedback, tool_meta = format_tool_observation(
                        plan.actions,
                        results,
                        goal=task.goal,
                        data_dir=self.supervisor.data_dir,
                    )
                    from fawai_avatar.channel.redact import redact_secrets

                    tool_feedback = redact_secrets(tool_feedback or "")
                    tool_feedback_for_obs = tool_feedback
                    for part in tool_meta.get("parts") or []:
                        if not isinstance(part, dict):
                            continue
                        dump = str(part.get("dump_path") or "").strip()
                        # 绝对上限（max_capped）压缩已含头+尾回灌：落盘仅取证备份，
                        # 不进未读 dump 闸（避免逼读数十 MB 全文；大文件应头/尾/中间起行）。
                        if (
                            dump
                            and dump not in compressed_dump_paths
                            and not part.get("max_capped")
                        ):
                            compressed_dump_paths.append(dump)
                        if part.get("compressed"):
                            tool_meta["compressed"] = True
                    tool_obs_ms = int((time.perf_counter() - t_tool_obs) * 1000)
                    # 工具卡收口耗时 = 动作墙钟（主信号）+ 结果观察格式化；
                    # 禁止只用格式化毫秒覆盖 act_total_ms。
                    tool_card_ms = max(0, int(act_total_ms)) + max(0, int(tool_obs_ms))
                    self._progress(
                        progress_tool_observe_text(
                            pending=False,
                            compressed=bool(tool_meta.get("compressed")),
                            preview=tool_feedback,
                        ),
                        phase="observe_tool",
                        step=step,
                        elapsed_ms=tool_card_ms,
                        act_elapsed_ms=max(0, int(act_total_ms)),
                        observe_elapsed_ms=max(0, int(tool_obs_ms)),
                        observe_method="obs_tool",
                        observe_label="工具结果",
                        compressed=bool(tool_meta.get("compressed")),
                        result_chars=len(tool_feedback or ""),
                        paths=[
                            str(p.get("dump_path"))
                            for p in (tool_meta.get("parts") or [])
                            if isinstance(p, dict) and p.get("dump_path")
                        ],
                    )
                    history.append({"role": "user", "content": tool_feedback})
                    # hits/stdout 等观察压缩：提示取证，并把检索 path 纳入宽搜禁扫
                    hits_compressed = any(
                        isinstance(part, dict)
                        and part.get("compressed")
                        and str(part.get("label") or "") == "hits"
                        for part in (tool_meta.get("parts") or [])
                    )
                    grep_paths: list[str] = []
                    if hits_compressed or tool_meta.get("compressed"):
                        for action in plan.actions:
                            kind = (action.kind or "").strip().lower()
                            if kind not in {"fs_grep", "explore_search"}:
                                continue
                            p = str(
                                (action.args or {}).get("path")
                                or (action.args or {}).get("root")
                                or ""
                            ).strip() or "(repo)"
                            if p not in grep_paths:
                                grep_paths.append(p)
                            if p not in wide_grep_ban_paths:
                                wide_grep_ban_paths.append(p)
                            pat = str((action.args or {}).get("pattern") or "").strip()
                            if pat and p not in wide_grep_ban_patterns:
                                wide_grep_ban_patterns[p] = pat
                        if hits_compressed and not pending_wide_grep_msg:
                            pending_compressed_hits_msg = format_compressed_hits_message(
                                paths=grep_paths
                            )
                    compressed_dump_paths = prune_hits_dumps_after_source_evidence(
                        compressed_dump_paths,
                        write_set=write_set,
                        read_ok_paths=read_ok_paths,
                    )
                    unread_after_obs = unread_compressed_dumps(
                        compressed_dump_paths, read_ok_paths
                    )
                    if (
                        unread_after_obs
                        and not pending_compressed_unread_msg
                        and not should_suppress_compressed_unread_ban(
                            write_set=write_set, read_ok_paths=read_ok_paths
                        )
                    ):
                        pending_compressed_unread_msg = (
                            format_compressed_unread_ban_message(
                                dump_paths=unread_after_obs
                            )
                        )
                else:
                    soft_fmt = format_soft_fallback_history_message(plan)
                    history.append(
                        {
                            "role": "user",
                            "content": soft_fmt or "执行结果：本步无动作。",
                        }
                    )
                if pending_wide_grep_msg:
                    history.append(
                        {"role": "user", "content": pending_wide_grep_msg}
                    )
                    self.supervisor.report(
                        task_id,
                        "wide_grep: repeat"
                        if pending_wide_grep_repeat
                        else "wide_grep: degrade",
                        {
                            "paths": list(wide_grep_ban_paths)[:8],
                            "hit_threshold": wide_grep_n,
                            "repeat": pending_wide_grep_repeat,
                        },
                    )
                    self._progress(
                        pending_wide_grep_msg[:200],
                        phase="wide_grep",
                        step=step,
                        decision="repeat" if pending_wide_grep_repeat else "degrade",
                    )
                elif pending_compressed_unread_msg:
                    history.append(
                        {
                            "role": "user",
                            "content": pending_compressed_unread_msg,
                        }
                    )
                    self.supervisor.report(
                        task_id,
                        "wide_grep: compressed_unread",
                        {"preview": pending_compressed_unread_msg[:120]},
                    )
                    self._progress(
                        pending_compressed_unread_msg[:200],
                        phase="wide_grep",
                        step=step,
                        decision="compressed_unread",
                    )
                elif pending_compressed_hits_msg:
                    history.append(
                        {"role": "user", "content": pending_compressed_hits_msg}
                    )
                    self.supervisor.report(
                        task_id,
                        "wide_grep: compressed_hits",
                        {"preview": pending_compressed_hits_msg[:120]},
                    )
                    self._progress(
                        pending_compressed_hits_msg[:200],
                        phase="wide_grep",
                        step=step,
                        decision="compressed_hits",
                    )
                # 【开读须改写】硬闸已退役：不再因只读步强制 mutate / 禁定位
                # 动作间隙取消：不要走进反省/重规划空转
                if _results_indicate_stop(results) or self._task_control(task_id) != "ok":
                    halted = self._halt_if_stopped(
                        task_id,
                        step=step,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        final_thought=plan.thought,
                    )
                    if halted is not None:
                        return halted
                if any(not r.ok for r in results):
                    err = next((r.message for r in results if not r.ok), "action failed")
                    # 末步或大脑已宣告失败：直接失败，仍记一次反省便于轨迹
                    can_replan = self._can_continue(step) and plan.status != "failed"
                    fps = failed_action_fingerprints(plan.actions, results)
                    primary_fp = fps[0] if fps else ""
                    if primary_fp:
                        fingerprint_fails[primary_fp] += 1
                    fail_count = int(fingerprint_fails[primary_fp]) if primary_fp else 1

                    self._progress(
                        "动作失败，反省中…",
                        phase="reflect",
                        step=step,
                        fingerprint=primary_fp or None,
                    )
                    t_reflect = time.perf_counter()
                    reflect_live = self._llm_live(
                        task_id,
                        phase="reflect",
                        step=step,
                        fingerprint=primary_fp or None,
                        **self._step_label_extra(task_id, plan),
                    )
                    reflect_fn = getattr(self.brain, "reflect", None)
                    effort_on = self._task_effort(task)
                    fast_on = self._task_fast(task)
                    if callable(reflect_fn):
                        reflection = reflect_fn(
                            task.goal,
                            plan.actions,
                            results,
                            fingerprint=primary_fp,
                            fail_count=fail_count,
                            history=history,
                            prior_alternate_fps=prior_alternate_fps,
                            on_llm_delta=reflect_live.on_delta,
                            effort=effort_on,
                            fast=fast_on,
                            tool_obs_summary=tool_feedback_for_obs,
                        )
                    else:
                        reflection = run_reflect(
                            task.goal,
                            plan.actions,
                            results,
                            fingerprint=primary_fp,
                            fail_count=fail_count,
                            history=history,
                            prior_alternate_fps=prior_alternate_fps,
                            llm=None,
                            effort=effort_on,
                            fast=fast_on,
                            tool_obs_summary=tool_feedback_for_obs,
                        )
                    reflect_live.flush()
                    reflect_ms = int((time.perf_counter() - t_reflect) * 1000)
                    # 反省不再 abort；兼容旧模型若仍输出 abort → alternate
                    reflection = coerce_reflection_no_abort(reflection)
                    reflect_count += 1
                    last_reflection = reflection.to_dict()
                    self._stats_note_reflection(task_id, reflection)
                    if reflection.decision == "alternate" and primary_fp:
                        prior_alternate_fps.add(primary_fp)
                    history.append(
                        {
                            "role": "user",
                            "content": format_reflection_for_history(reflection),
                        }
                    )
                    method_cs = method_constraints_for_failure(
                        plan.actions, results, goal=task.goal
                    )
                    if method_cs or (reflection.raw or {}).get("method_switch"):
                        switch_msg = format_method_switch_history(
                            method_cs or list(reflection.constraints or [])[:4]
                        )
                        history.append({"role": "user", "content": switch_msg})
                        self._progress(
                            switch_msg[:200],
                            phase="method_switch",
                            step=step,
                            reflect_count=reflect_count,
                        )
                    # 【反省开读】/【开读须改写】硬闸已退役：失败后仍可 fs_glob/fs_grep 纠路径
                    self.supervisor.report(
                        task_id,
                        f"reflect: {reflection.decision}",
                        {
                            "reflect_count": reflect_count,
                            "reflection": last_reflection,
                            "results": [r.to_dict() for r in results],
                        },
                    )
                    self._progress(
                        f"反省完成：{reflection.decision} — {(reflection.thought or reflection.cause)[:160]}",
                        phase="reflect",
                        step=step,
                        decision=reflection.decision,
                        reflect_count=reflect_count,
                        **_llm_raw_progress_meta(
                            getattr(reflection, "raw", None), elapsed_ms=reflect_ms
                        ),
                        **self._stats_progress_extra(),
                    )

                    # 反省不再 abort：仅步数用尽时失败；止损由规划 status=failed
                    if not can_replan:
                        abort_msg = reflection.cause or err
                        abort_thought = plan.thought
                        if abort_should_deliver_commands(reflection):
                            abort_msg = format_abort_delivery(
                                reflection, fallback=abort_msg
                            )
                            abort_thought = abort_msg
                        # 已取消则保持 cancelled，不覆盖为 failed
                        if self._task_control(task_id) == "cancelled":
                            return self._finish(
                                task_id,
                                ok=False,
                                error="cancelled",
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=abort_thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                        self.supervisor.fail(task_id, message=abort_msg)
                        return self._finish(
                            task_id,
                            ok=False,
                            error=abort_msg,
                            traces=traces,
                            replan_count=replan_count,
                            reflect_count=reflect_count,
                            verify_results=verify_results,
                            final_thought=abort_thought,
                            last_reflection=last_reflection,
                            write_set=write_set,
                        )

                    replan_count += 1
                    awaiting_user_answer = False
                    # 取消后释放租约可能导致动作失败；重规划前必须再闸，避免空转
                    halted = self._halt_if_stopped(
                        task_id,
                        step=step,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        final_thought=plan.thought,
                    )
                    if halted is not None:
                        return halted
                    self._progress(
                        f"按反省结果重规划（第 {replan_count} 次，{reflection.decision}）…",
                        phase="replan",
                        step=step,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        decision=reflection.decision,
                    )
                    self.supervisor.report(
                        task_id,
                        "action failed; will replan after reflect",
                        {
                            "replan_count": replan_count,
                            "reflect_count": reflect_count,
                            "decision": reflection.decision,
                        },
                    )
                    persist(next_step=step + 1)
                    continue

                # 成功路径：工具结果观察后轻量思考反省（软建议；≠ 执行反省）
                # Fast → 跳过；step_run=act → 规则跳过；brief → 短 LLM；
                # reflect → 完整 LLM；缺省 → 机械跳过或完整 LLM
                if (
                    obs_reflect_enabled()
                    and plan.actions
                    and results
                    and all(r.ok for r in results)
                    and not awaiting_user_answer
                ):
                    # 分档关：忽略规划里的 step_run，走现网默认
                    step_run = (
                        normalize_step_run(getattr(plan, "step_run", "") or "")
                        if self._task_step_tier(task)
                        else ""
                    )
                    obs_done = False
                    # 跳过路径：真跳过——不推 obs_reflect 进度卡（时间线不展示）
                    if self._task_fast(task):
                        self.supervisor.report(
                            task_id,
                            "obs_reflect: skipped_fast",
                            {
                                "obs_reflection": {
                                    "progress": "advanced",
                                    "skipped": True,
                                    "mode": "fast",
                                }
                            },
                        )
                        obs_done = True
                    elif step_run == "act":
                        rule_ok, rule_reason = act_rule_ok(plan.actions, results)
                        if rule_ok:
                            # 规则摘要仍入规划 history；不推「跳过」进度卡
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_act_obs_summary(
                                        plan.actions, results
                                    ),
                                }
                            )
                            self.supervisor.report(
                                task_id,
                                "obs_reflect: skipped_act",
                                {
                                    "obs_reflection": {
                                        "progress": "advanced",
                                        "skipped": True,
                                        "mode": "act_rule",
                                        "step_run": "act",
                                    }
                                },
                            )
                            obs_done = True
                        else:
                            # 规则不过：升 brief，不静默放过
                            self.supervisor.report(
                                task_id,
                                f"obs_reflect: act_rule_miss → brief ({rule_reason})",
                                {
                                    "step_run": "act",
                                    "rule_reason": rule_reason,
                                },
                            )
                            step_run = "brief"
                    elif (
                        not step_run
                        and should_skip_obs_reflect_llm(plan.actions, results)
                    ):
                        history.append(
                            {
                                "role": "user",
                                "content": format_mechanical_obs_summary(
                                    plan.actions, results
                                ),
                            }
                        )
                        self.supervisor.report(
                            task_id,
                            "obs_reflect: skipped_mechanical",
                            {
                                "obs_reflection": {
                                    "progress": "advanced",
                                    "skipped": True,
                                    "mode": "mechanical_rule",
                                }
                            },
                        )
                        obs_done = True

                    if not obs_done:
                        # brief / reflect / 缺省非机械 / act→brief
                        use_brief = step_run == "brief"
                        self._progress(
                            "短观察反省中…" if use_brief else "观察反省中…",
                            phase="obs_reflect",
                            step=step,
                            **self._step_label_extra(task_id, plan),
                        )
                        t_obs_ref = time.perf_counter()
                        obs_live = self._llm_live(
                            task_id,
                            phase="obs_reflect",
                            step=step,
                            **self._step_label_extra(task_id, plan),
                        )
                        obs_reflect_fn = getattr(self.brain, "obs_reflect", None)
                        obs_data_dir = str(self.supervisor.data_dir)
                        effort_on = self._task_effort(task)
                        if callable(obs_reflect_fn):
                            obs_reflection = obs_reflect_fn(
                                task.goal,
                                plan.actions,
                                results,
                                tool_feedback=tool_feedback_for_obs,
                                data_dir=obs_data_dir,
                                on_llm_delta=obs_live.on_delta,
                                effort=effort_on,
                                brief=use_brief,
                            )
                        else:
                            obs_reflection = run_obs_reflect(
                                task.goal,
                                plan.actions,
                                results,
                                tool_feedback=tool_feedback_for_obs,
                                data_dir=obs_data_dir,
                                llm=None,
                                effort=effort_on,
                                brief=use_brief,
                            )
                        obs_live.flush()
                        obs_ref_ms = int((time.perf_counter() - t_obs_ref) * 1000)
                        history.append(
                            {
                                "role": "user",
                                "content": format_obs_reflection_for_history(
                                    obs_reflection
                                ),
                            }
                        )
                        mode_tag = "brief" if use_brief else (step_run or "reflect")
                        self.supervisor.report(
                            task_id,
                            f"obs_reflect: {obs_reflection.progress} ({mode_tag})",
                            {"obs_reflection": obs_reflection.to_dict()},
                        )
                        obs_extra: dict[str, Any] = {}
                        obs_raw = getattr(obs_reflection, "raw", None) or {}
                        if isinstance(obs_raw, dict):
                            usage = obs_raw.get("usage")
                            if isinstance(usage, dict) and usage:
                                obs_extra["tokens"] = usage
                            obs_extra["elapsed_ms"] = obs_ref_ms
                            thinking = obs_raw.get("thinking")
                            if isinstance(thinking, dict) and thinking:
                                obs_extra["thinking"] = thinking
                            if obs_raw.get("model"):
                                obs_extra["model"] = obs_raw.get("model")
                        prefix = "短观察反省" if use_brief else "观察反省"
                        self._progress(
                            f"{prefix}：{obs_reflection.progress} — "
                            f"{(obs_reflection.thought or '')[:160]}",
                            phase="obs_reflect",
                            step=step,
                            progress=obs_reflection.progress,
                            **obs_extra,
                            **self._step_label_extra(task_id, plan),
                            **self._stats_progress_extra(),
                        )

                # 成功路径：改文件目标连续只读探索过久 → 注入 / 强制改写 / 止损
                repeated_paths = hot_repeated_paths(read_path_counts)
                if write_set:
                    explore_mutate_forced = False
                # 强制改写轮之后仍只读成功 → 真正 failed（不再空转）
                if explore_gate_should_abort_after_force(
                    streak=explore_only_streak,
                    abort_at=explore_abort_n,
                    write_set=write_set,
                    mutate_forced=explore_mutate_forced,
                    actions=plan.actions,
                    results=results,
                ):
                    abort_msg = format_explore_gate_message(
                        streak=explore_only_streak,
                        gate=explore_gate_n,
                        abort_at=explore_abort_n,
                        repeated_paths=repeated_paths,
                    )
                    if explore_mutate_forced:
                        abort_msg = (
                            abort_msg
                            + " 已给过强制改写轮仍无写入，止损。"
                        )
                    history.append({"role": "user", "content": abort_msg})
                    self.supervisor.report(
                        task_id,
                        "explore_gate: abort",
                        {
                            "explore_only_streak": explore_only_streak,
                            "gate": explore_gate_n,
                            "abort_at": explore_abort_n,
                            "repeated_paths": repeated_paths,
                            "mutate_forced": explore_mutate_forced,
                        },
                    )
                    self._progress(
                        abort_msg[:200],
                        phase="explore_gate",
                        step=step,
                        explore_only_streak=explore_only_streak,
                        decision="abort",
                    )
                    if self._task_control(task_id) == "cancelled":
                        return self._finish(
                            task_id,
                            ok=False,
                            error="cancelled",
                            traces=traces,
                            replan_count=replan_count,
                            reflect_count=reflect_count,
                            verify_results=verify_results,
                            final_thought=plan.thought,
                            last_reflection=last_reflection,
                            write_set=write_set,
                            stagnation_count=stagnation_count,
                        )
                    self.supervisor.fail(task_id, message=abort_msg)
                    return self._finish(
                        task_id,
                        ok=False,
                        error=abort_msg,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=plan.thought,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        stagnation_count=stagnation_count,
                    )
                # 达中止阈值：先强制一轮改写（收紧工具面），不立刻 failed
                if explore_gate_should_force_mutate(
                    streak=explore_only_streak,
                    abort_at=explore_abort_n,
                    write_set=write_set,
                    mutate_forced=explore_mutate_forced,
                ):
                    force_msg = format_mutate_force_message(
                        streak=explore_only_streak,
                        gate=explore_gate_n,
                        abort_at=explore_abort_n,
                        repeated_paths=repeated_paths,
                    )
                    history.append({"role": "user", "content": force_msg})
                    explore_mutate_forced = True
                    self.supervisor.report(
                        task_id,
                        "explore_gate: force_mutate",
                        {
                            "explore_only_streak": explore_only_streak,
                            "gate": explore_gate_n,
                            "abort_at": explore_abort_n,
                            "repeated_paths": repeated_paths,
                        },
                    )
                    self._progress(
                        force_msg[:200],
                        phase="explore_gate",
                        step=step,
                        explore_only_streak=explore_only_streak,
                        decision="force_mutate",
                    )
                    persist(next_step=step + 1)
                    continue

                # 远端/部署类：连续只读无远端副作用 → 注入 / 强制换方法族 / 止损
                if looks_like_remote_ops_goal(task.goal) and remote_gate_n > 0:
                    if remote_gate_should_abort_after_force(
                        streak=remote_idle_streak,
                        abort_at=remote_abort_n,
                        forced=remote_method_forced,
                        actions=plan.actions,
                        results=results,
                    ):
                        abort_msg = format_remote_force_message(
                            streak=remote_idle_streak,
                            gate=remote_gate_n,
                            abort_at=remote_abort_n,
                        )
                        abort_msg = (
                            abort_msg
                            + " 已给过远端方法强制轮仍无远端成功副作用，止损。"
                        )
                        history.append({"role": "user", "content": abort_msg})
                        self.supervisor.report(
                            task_id,
                            "remote_gate: abort",
                            {
                                "remote_idle_streak": remote_idle_streak,
                                "gate": remote_gate_n,
                                "abort_at": remote_abort_n,
                            },
                        )
                        self._progress(
                            abort_msg[:200],
                            phase="remote_gate",
                            step=step,
                            remote_idle_streak=remote_idle_streak,
                            decision="abort",
                        )
                        if self._task_control(task_id) == "cancelled":
                            return self._finish(
                                task_id,
                                ok=False,
                                error="cancelled",
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=plan.thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                                stagnation_count=stagnation_count,
                            )
                        self.supervisor.fail(task_id, message=abort_msg)
                        return self._finish(
                            task_id,
                            ok=False,
                            error=abort_msg,
                            traces=traces,
                            replan_count=replan_count,
                            reflect_count=reflect_count,
                            verify_results=verify_results,
                            final_thought=abort_msg,
                            last_reflection=last_reflection,
                            write_set=write_set,
                            stagnation_count=stagnation_count,
                        )
                    if remote_gate_should_force(
                        streak=remote_idle_streak,
                        abort_at=remote_abort_n,
                        forced=remote_method_forced,
                    ):
                        force_msg = format_remote_force_message(
                            streak=remote_idle_streak,
                            gate=remote_gate_n,
                            abort_at=remote_abort_n,
                        )
                        history.append({"role": "user", "content": force_msg})
                        remote_method_forced = True
                        self.supervisor.report(
                            task_id,
                            "remote_gate: force_method",
                            {
                                "remote_idle_streak": remote_idle_streak,
                                "gate": remote_gate_n,
                                "abort_at": remote_abort_n,
                            },
                        )
                        self._progress(
                            force_msg[:200],
                            phase="remote_gate",
                            step=step,
                            remote_idle_streak=remote_idle_streak,
                            decision="force_method",
                        )
                        persist(next_step=step + 1)
                        continue
                    if remote_gate_should_inject(
                        streak=remote_idle_streak, gate=remote_gate_n
                    ):
                        gate_msg = format_remote_gate_message(
                            streak=remote_idle_streak,
                            gate=remote_gate_n,
                            abort_at=remote_abort_n,
                        )
                        history.append({"role": "user", "content": gate_msg})
                        self.supervisor.report(
                            task_id,
                            "remote_gate: inject",
                            {
                                "remote_idle_streak": remote_idle_streak,
                                "gate": remote_gate_n,
                                "abort_at": remote_abort_n,
                            },
                        )
                        self._progress(
                            gate_msg[:200],
                            phase="remote_gate",
                            step=step,
                            remote_idle_streak=remote_idle_streak,
                            decision="inject",
                        )

                # 已定位锚点：注入 / 强制改写 / 止损（不依赖 goal 启发式）
                locate_force_n = locate_force_threshold(budget=locate_budget_n)
                prefer_anchors = preferred_locate_anchors(locate_anchors, limit=8)
                if locate_budget_should_abort_after_force(
                    streak=post_locate_streak,
                    budget=locate_budget_n,
                    write_set=write_set,
                    mutate_forced=locate_mutate_forced,
                    actions=plan.actions,
                    results=results,
                ):
                    abort_msg = format_locate_mutate_force_message(
                        streak=post_locate_streak,
                        budget=locate_budget_n,
                        anchors=prefer_anchors,
                    )
                    abort_msg = abort_msg + " 已给过定位强制改写轮仍无写入，止损。"
                    history.append({"role": "user", "content": abort_msg})
                    self.supervisor.report(
                        task_id,
                        "locate_budget: abort",
                        {
                            "post_locate_streak": post_locate_streak,
                            "budget": locate_budget_n,
                            "force_at": locate_force_n,
                            "anchors": prefer_anchors,
                            "mutate_forced": locate_mutate_forced,
                        },
                    )
                    self._progress(
                        abort_msg[:200],
                        phase="locate_budget",
                        step=step,
                        post_locate_streak=post_locate_streak,
                        decision="abort",
                    )
                    if self._task_control(task_id) == "cancelled":
                        return self._finish(
                            task_id,
                            ok=False,
                            error="cancelled",
                            traces=traces,
                            replan_count=replan_count,
                            reflect_count=reflect_count,
                            verify_results=verify_results,
                            final_thought=plan.thought,
                            last_reflection=last_reflection,
                            write_set=write_set,
                            stagnation_count=stagnation_count,
                        )
                    self.supervisor.fail(task_id, message=abort_msg)
                    return self._finish(
                        task_id,
                        ok=False,
                        error=abort_msg,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=plan.thought,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        stagnation_count=stagnation_count,
                    )
                if locate_budget_should_force_mutate(
                    streak=post_locate_streak,
                    budget=locate_budget_n,
                    write_set=write_set,
                    mutate_forced=locate_mutate_forced,
                ):
                    force_msg = format_locate_mutate_force_message(
                        streak=post_locate_streak,
                        budget=locate_budget_n,
                        anchors=prefer_anchors,
                    )
                    history.append({"role": "user", "content": force_msg})
                    locate_mutate_forced = True
                    self.supervisor.report(
                        task_id,
                        "locate_budget: force_mutate",
                        {
                            "post_locate_streak": post_locate_streak,
                            "budget": locate_budget_n,
                            "force_at": locate_force_n,
                            "anchors": prefer_anchors,
                        },
                    )
                    self._progress(
                        force_msg[:200],
                        phase="locate_budget",
                        step=step,
                        post_locate_streak=post_locate_streak,
                        decision="force_mutate",
                    )
                    persist(next_step=step + 1)
                    continue
                if locate_budget_should_inject(
                    streak=post_locate_streak, budget=locate_budget_n
                ):
                    locate_msg = format_locate_budget_message(
                        streak=post_locate_streak,
                        budget=locate_budget_n,
                        anchors=prefer_anchors,
                    )
                    history.append({"role": "user", "content": locate_msg})
                    self.supervisor.report(
                        task_id,
                        "locate_budget: inject",
                        {
                            "post_locate_streak": post_locate_streak,
                            "budget": locate_budget_n,
                            "anchors": prefer_anchors,
                        },
                    )
                    self._progress(
                        locate_msg[:200],
                        phase="locate_budget",
                        step=step,
                        post_locate_streak=post_locate_streak,
                        decision="inject",
                    )
                if explore_gate_should_inject(
                    streak=explore_only_streak, gate=explore_gate_n
                ):
                    gate_msg = format_explore_gate_message(
                        streak=explore_only_streak,
                        gate=explore_gate_n,
                        abort_at=explore_abort_n,
                        repeated_paths=repeated_paths,
                    )
                    history.append({"role": "user", "content": gate_msg})
                    self.supervisor.report(
                        task_id,
                        "explore_gate: inject",
                        {
                            "explore_only_streak": explore_only_streak,
                            "gate": explore_gate_n,
                            "repeated_paths": repeated_paths,
                        },
                    )
                    self._progress(
                        gate_msg[:200],
                        phase="explore_gate",
                        step=step,
                        explore_only_streak=explore_only_streak,
                        decision="inject",
                    )

                # 成功路径：步骤重复守卫（零 LLM 判定；命中再裁决）
                # 规划可声明 allow_repeat / skip_step_repeat：本步豁免计数与裁决
                if (
                    step_repeat_enabled()
                    and plan.actions
                    and results
                    and all(r.ok for r in results)
                    and not plan_skips_step_repeat(
                        plan.actions, plan_raw=getattr(plan, "raw", None) or {}
                    )
                ):
                    step_fp = step_fingerprint(
                        plan.actions, observe_methods=list(methods)
                    )
                    if step_fp:
                        step_fp_counts[step_fp] += 1
                        hit_count = int(step_fp_counts[step_fp])
                        prior_judged = step_fp in prior_judged_step_fps
                        # 判决后再撞同指纹：仍回灌大脑裁决，不由代码硬 abort
                        should_judge = prior_judged or hit_count >= step_repeat_n
                        if should_judge:
                            self._progress(
                                "检测到步骤重复，裁决中…",
                                phase="stagnation",
                                step=step,
                                fingerprint=step_fp,
                                hit_count=hit_count,
                                prior_judged=prior_judged,
                            )
                            t_stag = time.perf_counter()
                            stag_fn = getattr(self.brain, "stagnation", None)
                            stag_kwargs = {
                                "fingerprint": step_fp,
                                "hit_count": hit_count,
                                "history": history,
                                "prior_judged": prior_judged,
                                "locate_anchors": locate_anchors,
                                "fast": self._task_fast(task),
                            }
                            if callable(stag_fn):
                                reflection = stag_fn(
                                    task.goal,
                                    plan.actions,
                                    results,
                                    **stag_kwargs,
                                )
                            else:
                                reflection = run_stagnation(
                                    task.goal,
                                    plan.actions,
                                    results,
                                    llm=None,
                                    **stag_kwargs,
                                )
                            reflection = coerce_reflection_no_abort(reflection)
                            stag_ms = int((time.perf_counter() - t_stag) * 1000)
                            stagnation_count += 1
                            self._run_stagnation_count = stagnation_count
                            last_reflection = reflection.to_dict()
                            last_reflection["trigger"] = "step_repeat"
                            self._stats_note_reflection(task_id, reflection)
                            prior_judged_step_fps.add(step_fp)
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_stagnation_for_history(reflection),
                                }
                            )
                            self.supervisor.report(
                                task_id,
                                f"stagnation: {reflection.decision}",
                                {
                                    "stagnation_count": stagnation_count,
                                    "reflection": last_reflection,
                                    "step_fingerprint": step_fp,
                                },
                            )
                            self._progress(
                                f"步骤重复裁决：{reflection.decision} — "
                                f"{(reflection.thought or reflection.cause)[:160]}",
                                phase="stagnation",
                                step=step,
                                decision=reflection.decision,
                                stagnation_count=stagnation_count,
                                forced=bool(reflection.forced),
                                **_llm_raw_progress_meta(
                                    getattr(reflection, "raw", None), elapsed_ms=stag_ms
                                ),
                                **self._stats_progress_extra(),
                            )
                            # continue / alternate：注入约束后进入下一轮；止损由规划 failed
                            replan_count += 1
                            awaiting_user_answer = False
                            persist(next_step=step + 1)
                            continue

                if plan.done or plan.status in {"success", "failed"}:
                    if plan.status == "failed":
                        if self._task_control(task_id) == "cancelled":
                            return self._finish(
                                task_id,
                                ok=False,
                                error="cancelled",
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=plan.thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                                stagnation_count=stagnation_count,
                            )
                        # 兜底：JSON/空内容格式失败不得整任务 Failed（Brain 应已软降级）
                        thought_fmt = (plan.thought or "").strip()
                        if self._can_continue(step) and any(
                            thought_fmt.startswith(p)
                            for p in ("无法解析 JSON：", "LLM 返回空内容")
                        ):
                            soft_plan = Plan(
                                thought=thought_fmt,
                                actions=[],
                                done=False,
                                status="continue",
                                raw={
                                    "format_soft_fallback": True,
                                    "format_fail_excerpt": thought_fmt[:400],
                                },
                            )
                            history.append(
                                {
                                    "role": "assistant",
                                    "content": thought_fmt[:1500],
                                }
                            )
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_soft_fallback_history_message(
                                        soft_plan
                                    )
                                    or (
                                        "规划输出格式异常，请只输出一个 JSON 对象后继续。"
                                    ),
                                }
                            )
                            self._progress(
                                "规划格式异常，已跳过本步并重试（未中断）",
                                phase="planned",
                                step=step,
                                decision="format_soft_fallback",
                            )
                            replan_count += 1
                            awaiting_user_answer = False
                            persist(next_step=step + 1)
                            continue
                        # 假归因且产物已齐：直接升 success（先于台账回灌，避免「元概念」假失败落地）
                        if should_promote_false_beyond_success(
                            goal=task.goal,
                            thought=plan.thought or "",
                            write_set=write_set,
                        ):
                            plan.status = "success"
                            plan.done = True
                            plan.raw = {
                                **(plan.raw or {}),
                                "false_beyond_promoted": True,
                            }
                            self._progress(
                                "假归因超出能力但产物已齐，已升为成功…",
                                phase="false_beyond",
                                step=step,
                                decision="promote_false_beyond",
                                write_set=list(write_set)[:12],
                            )
                            self.supervisor.report(
                                task_id,
                                "false_beyond: promote_success",
                                {
                                    "write_set": list(write_set)[:12],
                                    "thought": (plan.thought or "")[:240],
                                },
                            )
                        # 假归因「超出能力清单」：文案/脚本应生成+fs_write，回灌至多 1 次
                        # （须先于 path_ledger：监管目标常误触发台账，会抢走假归因回灌）
                        elif (
                            false_beyond_retries < 1
                            and self._can_continue(step)
                            and failed_needs_false_beyond_retry(
                                goal=task.goal,
                                thought=plan.thought or "",
                                write_set=write_set,
                            )
                        ):
                            false_beyond_retries += 1
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_false_beyond_fail_prompt(
                                        goal=task.goal,
                                        thought=plan.thought or "",
                                        write_set=write_set,
                                    ),
                                }
                            )
                            self._progress(
                                "假归因超出能力，已回灌：请生成落盘或验收已写产物…",
                                phase="false_beyond",
                                step=step,
                                decision="retry_false_beyond",
                                false_beyond_retries=false_beyond_retries,
                            )
                            self.supervisor.report(
                                task_id,
                                "false_beyond: retry_fail_thought",
                                {
                                    "false_beyond_retries": false_beyond_retries,
                                    "write_set": list(write_set)[:12],
                                    "thought": (plan.thought or "")[:240],
                                },
                            )
                            awaiting_user_answer = False
                            persist(next_step=step + 1)
                            continue
                        # 假归因「找不到样式/源码」：已有成功读/非空 grep 则回灌至多 1 次
                        elif (
                            false_missing_retries < 1
                            and self._can_continue(step)
                            and not thought_claims_false_beyond(plan.thought or "")
                            and failed_needs_false_missing_retry(
                                thought=plan.thought or "",
                                read_ok_paths=read_ok_paths,
                                traces=traces,
                            )
                        ):
                            false_missing_retries += 1
                            evidence = collect_source_evidence(
                                read_ok_paths=read_ok_paths, traces=traces
                            )
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_false_missing_fail_prompt(
                                        thought=plan.thought or "",
                                        evidence_paths=evidence,
                                    ),
                                }
                            )
                            self._progress(
                                "假归因找不到源，已回灌：请读命中行改写或分项阻塞…",
                                phase="false_missing",
                                step=step,
                                decision="retry_false_missing",
                                false_missing_retries=false_missing_retries,
                            )
                            self.supervisor.report(
                                task_id,
                                "false_missing: retry_fail_thought",
                                {
                                    "false_missing_retries": false_missing_retries,
                                    "evidence": evidence[:12],
                                    "thought": (plan.thought or "")[:240],
                                },
                            )
                            awaiting_user_answer = False
                            persist(next_step=step + 1)
                            continue
                        # 必要路径台账：分项/交付未兑现则回灌一轮（假归因套话不走此闸）
                        elif (
                            path_ledger_retries < 1
                            and self._can_continue(step)
                            and not thought_claims_false_beyond(plan.thought or "")
                            and failed_needs_path_ledger_retry(
                                goal=task.goal, thought=plan.thought or ""
                            )
                        ):
                            path_ledger_retries += 1
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_path_ledger_fail_prompt(
                                        goal=task.goal,
                                        thought=plan.thought or "",
                                    ),
                                }
                            )
                            self._progress(
                                "必要路径收尾未过，请按台账分项或交付命令后重写…",
                                phase="path_ledger",
                                step=step,
                                decision="retry_fail_thought",
                                path_ledger_retries=path_ledger_retries,
                            )
                            self.supervisor.report(
                                task_id,
                                "path_ledger: retry_fail_thought",
                                {
                                    "path_ledger_retries": path_ledger_retries,
                                    "thought": (plan.thought or "")[:240],
                                },
                            )
                            awaiting_user_answer = False
                            persist(next_step=step + 1)
                            continue
                        elif plan.status == "failed":
                            self.supervisor.fail(
                                task_id, message=plan.thought or "brain failed"
                            )
                            return self._finish(
                                task_id,
                                ok=False,
                                error=plan.thought or "brain failed",
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=plan.thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                    # 弱写入未消化：禁止假 success
                    if (
                        plan.status == "success"
                        and weak_write_pending
                        and self._can_continue(step)
                    ):
                        history.append(
                            {
                                "role": "user",
                                "content": format_weak_write_message(
                                    path=write_set[-1] if write_set else "(unknown)",
                                    reasons=["仍有未消化的弱写入"],
                                ),
                            }
                        )
                        awaiting_user_answer = False
                        persist(next_step=step + 1)
                        continue
                    # 编码类：写过文件但未 code_check → 强制补检查
                    if (
                        looks_like_coding_goal(task.goal)
                        and write_set
                        and not saw_code_check_ok
                        and plan.status == "success"
                        and self._can_continue(step)
                    ):
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "编码目标尚未通过 code_check/shell_verify。"
                                    "请先对已改路径执行 code_check，再 success。"
                                    f"已改路径：{', '.join(write_set)}"
                                ),
                            }
                        )
                        awaiting_user_answer = False
                        persist(next_step=step + 1)
                        continue
                    # 脚本铲子：临时脚本成功后软提示提炼（不阻断最终 success）
                    if should_inject_promote_hint(
                        history=history, status=plan.status or ""
                    ):
                        hint = format_promote_hint_message()
                        history.append({"role": "user", "content": hint})
                        self.supervisor.report(
                            task_id,
                            "shovel_lifecycle: promote_hint",
                            {},
                        )
                        self._progress(
                            hint[:200],
                            phase="shovel_gate",
                            step=step,
                            decision="promote_hint",
                        )
                        awaiting_user_answer = False
                        persist(next_step=step + 1)
                        continue
                    # 守规：写过分层路径却从未声明 doc_refs/skip → 强制补声明
                    if (
                        architecture_paths_in(write_set)
                        and not saw_architecture_ok
                        and plan.status == "success"
                        and self._can_continue(step)
                    ):
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    "守规约束：已改分层路径但尚未声明 doc_refs 或 architecture_skip_reason。"
                                    "请再规划一步：可空 actions，但 raw/JSON 须带 doc_refs"
                                    "（对应 doc/功能/*/说明.md）或非空 architecture_skip_reason；"
                                    "然后再 status=success。"
                                    f"已改分层路径：{', '.join(architecture_paths_in(write_set))}"
                                ),
                            }
                        )
                        awaiting_user_answer = False
                        persist(next_step=step + 1)
                        continue
                    # 页面/布局类：空 actions success 也须说明可见副作用（防 native tool_calls 假收尾）
                    if (
                        plan.status == "success"
                        and write_set
                        and not plan.actions
                        and not awaiting_user_answer
                        and goal_needs_visible_effect_gate(task.goal)
                        and self._can_continue(step)
                    ):
                        awaiting_user_answer = True
                        history.append(
                            {
                                "role": "user",
                                "content": format_visible_effect_prompt(
                                    goal=task.goal, write_set=write_set
                                ),
                            }
                        )
                        persist(next_step=step + 1)
                        continue
                    # 收尾轮：thought 须过可见副作用闸；自认无变化 → failed
                    if (
                        plan.status == "success"
                        and awaiting_user_answer
                        and write_set
                        and goal_needs_visible_effect_gate(task.goal)
                    ):
                        thought = plan.thought or ""
                        if thought_admits_no_visible_effect(thought):
                            fail_msg = (
                                thought.strip()
                                or "写入无目标对齐的可见副作用，不能标完成"
                            )
                            self.supervisor.fail(task_id, message=fail_msg)
                            return self._finish(
                                task_id,
                                ok=False,
                                error=fail_msg,
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                        if not thought_ok_for_visible_effect(
                            thought, goal=task.goal
                        ):
                            if (
                                visible_effect_retries < 1
                                and self._can_continue(step)
                            ):
                                visible_effect_retries += 1
                                history.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            format_visible_effect_prompt(
                                                goal=task.goal, write_set=write_set
                                            )
                                            + "上一轮 thought 太空或像占位（如 native tool_calls），请重写。"
                                        ),
                                    }
                                )
                                persist(next_step=step + 1)
                                continue
                            fail_msg = (
                                "最终回复未说明用户可见变化，拒绝假成功"
                            )
                            self.supervisor.fail(task_id, message=fail_msg)
                            return self._finish(
                                task_id,
                                ok=False,
                                error=fail_msg,
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                    # 交稿意图句 / 输出类追问：空 actions success 须有会话载荷（常开）
                    followups_meta = list(
                        ((task.meta or {}).get("followups") or [])
                    )
                    answer_goal = compose_answer_goal(task.goal, followups_meta)
                    needs_delivery_payload = goal_needs_delivery_payload_gate(
                        task.goal, followups=followups_meta
                    )
                    if (
                        plan.status == "success"
                        and not plan.actions
                        and not (
                            goal_needs_visible_effect_gate(task.goal) and write_set
                        )
                        and self._can_continue(step)
                        and (
                            needs_delivery_payload
                            or thought_is_delivery_intention(plan.thought or "")
                        )
                    ):
                        thought = plan.thought or ""
                        if needs_delivery_payload:
                            delivery_reason = delivery_payload_reject_reason(
                                thought, goal=answer_goal
                            )
                        else:
                            delivery_reason = (
                                "delivery_intention"
                                if thought_is_delivery_intention(thought)
                                else None
                            )
                        if delivery_reason is not None:
                            if answer_coverage_retries < 1:
                                answer_coverage_retries += 1
                                awaiting_user_answer = True
                                history.append(
                                    {
                                        "role": "user",
                                        "content": format_delivery_payload_prompt(
                                            goal=answer_goal,
                                            thought=thought,
                                            reason=delivery_reason,
                                            write_set=list(write_set or []),
                                        ),
                                    }
                                )
                                self.supervisor.report(
                                    task_id,
                                    f"answer_coverage: {delivery_reason}",
                                    {"goal": answer_goal[:160]},
                                )
                                persist(next_step=step + 1)
                                continue
                            fail_msg = fail_message_for_answer_coverage(
                                delivery_reason
                            )
                            self.supervisor.fail(task_id, message=fail_msg)
                            return self._finish(
                                task_id,
                                ok=False,
                                error=fail_msg,
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                    # 问询/状态类：空 actions success 也须过答复覆盖闸
                    if (
                        plan.status == "success"
                        and not plan.actions
                        and goal_needs_answer_coverage_gate(
                            task.goal, followups=followups_meta
                        )
                        and not (
                            goal_needs_visible_effect_gate(task.goal) and write_set
                        )
                        and self._can_continue(step)
                    ):
                        thought = plan.thought or ""
                        work_root = None
                        try:
                            bg = getattr(self, "background", None) or getattr(
                                self.supervisor, "background", None
                            )
                            if bg is not None and getattr(bg, "fs", None) is not None:
                                work_root = bg.fs.work_root
                            else:
                                from fawai_avatar.config import RuntimeConfig

                                work_root = RuntimeConfig.from_env().work_root
                        except Exception:  # noqa: BLE001
                            work_root = None
                        unread = unread_compressed_dumps(
                            compressed_dump_paths, read_ok_paths
                        )
                        if unread and not unread_dump_forced:
                            unread_dump_forced = True
                            awaiting_user_answer = True
                            rel_paths = [
                                prefer_fs_read_path(p, work_root=work_root)
                                for p in unread[:3]
                            ]
                            history.append(
                                {
                                    "role": "user",
                                    "content": format_unread_dump_prompt(
                                        paths=rel_paths
                                    ),
                                }
                            )
                            self.supervisor.report(
                                task_id,
                                "answer_coverage: unread_dump",
                                {"paths": rel_paths[:3]},
                            )
                            persist(next_step=step + 1)
                            continue
                        reason = answer_coverage_reject_reason(
                            thought, goal=answer_goal
                        )
                        if reason is not None:
                            # 过薄且仍有未读 dump：优先逼读（若上一轮已 forced 仍未读则计入拒因）
                            if unread and reason in {
                                "inventory_thin",
                                "meta_narration",
                                "delivery_intention",
                                "delivery_thin",
                                "too_short",
                                "placeholder",
                            }:
                                reason_for_fail = "unread_dump"
                            else:
                                reason_for_fail = reason
                            if answer_coverage_retries < 1:
                                answer_coverage_retries += 1
                                awaiting_user_answer = True
                                rel_unread = [
                                    prefer_fs_read_path(p, work_root=work_root)
                                    for p in unread[:3]
                                ]
                                history.append(
                                    {
                                        "role": "user",
                                        "content": format_answer_coverage_prompt(
                                            goal=answer_goal,
                                            thought=thought,
                                            reason=reason,
                                            unread_dumps=rel_unread or None,
                                            write_set=list(write_set or []),
                                        ),
                                    }
                                )
                                persist(next_step=step + 1)
                                continue
                            fail_msg = fail_message_for_answer_coverage(
                                reason_for_fail
                            )
                            self.supervisor.fail(task_id, message=fail_msg)
                            return self._finish(
                                task_id,
                                ok=False,
                                error=fail_msg,
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                    # 刚执行完工具却直接 success：再要一轮自然语言结论（供会话层回用户）
                    if (
                        plan.actions
                        and not awaiting_user_answer
                        and self._can_continue(step)
                    ):
                        awaiting_user_answer = True
                        if goal_needs_visible_effect_gate(task.goal) and write_set:
                            content = format_visible_effect_prompt(
                                goal=task.goal, write_set=write_set
                            )
                        elif goal_needs_delivery_payload_gate(
                            task.goal, followups=followups_meta
                        ):
                            content = format_delivery_payload_prompt(
                                goal=answer_goal,
                                write_set=list(write_set or []),
                            )
                        elif goal_needs_answer_coverage_gate(
                            task.goal, followups=followups_meta
                        ):
                            content = format_answer_coverage_prompt(
                                goal=answer_goal,
                                unread_dumps=unread_compressed_dumps(
                                    compressed_dump_paths, read_ok_paths
                                )
                                or None,
                                write_set=list(write_set or []),
                            )
                        else:
                            from fawai_avatar.brain.prompt_profile import (
                                report_delivery_rule,
                            )

                            content = (
                                "请根据以上执行结果，给出面向用户的最终回答。"
                                "直接回答用户目标（例如高不高、是否成功、关键数字含义）；"
                                "不要复述或粘贴原始 stdout/top 全文；不要写出明文口令。"
                                "禁止把「准备读取/然后向用户交付」等下一步计划写进 thought；"
                                "thought 必须是给用户看的正文或结论。"
                                + report_delivery_rule(task.goal)
                                + "本步必须 status=success、done=true、actions 为空数组；"
                                "thought 即为给用户看的最终回复。"
                            )
                        history.append(
                            {
                                "role": "user",
                                "content": content,
                            }
                        )
                        persist(next_step=step + 1)
                        continue
                    # 完成前再闸：避免「取消刚生效」却标成 completed
                    halted = self._halt_if_stopped(
                        task_id,
                        step=step,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        last_reflection=last_reflection,
                        write_set=write_set,
                        final_thought=plan.thought,
                    )
                    if halted is not None:
                        return halted
                    if not self.supervisor.complete(task_id):
                        # 竞态：期间被取消/归档
                        if self._task_control(task_id) == "cancelled":
                            return self._finish(
                                task_id,
                                ok=False,
                                error="cancelled",
                                traces=traces,
                                replan_count=replan_count,
                                reflect_count=reflect_count,
                                verify_results=verify_results,
                                final_thought=plan.thought,
                                last_reflection=last_reflection,
                                write_set=write_set,
                            )
                        return self._finish(
                            task_id,
                            ok=False,
                            error="cannot complete task",
                            traces=traces,
                            replan_count=replan_count,
                            reflect_count=reflect_count,
                            verify_results=verify_results,
                            final_thought=plan.thought,
                            last_reflection=last_reflection,
                            write_set=write_set,
                        )
                    return self._finish(
                        task_id,
                        ok=True,
                        error=None,
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=plan.thought,
                        last_reflection=last_reflection,
                        write_set=write_set,
                    )

                # 本步收束（continue）：落检查点后再进下一轮
                persist(next_step=step + 1)
            except Exception as exc:  # noqa: BLE001 — 步骤级保护：反省后尽量重规划
                halted = _recover_step_crash(
                    exc,
                    step=step,
                    phase=crash_phase,
                    plan_hint=plan,
                    results_hint=results,
                    obs_hint=obs,
                )
                if halted is not None:
                    return halted
                continue


        if self._task_control(task_id) == "cancelled":
            return self._finish(
                task_id,
                ok=False,
                error="cancelled",
                traces=traces,
                replan_count=replan_count,
                reflect_count=reflect_count,
                verify_results=verify_results,
                final_thought=None,
                last_reflection=last_reflection,
                write_set=write_set,
            )
        self.supervisor.fail(task_id, message="max_steps exceeded")
        return self._finish(
            task_id,
            ok=False,
            error="max_steps exceeded",
            traces=traces,
            replan_count=replan_count,
            reflect_count=reflect_count,
            verify_results=verify_results,
            final_thought=None,
            last_reflection=last_reflection,
            write_set=write_set,
        )

    def _pause_for_clarification(
        self,
        task_id: str,
        *,
        plan: Plan,
        step: int,
        traces: list[StepTrace],
        replan_count: int,
        reflect_count: int,
        verify_results: list[ExecResult],
        last_reflection: dict[str, Any] | None,
        write_set: list[str] | None,
        stagnation_count: int | None = None,
    ) -> dict[str, Any]:
        """need_input：出澄清进度、置 waiting、保留检查点；不 complete/fail。"""
        options = options_from_plan(plan)
        question = (plan.thought or "").strip() or "需要你确认后才能继续。"
        card_text = format_clarification_text(question, options)
        clar = build_clarification_meta(question=question, options=options, step=step)
        task = self.supervisor.get_task(task_id)
        meta = dict(task.meta) if task else {}
        meta[CLARIFICATION_KEY] = clar
        # 保留已由 persist 写入的 run_checkpoint
        self.supervisor.replace_task_meta(task_id, meta)
        self.supervisor.set_waiting(task_id)
        self._progress(
            card_text,
            phase="clarification",
            step=step,
            status="waiting",
            question=question,
            options=options,
        )
        stag = (
            int(stagnation_count)
            if stagnation_count is not None
            else int(getattr(self, "_run_stagnation_count", 0) or 0)
        )
        steps = _traces_to_dict(traces)
        out: dict[str, Any] = {
            "ok": True,
            "awaiting_input": True,
            "task_id": task_id,
            "replan_count": replan_count,
            "reflect_count": reflect_count,
            "stagnation_count": stag,
            "verifies": [v.to_dict() for v in verify_results],
            "write_set": list(write_set or []),
            "steps": steps,
            "clarification": clar,
            "final_thought": question,
        }
        if last_reflection:
            out["reflection"] = last_reflection
        return out

    def _finish(
        self,
        task_id: str,
        *,
        ok: bool,
        error: str | None,
        traces: list[StepTrace],
        replan_count: int,
        reflect_count: int,
        verify_results: list[ExecResult],
        final_thought: str | None,
        last_reflection: dict[str, Any] | None = None,
        write_set: list[str] | None = None,
        stagnation_count: int | None = None,
    ) -> dict[str, Any]:
        steps = _traces_to_dict(traces)
        # 截断落盘，避免 meta 无限膨胀
        clipped = _clip_steps_for_meta(steps)
        thought = (final_thought or "").strip() or _guess_final_thought(steps)
        stag = (
            int(stagnation_count)
            if stagnation_count is not None
            else int(getattr(self, "_run_stagnation_count", 0) or 0)
        )
        try:
            task_stats.end_run_segment(self._run_stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stats end_run_segment failed: %s", exc)
        try:
            task = self.supervisor.get_task(task_id)
            meta = dict(task.meta) if task else {}
            meta.update(
                {
                    "steps": clipped,
                    "final_thought": thought,
                    "replan_count": replan_count,
                    "reflect_count": reflect_count,
                    "stagnation_count": stag,
                    "ok": ok,
                    "error": error,
                    "write_set": list(write_set or []),
                    task_stats.STATS_KEY: task_stats.persistable(self._run_stats),
                }
            )
            if last_reflection:
                meta["reflection"] = last_reflection
            # 终态清检查点，避免重启误续已完成任务
            meta.pop(CHECKPOINT_KEY, None)
            self.supervisor.replace_task_meta(task_id, meta)
            # 个人长记忆旁路：成功任务的 goal + final_thought
            if ok and thought:
                try:
                    from fawai_avatar.lore_sidecar import observe_lore_event

                    observe_lore_event(
                        goal=str(task.goal or "") if task else "",
                        final_thought=thought,
                        event_kind="task_finished",
                        task_id=task_id,
                    )
                except Exception as side_exc:  # noqa: BLE001
                    logger.warning("lore sidecar on finish failed: %s", side_exc)
            # E1 终态条件短卡蒸馏（失败不改任务终态）
            try:
                distill_meta = self._run_task_distill_on_finish(
                    task_id,
                    ok=ok,
                    goal=str(task.goal or "") if task else "",
                    meta=meta,
                    steps=clipped,
                )
                if distill_meta is not None:
                    meta["task_distill"] = distill_meta
                    self.supervisor.replace_task_meta(task_id, meta)
            except Exception as distill_exc:  # noqa: BLE001
                logger.warning(
                    "task_distill on finish failed task=%s: %s",
                    task_id,
                    distill_exc,
                )
        except Exception as exc:  # noqa: BLE001 — 终态 meta 写失败仍返回结果给枢纽
            logger.exception("finish meta write failed task=%s: %s", task_id, exc)
        self._run_state = None
        out: dict[str, Any] = {
            "ok": ok,
            "task_id": task_id,
            "replan_count": replan_count,
            "reflect_count": reflect_count,
            "stagnation_count": stag,
            "verifies": [v.to_dict() for v in verify_results],
            "write_set": list(write_set or []),
            "steps": steps,
            "final_thought": thought,
        }
        if last_reflection:
            out["reflection"] = last_reflection
        if error:
            out["error"] = error
        return out

    def _run_task_distill_on_finish(
        self,
        task_id: str,
        *,
        ok: bool,
        goal: str,
        meta: dict[str, Any],
        steps: list[Any],
    ) -> dict[str, Any] | None:
        """终态条件蒸馏；返回写入 meta.task_distill 的摘要。"""
        from fawai_avatar.brain.task_distill import run_task_distill

        data_dir = str(self.supervisor.data_dir)
        step = int(getattr(self, "_current_step", 0) or 0)
        run_id = self._current_run_id

        def _on_progress(text: str, prog_meta: dict[str, Any]) -> None:
            payload = dict(prog_meta or {})
            phase = str(payload.pop("phase", None) or "").strip() or "task_distill"
            if "step" not in payload:
                payload["step"] = step
            if run_id and "run_id" not in payload:
                payload["run_id"] = run_id
            if str(payload.get("status") or "") == "done":
                with self._stats_lock:
                    payload.update(self._stats_progress_extra())
            # phase="task_distill" 字面量锚点见 brain/task_distill.py
            self._progress(text, phase=phase, **payload)

        def _on_usage(usage: dict[str, Any] | None, elapsed_ms: int) -> None:
            self._stats_note_side_llm(task_id, usage, elapsed_ms=elapsed_ms)

        return run_task_distill(
            data_dir=data_dir,
            task_id=task_id,
            goal=goal,
            ok=ok,
            meta=meta,
            steps=steps,
            on_progress=_on_progress,
            on_usage=_on_usage,
        )

    def _attach_title_context(self, task_id: str, obs: Observation) -> Observation:
        """把是否需要 LLM 命名任务标题注入观察 meta。"""
        task = self.supervisor.get_task(task_id)
        meta = dict(task.meta or {}) if task else {}
        title = normalize_task_title(str(meta.get("title") or ""))
        source = str(meta.get("title_source") or "")
        need = (not title) or source == "heuristic"
        obs_meta = dict(obs.meta or {})
        if need:
            obs_meta["need_task_title"] = True
            if title:
                obs_meta["task_title"] = title
                obs_meta["title_source"] = source or "heuristic"
        elif title:
            obs_meta["task_title"] = title
            obs_meta["title_source"] = source or "llm"
            obs_meta.pop("need_task_title", None)
        return Observation(
            summary=obs.summary,
            image_path=obs.image_path,
            width=obs.width,
            height=obs.height,
            meta=obs_meta,
        )

    def _maybe_commit_task_title(
        self, task_id: str, plan: Plan, *, step: int
    ) -> None:
        """首轮（或启发式占位时）用规划里的 title 落盘并推进度。"""
        from fawai_avatar.task.title import commit_task_title

        proposed = normalize_task_title(plan.title or "")
        source = "llm" if proposed else "heuristic"
        if not proposed:
            # 规划未给 title 时：若仍无标题，用 goal 启发式兜底一次
            task = self.supervisor.get_task(task_id)
            if task is None:
                return
            meta = dict(task.meta or {})
            if meta.get("title"):
                # 本任务已有标题，仍可能要把已有 llm 名提升到父计划会话
                existing = normalize_task_title(str(meta.get("title") or ""))
                existing_source = str(meta.get("title_source") or "")
                if existing and existing_source == "llm":
                    try:
                        from fawai_avatar.brain.plan_scheduler import (
                            promote_title_to_plan_parent,
                        )

                        if promote_title_to_plan_parent(
                            self.supervisor, task_id, existing, source="llm"
                        ):
                            self._progress(
                                f"任务命名：{existing}",
                                phase="title",
                                step=step,
                                title=existing,
                                title_source="llm",
                            )
                    except Exception:  # noqa: BLE001
                        pass
                return
            proposed = suggest_task_title(task.goal)
            source = "heuristic"

        written = commit_task_title(
            self.supervisor, task_id, proposed, source=source
        )
        named = written or proposed
        promoted = ""
        # 计划子任务正式名：提升到父会话 meta.title，避免刷新后仍显示启发式路径截断
        if source == "llm" and named:
            try:
                from fawai_avatar.brain.plan_scheduler import (
                    promote_title_to_plan_parent,
                )

                promoted = promote_title_to_plan_parent(
                    self.supervisor, task_id, named, source="llm"
                )
            except Exception:  # noqa: BLE001
                promoted = ""
        if not written and not promoted:
            return
        self._progress(
            f"任务命名：{named}",
            phase="title",
            step=step,
            title=named,
            title_source=source,
        )

    def _observe(
        self,
        goal: str,
        *,
        methods: list[str] | None = None,
        observe_args: dict[str, Any] | None = None,
        on_progress: Any | None = None,
        progress_base: dict[str, Any] | None = None,
    ) -> Observation:
        chosen = methods or choose_observe_methods(goal, capture=self.capture)
        store = getattr(self.controller, "system_log", None)

        def _screen_observe(
            *,
            screen_target: str | None = None,
            displays: list[dict[str, Any]] | None = None,
        ) -> Observation:
            from fawai_avatar.observe.screen import observe as screen_observe

            try:
                obs = screen_observe(
                    screen_target=screen_target, displays=displays
                )
            except Exception as exc:  # noqa: BLE001 — 记日志后抛回
                if store is not None and hasattr(store, "log_screenshot"):
                    try:
                        store.log_screenshot(
                            path=None,
                            ok=False,
                            message=str(exc),
                            task_id=self._current_task_id,
                            actor="runtime",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                raise
            if store is not None and hasattr(store, "log_screenshot"):
                try:
                    store.log_screenshot(
                        path=obs.image_path,
                        width=obs.width,
                        height=obs.height,
                        ok=True,
                        message=obs.summary or "screenshot",
                        task_id=self._current_task_id,
                        actor="runtime",
                    )
                except Exception:  # noqa: BLE001 — 日志失败不影响观察
                    pass
            return obs

        obs = collect_observation(
            chosen,
            screen_observe=_screen_observe,
            goal=goal,
            capture=self.capture,
            observe_args=observe_args,
            on_progress=on_progress,
            progress_base=progress_base,
        )
        meta = obs.meta or {}
        if obs.image_path and (
            meta.get("origin_x") is not None or meta.get("origin_y") is not None
        ):
            frame: dict[str, float] = {
                "origin_x": float(meta.get("origin_x") or 0),
                "origin_y": float(meta.get("origin_y") or 0),
            }
            for key in (
                "image_width",
                "image_height",
                "capture_width",
                "capture_height",
                "coord_scale_x",
                "coord_scale_y",
            ):
                if meta.get(key) is not None:
                    try:
                        frame[key] = float(meta[key])
                    except (TypeError, ValueError):
                        pass
            if obs.width is not None and "image_width" not in frame:
                frame["image_width"] = float(obs.width)
            if obs.height is not None and "image_height" not in frame:
                frame["image_height"] = float(obs.height)
            self._last_screen_frame = frame
            self._last_screen_origin = {
                "origin_x": frame["origin_x"],
                "origin_y": frame["origin_y"],
            }
        return obs

    def _verify_file_state(
        self, actions: list[Action], results: list[ExecResult]
    ) -> list[ExecResult]:
        bg = getattr(self.controller, "_background", None)
        if bg is None or not hasattr(bg, "fs"):
            return []
        from fawai_avatar.observe.verify import verify_actions_file_state

        return verify_actions_file_state(bg.fs.work_root, actions, results)

    def _verify_ui_ax(
        self, actions: list[Action], results: list[ExecResult]
    ) -> list[ExecResult]:
        from fawai_avatar.observe.verify import verify_actions_ui_ax

        if getattr(self.controller, "dry_run", False):
            return []
        try:
            return verify_actions_ui_ax(actions, results)
        except Exception as exc:  # noqa: BLE001
            return [
                ExecResult(
                    ok=False,
                    message=f"ui_verify exception: {exc}",
                    data={"error": str(exc)},
                )
            ]

    def _run_fast_delegate(
        self,
        task_id: str,
        *,
        task_goal: str,
        spec: Any,
        model: str,
        history: list[dict[str, Any]],
        traces: list[StepTrace],
        write_set: list[str],
        start_step: int,
        pending_observe_kinds: list[str] | None,
        replan_count: int,
        reflect_count: int,
        verify_results: list[Any],
        last_reflection: dict[str, Any] | None,
        persist: Callable[..., None],
        pending_observe_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """快模型子闭环：观察→规划→执行；失败/收束立刻回灌主脑（不做段内反省换道）。"""
        from fawai_avatar.brain.delegate import DelegateSpec

        assert isinstance(spec, DelegateSpec)
        sub_goal = (
            f"{task_goal}\n\n【委派子目标】{spec.goal}\n"
            f"【停条件】{spec.stop_when or '子目标完成或无法继续'}"
        )
        step = int(start_step)
        steps_used = 0
        last_summary = ""
        outcome = "max_steps"
        pending = list(pending_observe_kinds) if pending_observe_kinds else None
        pending_args = (
            dict(pending_observe_args) if isinstance(pending_observe_args, dict) else None
        )
        fast_compact: dict[str, Any] = {}

        for _ in range(int(spec.max_steps)):
            halted = self._halt_if_stopped(
                task_id,
                step=step,
                traces=traces,
                replan_count=replan_count,
                reflect_count=reflect_count,
                verify_results=verify_results,
                last_reflection=last_reflection,
                write_set=write_set,
            )
            if halted is not None:
                return {
                    "history": history,
                    "traces": traces,
                    "write_set": write_set,
                    "step": step,
                    "pending_observe_kinds": pending,
                    "pending_observe_args": pending_args,
                    "outcome": "halted",
                    "summary": last_summary or "任务已停止",
                    "steps_used": steps_used,
                    "halted": halted,
                }
            if not self._can_continue(step):
                outcome = "max_steps"
                last_summary = last_summary or "达到全局步数上限"
                break

            methods = resolve_observe_methods(
                sub_goal,
                capture=self.capture,
                requested=pending,
            )
            if is_env_observe_skipped(methods):
                obs = skipped_env_observation(
                    goal=sub_goal,
                    capture=self.capture,
                    extra_meta={"delegate_tier": "fast"},
                )
                obs = self._attach_title_context(task_id, obs)
            else:
                self._progress(
                    progress_observe_text(methods, pending=True),
                    phase="observe",
                    step=step,
                    observe_methods=methods,
                    observe_label="、".join(
                        observe_label(m) for m in methods
                    ),
                    delegate_tier="fast",
                )
                t_obs = time.perf_counter()
                progress_base = {"step": step, "delegate_tier": "fast"}

                def _observe_progress_fast(
                    text: str, *, phase: str, **extra: Any
                ) -> None:
                    self._progress(text, phase=phase, **extra)

                obs = self._observe(
                    sub_goal,
                    methods=methods,
                    observe_args=pending_args,
                    on_progress=_observe_progress_fast,
                    progress_base=progress_base,
                )
                obs = self._attach_title_context(task_id, obs)
                obs_ms = int((time.perf_counter() - t_obs) * 1000)
                obs_meta = obs.meta or {}
                done_methods = list(
                    obs_meta.get("observe_methods") or methods
                )
                stages = {
                    str(x)
                    for x in (obs_meta.get("progress_stages") or [])
                    if x
                }
                if "observe" not in stages:
                    self._progress(
                        progress_observe_text(
                            done_methods,
                            pending=False,
                            preview=str(obs.summary or ""),
                        ),
                        phase="observe",
                        step=step,
                        elapsed_ms=obs_ms,
                        observe_methods=done_methods,
                        observe_method=obs_meta.get("observe_method"),
                        observe_label=obs_meta.get("observe_label"),
                        paths=_paths_from_observation(obs),
                        frontmost_app=obs_meta.get("frontmost_app"),
                        frontmost_window=obs_meta.get("frontmost_window"),
                        frontmost_ok=obs_meta.get("frontmost_ok"),
                        delegate_tier="fast",
                    )
                else:
                    self._progress(
                        progress_observe_text(
                            ["obs_screen"],
                            pending=False,
                            preview=str(
                                (obs.summary or "").split("OCR 观察")[0]
                            ).strip(),
                        ),
                        phase="observe",
                        step=step,
                        elapsed_ms=int(
                            obs_meta.get("capture_elapsed_ms") or obs_ms
                        ),
                        observe_methods=["obs_screen"],
                        observe_method="obs_screen",
                        observe_label=observe_label("obs_screen"),
                        paths=_paths_from_observation(obs),
                        frontmost_app=obs_meta.get("frontmost_app"),
                        frontmost_window=obs_meta.get("frontmost_window"),
                        frontmost_ok=obs_meta.get("frontmost_ok"),
                        delegate_tier="fast",
                    )

            self._progress(
                "快模型规划中…",
                phase="plan",
                step=step,
                model=model,
                delegate_tier="fast",
            )
            fast_live = self._llm_live(
                task_id,
                phase="plan",
                step=step,
                model=model,
                delegate_tier="fast",
            )
            plan, plan_cancelled = self._call_cancellable(
                task_id,
                lambda hc=fast_compact: self.brain.plan(
                    sub_goal,
                    obs,
                    history=history,
                    model=model,
                    planner_role="fast",
                    on_llm_delta=fast_live.on_delta,
                    history_compact=hc,
                    allow_todos=False,
                ),
                label="plan_fast",
            )
            fast_compact, _todos = self._take_plan_memory(
                plan,
                history_compact=fast_compact,
                step_todos=[],
                allow_todos=False,
            )
            fast_live.flush()
            if plan_cancelled:
                return {
                    "history": history,
                    "traces": traces,
                    "write_set": write_set,
                    "step": step,
                    "pending_observe_kinds": pending,
                    "pending_observe_args": pending_args,
                    "outcome": "cancelled",
                    "summary": "cancelled",
                    "steps_used": steps_used,
                    "halted": self._finish(
                        task_id,
                        ok=False,
                        error="cancelled",
                        traces=traces,
                        replan_count=replan_count,
                        reflect_count=reflect_count,
                        verify_results=verify_results,
                        final_thought=None,
                        last_reflection=last_reflection,
                        write_set=write_set,
                    ),
                }
            assert plan is not None
            steps_used += 1
            self._stats_note_plan(task_id, plan)
            pending = list(plan.observe_kinds) or None
            pending_args = dict(plan.observe_args) if plan.observe_args else None
            last_summary = (plan.thought or "").strip()
            history.append(
                {
                    "role": "assistant",
                    "content": f"[fast] {plan.thought or plan.status}",
                }
            )

            if plan.status in {"success", "failed"}:
                traces.append(StepTrace(observation=obs, plan=plan, results=[]))
                outcome = plan.status
                persist(next_step=step + 1)
                step += 1
                break

            if plan.status == "need_input":
                # 快模型不应澄清；升为主脑处理
                traces.append(StepTrace(observation=obs, plan=plan, results=[]))
                outcome = "escalated"
                last_summary = last_summary or "快模型遇到歧义，交回主脑"
                persist(next_step=step + 1)
                step += 1
                break

            if not plan.actions:
                traces.append(StepTrace(observation=obs, plan=plan, results=[]))
                outcome = "empty"
                last_summary = last_summary or "快模型空 actions，交回主脑"
                persist(next_step=step + 1)
                step += 1
                break

            from fawai_avatar.runtime.api_wait import ApiWaitProgress

            fast_act_text = _format_act_progress(plan.actions)
            fast_act_extra = _actions_progress_meta(plan.actions)
            fast_act_waiter = ApiWaitProgress(
                self._progress,
                phase="act",
                step=step,
                waiting_text=fast_act_text,
                interval_sec=2.0,
                cancelled=lambda: self._task_control(task_id) == "cancelled",
                mark_api_wait=False,
                delegate_tier="fast",
                **fast_act_extra,
            )
            fast_act_waiter.start()
            t_fast_act = time.perf_counter()
            try:
                results = self._execute_actions(
                    task_id, plan.actions, write_set=write_set
                )
            finally:
                fast_act_waiter.stop()
            fast_act_ms = int((time.perf_counter() - t_fast_act) * 1000)
            for action in plan.actions:
                for p in action.write_resources:
                    if p not in write_set:
                        write_set.append(p)
            tool_obs, _tool_meta = format_tool_observation(
                plan.actions, results, goal=sub_goal
            )
            # 快模型路径无独立 observe_tool 卡时，仍推一条带动作墙钟的收口，避免只见结果无耗时
            self._progress(
                progress_tool_observe_text(
                    pending=False, preview=tool_obs
                ),
                phase="observe_tool",
                step=step,
                elapsed_ms=max(0, fast_act_ms),
                act_elapsed_ms=max(0, fast_act_ms),
                observe_method="obs_tool",
                observe_label="工具结果",
                delegate_tier="fast",
            )
            history.append(
                {
                    "role": "user",
                    "content": f"执行结果（工具结果观察·快模型）：\n{tool_obs}",
                }
            )
            traces.append(StepTrace(observation=obs, plan=plan, results=results))
            if any(not r.ok for r in results):
                fail_msgs = "; ".join(
                    (r.message or "fail")[:160] for r in results if not r.ok
                )
                outcome = "action_failed"
                last_summary = (
                    f"{last_summary}\n动作失败：{fail_msgs}".strip()
                    if last_summary
                    else f"动作失败：{fail_msgs}"
                )
                persist(next_step=step + 1)
                step += 1
                break
            persist(next_step=step + 1)
            step += 1
        else:
            outcome = "max_steps"
            last_summary = last_summary or f"快模型已用满 {spec.max_steps} 步"

        return {
            "history": history,
            "traces": traces,
            "write_set": write_set,
            "step": step,
            "pending_observe_kinds": pending,
            "pending_observe_args": pending_args,
            "outcome": outcome,
            "summary": last_summary,
            "steps_used": steps_used,
            "halted": None,
        }

    def _execute_actions(
        self,
        task_id: str,
        actions: list[Action],
        *,
        write_set: list[str] | None = None,
        parallel: bool = False,
    ) -> list[ExecResult]:
        if parallel and len(actions) >= 2:
            return self._execute_actions_parallel(
                task_id, actions, write_set=write_set
            )
        results: list[ExecResult] = []
        for action in actions:
            # 动作间隙协作停步：取消后不再开新动作（当前正在跑的 shell 仍可能跑完）
            state = self._task_control(task_id)
            if state == "paused":
                step_no = int(getattr(self, "_current_step", 0) or 0)
                self._save_run_checkpoint(task_id, next_step=step_no)
                self._progress(
                    "任务已暂停，等待恢复…",
                    phase="paused",
                    status="paused",
                )
                while self._task_control(task_id) == "paused":
                    time.sleep(0.25)
                state = self._task_control(task_id)
            if state == "cancelled":
                results.append(ExecResult(ok=False, message="cancelled"))
                break
            if state not in {"ok"}:
                results.append(ExecResult(ok=False, message=f"stopped:{state}"))
                break
            action, result = self._execute_one_action(
                task_id, action, write_set=write_set
            )
            results.append(result)
            # expect 失败视为衔接中断：无论只读与否都停批
            if (not result.ok) and str(result.message or "").startswith(
                "expect failed:"
            ):
                break
            if should_break_action_batch(action, result):
                break
        return results

    def _execute_actions_parallel(
        self,
        task_id: str,
        actions: list[Action],
        *,
        write_set: list[str] | None = None,
    ) -> list[ExecResult]:
        """不冲突的一批动作并行；索引类先于磁盘扫；取消后丢弃未完成结果。"""
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        from pathlib import Path

        state = self._task_control(task_id)
        if state == "cancelled":
            return [ExecResult(ok=False, message="cancelled") for _ in actions]
        if state not in {"ok", "paused"}:
            return [
                ExecResult(ok=False, message=f"stopped:{state}") for _ in actions
            ]
        while self._task_control(task_id) == "paused":
            time.sleep(0.25)
        if self._task_control(task_id) == "cancelled":
            return [ExecResult(ok=False, message="cancelled") for _ in actions]

        prepared: list[Action] = []
        for action in actions:
            kind = (action.kind or "").strip().lower()
            if kind == "code_check":
                from fawai_avatar.workspace.check import fill_code_check_args

                filled = fill_code_check_args(action.args, write_set=write_set)
                if filled != dict(action.args or {}):
                    action = action.evolve(args=filled)
            if "task_id" not in (action.args or {}):
                action = action.evolve(
                    args={**dict(action.args), "task_id": task_id},
                )
            prepared.append(action)

        index_kinds = {
            "lore_search",
            "name_index_search",
            "fts_search",
        }
        disk_kinds = {"fs_grep", "explore_search"}
        index_idxs = [
            i
            for i, a in enumerate(prepared)
            if (a.kind or "").strip().lower() in index_kinds
        ]
        disk_idxs = [
            i
            for i, a in enumerate(prepared)
            if (a.kind or "").strip().lower() in disk_kinds
        ]
        other_idxs = [
            i
            for i in range(len(prepared))
            if i not in index_idxs and i not in disk_idxs
        ]
        # 同批既有索引又有磁盘扫 → 先索引；否则整批并行
        defer_disk = bool(index_idxs) and bool(disk_idxs)
        wave1 = index_idxs + other_idxs if defer_disk else list(range(len(prepared)))
        wave2 = disk_idxs if defer_disk else []

        results: list[ExecResult | None] = [None] * len(prepared)
        cancelled_batch = False

        def _run_one(idx: int, act: Action) -> tuple[int, ExecResult]:
            if self._task_control(task_id) == "cancelled":
                return idx, ExecResult(ok=False, message="cancelled")
            bg = getattr(self.controller, "_background", None)
            if bg is not None:
                bg.task_id = task_id
                live = getattr(self, "_live_history", None)
                if isinstance(live, list):
                    bg.history_override = list(live)
                self._bind_success_ledger_hooks(task_id)
            try:
                result = self.controller.execute(act, task_id=task_id)
            except Exception as exc:  # noqa: BLE001
                result = ExecResult(
                    ok=False,
                    message=f"parallel execute error: {exc}",
                    data={"exc_type": type(exc).__name__},
                )
            if self._task_control(task_id) == "cancelled":
                return idx, ExecResult(ok=False, message="cancelled")
            result = apply_expect_to_result(act, result)
            return idx, result

        def _run_wave(indices: list[int]) -> bool:
            """跑一波；若已取消返回 True。"""
            nonlocal cancelled_batch
            if not indices or cancelled_batch:
                return cancelled_batch
            if self._task_control(task_id) == "cancelled":
                cancelled_batch = True
                for i in indices:
                    if results[i] is None:
                        results[i] = ExecResult(ok=False, message="cancelled")
                return True
            workers = min(parallel_worker_count(), len(indices))
            pool = ThreadPoolExecutor(max_workers=workers)
            futs = {pool.submit(_run_one, i, prepared[i]): i for i in indices}
            pending = set(futs)
            try:
                while pending:
                    if self._task_control(task_id) == "cancelled":
                        cancelled_batch = True
                        for fut in list(pending):
                            fut.cancel()
                        for i in indices:
                            if results[i] is None:
                                results[i] = ExecResult(
                                    ok=False, message="cancelled"
                                )
                        pool.shutdown(wait=False, cancel_futures=True)
                        return True
                    done, pending = wait(
                        pending, timeout=0.2, return_when=FIRST_COMPLETED
                    )
                    for fut in done:
                        try:
                            idx, result = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            idx = futs[fut]
                            result = ExecResult(
                                ok=False,
                                message=f"parallel execute error: {exc}",
                            )
                        if (
                            cancelled_batch
                            or self._task_control(task_id) == "cancelled"
                        ):
                            cancelled_batch = True
                            results[idx] = ExecResult(
                                ok=False, message="cancelled"
                            )
                        else:
                            results[idx] = result
            finally:
                if not cancelled_batch:
                    pool.shutdown(wait=True)
            return cancelled_batch

        def _index_hit_paths() -> list[str]:
            paths: list[str] = []
            for i in index_idxs:
                res = results[i]
                if res is None or not res.ok:
                    continue
                data = res.data if isinstance(res.data, dict) else {}
                hits = data.get("hits")
                if isinstance(hits, list):
                    for h in hits:
                        if isinstance(h, dict) and h.get("path"):
                            paths.append(str(h["path"]))
                for p in data.get("paths") or []:
                    if p:
                        paths.append(str(p))
            return paths

        def _path_is_broad(raw_path: str) -> bool:
            p = (raw_path or ".").strip() or "."
            if p in {".", "", "/", "/Users", "/home"}:
                return True
            try:
                bg = getattr(self.controller, "_background", None)
                root = getattr(getattr(bg, "fs", None), "work_root", None)
                if root is not None and Path(p).expanduser().resolve() == Path(
                    root
                ).resolve():
                    return True
            except Exception:
                return p in {".", ""}
            return False

        def _narrow_disk_actions(hit_paths: list[str]) -> None:
            """索引有命中时：宽根磁盘动作改为父目录，或跳过。"""
            if not hit_paths:
                return
            parents: list[str] = []
            for p in hit_paths:
                try:
                    parent = str(Path(p).parent).strip() or "."
                except Exception:
                    continue
                if parent not in {".", "", "/"} and parent not in parents:
                    parents.append(parent)
                if len(parents) >= 3:
                    break
            narrow = parents[0] if len(parents) == 1 else None
            for i in disk_idxs:
                act = prepared[i]
                args = dict(act.args or {})
                raw_path = str(args.get("path") or ".").strip() or "."
                if not _path_is_broad(raw_path):
                    continue
                if narrow:
                    args["path"] = narrow
                    prepared[i] = act.evolve(args=args)
                else:
                    results[i] = ExecResult(
                        ok=True,
                        message=(
                            "skipped disk search: index hits present; "
                            "use hit paths / task_context instead of broad fs_grep"
                        ),
                        data={
                            "skipped": True,
                            "reason": "index_hits",
                            "hit_paths": hit_paths[:12],
                        },
                    )

        if _run_wave(wave1):
            return [
                r if r is not None else ExecResult(ok=False, message="cancelled")
                for r in results
            ]

        if wave2:
            hits = _index_hit_paths()
            if hits:
                _narrow_disk_actions(hits)
            still = [i for i in wave2 if results[i] is None]
            _run_wave(still)

        out: list[ExecResult] = []
        for r in results:
            out.append(
                r
                if r is not None
                else ExecResult(ok=False, message="parallel slot missing")
            )
        return out

    def _execute_one_action(
        self,
        task_id: str,
        action: Action,
        *,
        write_set: list[str] | None = None,
    ) -> tuple[Action, ExecResult]:
        """执行单动作（串行路径）；租约失败时仍返回 (action, failed result)。"""
        if action.needs_foreground:
            if not self.supervisor.holding_foreground(task_id):
                claim = self.supervisor.claim_foreground_result(task_id)
                if not claim.ok:
                    reason = str(claim.reason or "")
                    if reason.startswith("task_not_claimable"):
                        status = reason.split(":", 1)[-1] if ":" in reason else "?"
                        msg = (
                            f"cannot claim foreground_lease: task status={status} "
                            "(need running|waiting；非目标 App 聚焦失败)"
                        )
                    elif reason == "foreground_held_by_other" or claim.holder_task_id:
                        holder = claim.holder_task_id or "?"
                        msg = (
                            f"cannot claim foreground_lease: held by other task "
                            f"{str(holder)[:8]}…"
                        )
                    elif reason == "task_missing":
                        msg = "cannot claim foreground_lease: task missing"
                    else:
                        msg = f"cannot claim foreground_lease: {reason or 'unknown'}"
                    return action, ExecResult(
                        ok=False,
                        message=msg,
                        data={
                            "reason": reason,
                            "holder_task_id": claim.holder_task_id,
                        },
                    )
        if action.write_resources:
            items = [
                ResourceItem(kind="file", key=path, mode="write")
                for path in action.write_resources
            ]
            if not self.supervisor.allows_writes(
                task_id, list(action.write_resources)
            ):
                if not self.supervisor.claim_resources(task_id, items):
                    return action, ExecResult(
                        ok=False, message="cannot claim resource_lease"
                    )
        kind = (action.kind or "").strip().lower()
        # 空 code_check：用本任务已写路径回填，避免「写后空调 check」假失败
        if kind == "code_check":
            from fawai_avatar.workspace.check import fill_code_check_args

            filled = fill_code_check_args(action.args, write_set=write_set)
            if filled != dict(action.args or {}):
                action = action.evolve(args=filled)
        # click 族：注入最近截屏 origin / 尺寸 / Retina scale（图内 → 全局）
        if kind in {"click", "double_click", "right_click"}:
            args = dict(action.args or {})
            space = str(
                args.get("coord_space") or args.get("coords") or "image"
            ).strip().lower()
            frame = self._last_screen_frame or self._last_screen_origin
            if (
                space not in {"global", "screen", "absolute", "desktop"}
                and frame
            ):
                injected = dict(args)
                if injected.get("origin_x") is None and injected.get("origin_y") is None:
                    injected["origin_x"] = frame.get("origin_x", 0)
                    injected["origin_y"] = frame.get("origin_y", 0)
                for key in (
                    "image_width",
                    "image_height",
                    "capture_width",
                    "capture_height",
                    "coord_scale_x",
                    "coord_scale_y",
                ):
                    if injected.get(key) is None and frame.get(key) is not None:
                        injected[key] = frame[key]
                if injected != args:
                    action = action.evolve(args=injected)
        # confirm 模式 / 会话 Diff 需要 task_id
        if "task_id" not in (action.args or {}):
            action = action.evolve(
                args={**dict(action.args), "task_id": task_id},
            )
        bg = getattr(self.controller, "_background", None)
        if bg is not None:
            bg.task_id = task_id
            # 注入本任务 live history，供 task_context_* 优先于检查点
            live = getattr(self, "_live_history", None)
            if isinstance(live, list):
                bg.history_override = list(live)
            self._bind_success_ledger_hooks(task_id)
        result = self.controller.execute(action, task_id=task_id)
        result = apply_expect_to_result(action, result)
        return action, result


def run_worker_once(
    goal: str,
    *,
    data_dir: str = "data",
    dry_run: bool = True,
    mock_brain: bool = True,
    capture: bool = False,
    max_steps: int | None = None,
) -> dict[str, Any]:
    import os

    from fawai_avatar.config import max_steps_from_env

    if mock_brain:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"
    supervisor = Supervisor(data_dir)
    task_id = supervisor.create_task(goal)
    steps = max_steps_from_env(default=0) if max_steps is None else max_steps
    loop = WorkerLoop(
        supervisor,
        brain=Brain(),
        controller=Controller(dry_run=dry_run, supervisor=supervisor),
        max_steps=steps,
        capture=capture,
    )
    return loop.run(task_id)


def _results_indicate_stop(results: list[ExecResult]) -> bool:
    for r in results:
        msg = (r.message or "").strip()
        if msg == "cancelled" or msg.startswith("stopped:"):
            return True
    return False


def _llm_raw_progress_meta(
    raw: dict[str, Any] | None, *, elapsed_ms: int | None = None
) -> dict[str, Any]:
    """单次 LLM 的 usage / 模型 / 思考 / 耗时，供步骤簇汇总 token。"""
    meta: dict[str, Any] = {}
    if isinstance(raw, dict):
        usage = raw.get("usage")
        if isinstance(usage, dict) and usage:
            meta["tokens"] = usage
        model = raw.get("model")
        if model:
            meta["model"] = model
        thinking = raw.get("thinking")
        if isinstance(thinking, dict) and thinking:
            meta["thinking"] = thinking
    if elapsed_ms is not None:
        meta["elapsed_ms"] = max(0, int(elapsed_ms))
    return meta


def _llm_progress_meta(plan: Plan, *, fallback_elapsed_ms: int) -> dict[str, Any]:
    raw = plan.raw or {}
    meta: dict[str, Any] = {}
    usage = raw.get("usage")
    if isinstance(usage, dict) and usage:
        meta["tokens"] = usage
    elapsed = raw.get("elapsed_ms")
    meta["elapsed_ms"] = int(elapsed if elapsed is not None else fallback_elapsed_ms)
    if raw.get("model"):
        meta["model"] = raw.get("model")
    thinking = raw.get("thinking")
    if isinstance(thinking, dict) and thinking:
        meta["thinking"] = thinking
    thinking_text = raw.get("thinking_text")
    if isinstance(thinking_text, str) and thinking_text.strip():
        meta["thinking_text"] = thinking_text.strip()
    profile = raw.get("prompt_profile")
    if isinstance(profile, str) and profile.strip():
        meta["prompt_profile"] = profile.strip()
    if "attach_user_images" in raw:
        meta["attach_user_images"] = bool(raw.get("attach_user_images"))
    return meta


def _action_paths(action: Action) -> list[str]:
    paths: list[str] = []
    for p in action.write_resources or []:
        s = str(p).strip()
        if s and s not in paths:
            paths.append(s)
    args = action.args or {}
    for key in ("path", "target", "file", "src", "dst"):
        val = args.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s and s not in paths:
            paths.append(s)
    multi = args.get("paths")
    if isinstance(multi, list):
        for item in multi:
            s = str(item).strip()
            if s and s not in paths:
                paths.append(s)
    return paths


def _action_cmd(action: Action) -> str | None:
    args = action.args or {}
    cmd = args.get("cmd") or args.get("command")
    if cmd is None and isinstance(args.get("argv"), list) and args["argv"]:
        cmd = " ".join(str(x) for x in args["argv"])
    if cmd is None:
        return None
    text = str(cmd).strip()
    return text or None


def _action_progress_meta(action: Action) -> dict[str, Any]:
    meta: dict[str, Any] = {"tool": (action.kind or "").strip() or "?"}
    cmd = _action_cmd(action)
    if cmd:
        meta["cmd"] = _clip_progress_text(cmd, max_len=400)
    paths = _action_paths(action)
    if paths:
        meta["paths"] = paths[:12]
    return meta


def _actions_progress_meta(actions: list[Action]) -> dict[str, Any]:
    if not actions:
        return {}
    commands = [
        summarize_action_line(a.kind or "", a.args or {}, max_len=240) for a in actions[:8]
    ]
    if len(actions) == 1:
        meta = _action_progress_meta(actions[0])
        if commands:
            meta["commands"] = commands
        return meta
    tools = [(a.kind or "?").strip() or "?" for a in actions]
    paths: list[str] = []
    cmds: list[str] = []
    for a in actions:
        for p in _action_paths(a):
            if p not in paths:
                paths.append(p)
        c = _action_cmd(a)
        if c:
            cmds.append(_clip_progress_text(c, max_len=200))
    meta: dict[str, Any] = {"tool": ", ".join(tools[:8])}
    if commands:
        meta["commands"] = commands
    if cmds:
        meta["cmd"] = " | ".join(cmds[:4])
    if paths:
        meta["paths"] = paths[:12]
    return meta


def _paths_from_observation(obs: Observation) -> list[str]:
    paths: list[str] = []
    if obs.image_path:
        paths.append(str(obs.image_path))
    root = (obs.meta or {}).get("work_root")
    if root:
        paths.append(str(root))
    return paths


def _clip_progress_text(text: str, *, max_len: int = 320) -> str:
    from fawai_avatar.channel.redact import redact_secrets

    s = " ".join(redact_secrets(str(text)).split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# 会话上要露出的参数。大段正文（content/old/new）不进这一行。
_ACTION_LINE_FIRST = (
    "pattern",
    "glob",
    "q",
    "query",
    "queries",
    "path",
    "paths",
    "target",
    "file",
    "src",
    "dst",
    "head_limit",
    "offset",
    "limit",
    "script",
    "module",
    "packages",
    "keys",
    "text",
    "seconds",
    "command",
)
_ACTION_LINE_SKIP = {
    "content",
    "old",
    "new",
    "hunks",
    "doc_refs",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "cmd",
    "argv",
}


def _fmt_action_arg(key: str, value: Any, *, max_len: int) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, list):
        bits = [str(x).strip() for x in value[:6] if str(x).strip()]
        if not bits:
            return ""
        text = ",".join(bits)
        if len(value) > 6:
            text += ",…"
    elif isinstance(value, dict):
        return ""
    else:
        text = str(value).replace("\n", " ").strip()
    if not text:
        return ""
    text = _clip_progress_text(text, max_len=max_len)
    if any(ch in text for ch in " \t\"'"):
        text = '"' + text.replace('"', "'") + '"'
    return f"{key}={text}"


def summarize_action_line(kind: str, args: dict[str, Any] | None, *, max_len: int = 320) -> str:
    """一条动作的会话摘要：种类 + 能区分这次调用的参数（pattern/path/命令）。"""
    name = (kind or "?").strip() or "?"
    raw = args if isinstance(args, dict) else {}
    if name in {"shell", "shell_verify"}:
        cmd = raw.get("cmd") or raw.get("command")
        if cmd is None and isinstance(raw.get("argv"), list) and raw["argv"]:
            cmd = " ".join(str(x) for x in raw["argv"])
        if cmd is not None and str(cmd).strip():
            return f"{name}: {_clip_progress_text(str(cmd), max_len=max_len)}"
    parts: list[str] = []
    used: set[str] = set()
    for key in _ACTION_LINE_FIRST:
        if key not in raw:
            continue
        token = _fmt_action_arg(key, raw.get(key), max_len=160)
        if not token:
            continue
        parts.append(token)
        used.add(key)
        if len(parts) >= 6:
            break
    if len(parts) < 6:
        for key, val in raw.items():
            if key in used or key in _ACTION_LINE_SKIP:
                continue
            if isinstance(val, (dict, list)) and key not in {
                "paths",
                "keys",
                "queries",
                "packages",
                "files",
            }:
                continue
            token = _fmt_action_arg(str(key), val, max_len=80)
            if not token:
                continue
            parts.append(token)
            if len(parts) >= 6:
                break
    if not parts:
        return name
    line = f"{name}: {' '.join(parts)}"
    if len(line) <= max_len:
        return line
    return _clip_progress_text(line, max_len=max_len)


def _summarize_action(action: Action, *, max_len: int = 320) -> str:
    """会话时间线用的动作摘要：优先展示 shell 命令 / 检索式 / 路径等关键参数。"""
    return summarize_action_line(action.kind or "", action.args or {}, max_len=max_len)


def _format_act_progress(actions: list[Action]) -> str:
    if not actions:
        return "执行中"
    lines = [_summarize_action(a) for a in actions]
    if len(actions) == 1:
        return f"执行中：{lines[0]}"
    body = "\n".join(lines)
    if len(body) > 900:
        body = body[:899] + "…"
    return f"执行中：\n{body}"


def _guess_final_thought(steps: list[dict[str, Any]]) -> str:
    for step in reversed(steps):
        plan = (step or {}).get("plan") or {}
        thought = str(plan.get("thought") or "").strip()
        if thought:
            return thought
    return ""


def _clip_steps_for_meta(steps: list[dict[str, Any]], *, max_steps: int = 24) -> list[dict[str, Any]]:
    """落盘用：去掉观察大图路径噪音，限制步数与字段长度。"""
    clipped: list[dict[str, Any]] = []
    for step in steps[-max_steps:]:
        plan = dict((step or {}).get("plan") or {})
        thought = str(plan.get("thought") or "")[:800]
        actions = []
        for a in plan.get("actions") or []:
            if isinstance(a, dict):
                actions.append(
                    {
                        "kind": a.get("kind"),
                        "args": _clip_args(a.get("args") or {}),
                    }
                )
        results = []
        from fawai_avatar.channel.redact import redact_secrets

        for r in (step or {}).get("results") or []:
            if isinstance(r, dict):
                results.append(
                    {
                        "ok": r.get("ok"),
                        "message": redact_secrets(str(r.get("message") or ""))[:400],
                    }
                )
        clipped.append(
            {
                "plan": {
                    "thought": thought,
                    "status": plan.get("status"),
                    "done": plan.get("done"),
                    "actions": actions,
                },
                "results": results,
            }
        )
    return clipped


def _clip_args(args: dict[str, Any]) -> dict[str, Any]:
    from fawai_avatar.channel.redact import redact_secrets

    out: dict[str, Any] = {}
    for k, v in list(args.items())[:12]:
        key = str(k)
        if isinstance(v, str):
            text = redact_secrets(v)
            if key.lower() in {"cmd", "command", "password", "passwd", "secret"}:
                text = redact_secrets(text)
            if len(text) > 200:
                out[key] = text[:200] + "…"
            else:
                out[key] = text
        else:
            out[key] = v
    return out


def _traces_to_dict(traces: list[StepTrace]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, tr in enumerate(traces):
        out.append(
            {
                "step": int(i),
                "observation": tr.observation.to_dict() if tr.observation else None,
                "plan": tr.plan.to_dict() if tr.plan else None,
                "results": [r.to_dict() for r in tr.results],
            }
        )
    return out
