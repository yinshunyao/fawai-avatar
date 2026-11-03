# term:工作任务  doc:doc/议题/操作分身三层/结论.md
# term:会话通道  doc:doc/功能/会话通道/说明.md
# term:执行反省  doc:doc/功能/执行反省/说明.md
# term:步骤重复  doc:doc/功能/步骤重复/说明.md
# term:进程中断续跑  doc:doc/功能/进程中断续跑/说明.md
# term:守规约束  doc:doc/功能/守规约束/说明.md
# term:探索进度闸门  doc:doc/功能/编码闭环/说明.md
# term:弱写入闸门  doc:doc/功能/编码闭环/说明.md
# term:可见副作用闸门  doc:doc/功能/编码闭环/说明.md
# term:任务统计  doc:doc/功能/任务统计/说明.md
# term:执行中澄清  doc:doc/功能/执行中澄清/说明.md
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
    format_mutate_force_message,
    format_verify_read_message,
    format_wide_grep_message,
    hot_repeated_paths,
    locate_budget_should_inject,
    locate_budget_threshold,
    merge_locate_anchors,
    normalize_read_windows,
    step_repeats_banned_wide_grep,
    step_wide_grep_paths,
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
from fawai_avatar.brain.answer_effect import (
    answer_coverage_reject_reason,
    fail_message_for_answer_coverage,
    format_answer_coverage_prompt,
    format_unread_dump_prompt,
    goal_needs_answer_coverage_gate,
    prefer_fs_read_path,
    unread_compressed_dumps,
)
from fawai_avatar.brain.write_effect import (
    format_visible_effect_prompt,
    format_weak_write_message,
    goal_needs_visible_effect_gate,
    results_have_weak_write,
    thought_admits_no_visible_effect,
    thought_ok_for_visible_effect,
)
from fawai_avatar.brain.planner import Brain
from fawai_avatar.brain.reflect import (
    abort_should_deliver_commands,
    failed_action_fingerprints,
    failed_write_paths,
    format_abort_delivery,
    format_reflection_for_history,
    format_stagnation_for_history,
    hard_abort_stagnation,
    reflection_needs_verify_read,
    run_reflect,
    run_stagnation,
    soften_abort_when_mutated_format_error,
    step_fingerprint,
    step_repeat_enabled,
    step_repeat_threshold,
)
from fawai_avatar.control.executor import Controller
from fawai_avatar.observe.methods import (
    choose_observe_methods,
    collect_observation,
    observe_label,
    progress_observe_text,
    progress_tool_observe_text,
    resolve_observe_methods,
)
from fawai_avatar.observe.tool_digest import format_tool_observation
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
        """读总管状态：ok | cancelled | paused | gone | other。"""
        task = self.supervisor.get_task(task_id)
        if task is None:
            return "gone"
        status = (task.status or "").strip()
        if status == "cancelled":
            return "cancelled"
        if status == "paused":
            return "paused"
        if status in {"running", "waiting", "blocked", "pending"}:
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

    def _stats_progress_extra(self) -> dict[str, Any]:
        return {"stats": task_stats.summary_for_api(self._run_stats)}

    def _stats_load(self, task_id: str) -> None:
        try:
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
            self._run_stats = task_stats.empty_stats()

    def _stats_persist(self, task_id: str) -> None:
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
        task_stats.begin_run_segment(self._run_stats)
        self._stats_persist(task_id)

    def _stats_end_segment(self, task_id: str) -> None:
        task_stats.end_run_segment(self._run_stats)
        self._stats_persist(task_id)

    def _stats_note_plan(self, task_id: str, plan: Plan) -> None:
        raw = plan.raw if isinstance(plan.raw, dict) else {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        task_stats.apply_llm_call(self._run_stats, usage=usage)
        self._stats_persist(task_id)

    def _stats_note_reflection(self, task_id: str, reflection: Any) -> None:
        raw = getattr(reflection, "raw", None)
        if not isinstance(raw, dict):
            raw = {}
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        task_stats.apply_llm_call(self._run_stats, usage=usage)
        self._stats_persist(task_id)

    def _stats_note_step(self, task_id: str, actions: list[Action] | None) -> None:
        task_stats.apply_round(self._run_stats)
        task_stats.apply_tools(self._run_stats, actions)
        self._stats_persist(task_id)

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
            wide_grep_ban_paths = [
                str(x)
                for x in (resumed.get("wide_grep_ban_paths") or [])
                if str(x).strip()
            ]
            awaiting_user_answer = bool(resumed.get("awaiting_user_answer"))
            weak_write_pending = bool(resumed.get("weak_write_pending"))
            visible_effect_retries = int(resumed.get("visible_effect_retries") or 0)
            answer_coverage_retries = int(
                resumed.get("answer_coverage_retries") or 0
            )
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
            format_abort_softened = bool(resumed.get("format_abort_softened"))
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
            remote_idle_streak = 0
            remote_method_forced = False
            read_path_counts = {}
            read_windows = {}
            locate_anchors = []
            post_locate_streak = 0
            wide_grep_ban_paths = []
            awaiting_user_answer = False
            weak_write_pending = False
            visible_effect_retries = 0
            answer_coverage_retries = 0
            unread_dump_forced = False
            compressed_dump_paths = []
            read_ok_paths = []
            pending_observe_kinds = None
            format_abort_softened = False
            # 追问不再并入 goal：新 run 把 meta.followups 注入 history，大脑仍能看到增量
            history = self._seed_followup_history(task, history)
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
                remote_idle_streak=remote_idle_streak,
                remote_method_forced=remote_method_forced,
                read_path_counts=read_path_counts,
                read_windows=read_windows,
                locate_anchors=locate_anchors,
                post_locate_streak=post_locate_streak,
                wide_grep_ban_paths=wide_grep_ban_paths,
                awaiting_user_answer=awaiting_user_answer,
                weak_write_pending=weak_write_pending,
                visible_effect_retries=visible_effect_retries,
                answer_coverage_retries=answer_coverage_retries,
                unread_dump_forced=unread_dump_forced,
                compressed_dump_paths=compressed_dump_paths,
                read_ok_paths=read_ok_paths,
                pending_observe_kinds=pending_observe_kinds,
                last_reflection=last_reflection,
                format_abort_softened=format_abort_softened,
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
            reflect_fn = getattr(self.brain, "reflect", None)
            if callable(reflect_fn):
                reflection = reflect_fn(
                    task.goal,
                    actions,
                    results,
                    fingerprint=primary_fp,
                    fail_count=fail_count,
                    history=history,
                    prior_alternate_fps=prior_alternate_fps,
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
                )
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
            if reflection_needs_verify_read(reflection):
                history.append(
                    {
                        "role": "user",
                        "content": format_verify_read_message(
                            paths=failed_write_paths(actions, results)
                        ),
                    }
                )
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
                **self._stats_progress_extra(),
            )
            if reflection.decision == "abort" or not can_replan:
                abort_msg = reflection.cause or err_msg
                abort_thought = plan_for_reflect.thought
                if reflection.decision == "abort" and abort_should_deliver_commands(
                    reflection
                ):
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

            plan: Plan | None = None
            results: list[ExecResult] = []
            obs: Observation | None = None
            methods: list[str] = []
            crash_phase = "step"
            try:
                if awaiting_user_answer:
                    self._progress("整理面向用户的结论…", phase="finalize", step=step)

                crash_phase = "observe"
                methods = resolve_observe_methods(
                    task.goal,
                    capture=self.capture,
                    requested=pending_observe_kinds,
                )
                self._progress(
                    progress_observe_text(methods, pending=True),
                    phase="observe",
                    step=step,
                    observe_methods=methods,
                    observe_label="、".join(observe_label(m) for m in methods),
                )
                t_obs = time.perf_counter()
                obs = self._observe(task.goal, methods=methods)
                obs = self._attach_title_context(task_id, obs)
                obs_ms = int((time.perf_counter() - t_obs) * 1000)
                obs_meta = obs.meta or {}
                self._progress(
                    progress_observe_text(
                        list(obs_meta.get("observe_methods") or methods), pending=False
                    ),
                    phase="observe",
                    step=step,
                    elapsed_ms=obs_ms,
                    observe_methods=obs_meta.get("observe_methods") or methods,
                    observe_method=obs_meta.get("observe_method"),
                    observe_label=obs_meta.get("observe_label"),
                    paths=_paths_from_observation(obs),
                )

                crash_phase = "plan"
                self._progress("大脑规划中（等待模型）…", phase="plan", step=step)
                t_plan = time.perf_counter()
                plan, plan_cancelled = self._call_cancellable(
                    task_id,
                    lambda: self.brain.plan(task.goal, obs, history=history),
                    label="plan",
                )
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
                        observe_kinds=list(plan.observe_kinds),
                        title=plan.title or "",
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
                thought_preview = (plan.thought or "").strip()
                if len(thought_preview) > 200:
                    thought_preview = thought_preview[:200] + "…"
                planned_text = f"规划完成（{plan.status}）"
                if plan.observe_kinds:
                    labels = "、".join(observe_label(m) for m in plan.observe_kinds)
                    planned_text = f"{planned_text}；下一步观察：{labels}"
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

                if plan.actions:
                    act_extra = _actions_progress_meta(plan.actions)
                    self._progress(
                        _format_act_progress(plan.actions),
                        phase="act",
                        step=step,
                        **act_extra,
                    )
                crash_phase = "act"
                t_act = time.perf_counter()
                results = self._execute_actions(task_id, plan.actions)
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
                explore_only_streak = update_explore_only_streak(
                    explore_only_streak,
                    goal=task.goal,
                    write_set=write_set,
                    actions=plan.actions,
                    read_path_counts=read_path_counts,
                    read_windows=read_windows,
                    results=results,
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
                )
                wide_paths = step_wide_grep_paths(
                    plan.actions, results, hit_threshold=wide_grep_n
                )
                pending_wide_grep_msg: str | None = None
                pending_wide_grep_repeat = False
                if wide_paths:
                    for p in wide_paths:
                        if p not in wide_grep_ban_paths:
                            wide_grep_ban_paths.append(p)
                    pending_wide_grep_msg = format_wide_grep_message(
                        paths=wide_paths, hit_threshold=wide_grep_n
                    )
                elif wide_grep_ban_paths and step_repeats_banned_wide_grep(
                    actions=plan.actions, banned_paths=wide_grep_ban_paths
                ):
                    pending_wide_grep_msg = format_wide_grep_message(
                        paths=list(wide_grep_ban_paths),
                        hit_threshold=wide_grep_n,
                    )
                    pending_wide_grep_repeat = True
                    post_locate_streak = int(post_locate_streak) + 1
                # 文件态回证（窄场景：写成功后核对磁盘）
                file_verifies = self._verify_file_state(plan.actions, results)
                verify_results.extend(file_verifies)
                if any(not v.ok for v in file_verifies):
                    err = next(v.message for v in file_verifies if not v.ok)
                    results.append(ExecResult(ok=False, message=f"verify failed: {err}"))
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
                        )
                    self._progress(
                        prefix + body[:1200],
                        phase="acted",
                        step=step,
                        ok=r.ok,
                        **acted_extra,
                    )
                traces.append(StepTrace(observation=obs, plan=plan, results=results))
                self._stats_note_step(task_id, plan.actions)
                history.append(
                    {
                        "role": "assistant",
                        "content": plan.thought or plan.status,
                    }
                )
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
                    for part in tool_meta.get("parts") or []:
                        if not isinstance(part, dict):
                            continue
                        dump = str(part.get("dump_path") or "").strip()
                        if dump and dump not in compressed_dump_paths:
                            compressed_dump_paths.append(dump)
                        if part.get("compressed"):
                            tool_meta["compressed"] = True
                    tool_obs_ms = int((time.perf_counter() - t_tool_obs) * 1000)
                    self._progress(
                        progress_tool_observe_text(
                            pending=False,
                            compressed=bool(tool_meta.get("compressed")),
                            preview=tool_feedback,
                        ),
                        phase="observe_tool",
                        step=step,
                        elapsed_ms=tool_obs_ms,
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
                    # 命中观察被压缩也视为宽检索：补 path 到禁扫并灌降质
                    if (
                        not pending_wide_grep_msg
                        and wide_grep_n > 0
                        and any(
                            isinstance(part, dict)
                            and part.get("compressed")
                            and str(part.get("label") or "") == "hits"
                            for part in (tool_meta.get("parts") or [])
                        )
                    ):
                        grep_paths = []
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
                        for p in grep_paths:
                            if p not in wide_grep_ban_paths:
                                wide_grep_ban_paths.append(p)
                        pending_wide_grep_msg = format_wide_grep_message(
                            paths=grep_paths or list(wide_grep_ban_paths),
                            hit_threshold=wide_grep_n,
                        )
                else:
                    history.append(
                        {
                            "role": "user",
                            "content": "执行结果：本步无动作。",
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
                    reflect_fn = getattr(self.brain, "reflect", None)
                    if callable(reflect_fn):
                        reflection = reflect_fn(
                            task.goal,
                            plan.actions,
                            results,
                            fingerprint=primary_fp,
                            fail_count=fail_count,
                            history=history,
                            prior_alternate_fps=prior_alternate_fps,
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
                        )
                    reflect_count += 1
                    # D：已有写入 + 纯参数格式失败 → 降 abort 为 alternate（每 run 至多一次）
                    if (
                        reflection.decision == "abort"
                        and write_set
                        and not format_abort_softened
                    ):
                        softened = soften_abort_when_mutated_format_error(
                            reflection,
                            write_set=write_set,
                            results=results,
                        )
                        if softened.decision != "abort":
                            reflection = softened
                            format_abort_softened = True
                            self.supervisor.report(
                                task_id,
                                "reflect: soften_format_abort",
                                {
                                    "write_set": list(write_set)[:8],
                                    "reflection": reflection.to_dict(),
                                },
                            )
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
                    # patch miss / 缺 path / 截断锚点：临时放开定向只读，避免 mutate 面挡住补读后直接 failed
                    if reflection_needs_verify_read(reflection):
                        verify_msg = format_verify_read_message(
                            paths=failed_write_paths(plan.actions, results)
                        )
                        history.append({"role": "user", "content": verify_msg})
                        self.supervisor.report(
                            task_id,
                            "reflect: verify_read",
                            {
                                "paths": failed_write_paths(plan.actions, results)[:8],
                                "decision": reflection.decision,
                            },
                        )
                        self._progress(
                            verify_msg[:200],
                            phase="reflect",
                            step=step,
                            decision="verify_read",
                            reflect_count=reflect_count,
                        )
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
                        **self._stats_progress_extra(),
                    )

                    if reflection.decision == "abort" or not can_replan:
                        abort_msg = reflection.cause or err
                        abort_thought = plan.thought
                        if reflection.decision == "abort" and abort_should_deliver_commands(
                            reflection
                        ):
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

                if locate_budget_should_inject(
                    streak=post_locate_streak, budget=locate_budget_n
                ):
                    locate_msg = format_locate_budget_message(
                        streak=post_locate_streak,
                        budget=locate_budget_n,
                        anchors=locate_anchors,
                    )
                    history.append({"role": "user", "content": locate_msg})
                    self.supervisor.report(
                        task_id,
                        "locate_budget: inject",
                        {
                            "post_locate_streak": post_locate_streak,
                            "budget": locate_budget_n,
                            "anchors": locate_anchors[:8],
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
                if (
                    step_repeat_enabled()
                    and plan.actions
                    and results
                    and all(r.ok for r in results)
                ):
                    step_fp = step_fingerprint(
                        plan.actions, observe_methods=list(methods)
                    )
                    if step_fp:
                        step_fp_counts[step_fp] += 1
                        hit_count = int(step_fp_counts[step_fp])
                        force_hard = step_fp in prior_judged_step_fps
                        should_judge = force_hard or hit_count >= step_repeat_n
                        if should_judge:
                            if force_hard:
                                reflection = hard_abort_stagnation(
                                    step_fp, hit_count=hit_count
                                )
                            else:
                                self._progress(
                                    "检测到步骤重复，裁决中…",
                                    phase="stagnation",
                                    step=step,
                                    fingerprint=step_fp,
                                    hit_count=hit_count,
                                )
                                stag_fn = getattr(self.brain, "stagnation", None)
                                if callable(stag_fn):
                                    reflection = stag_fn(
                                        task.goal,
                                        plan.actions,
                                        results,
                                        fingerprint=step_fp,
                                        hit_count=hit_count,
                                        history=history,
                                    )
                                else:
                                    reflection = run_stagnation(
                                        task.goal,
                                        plan.actions,
                                        results,
                                        fingerprint=step_fp,
                                        hit_count=hit_count,
                                        history=history,
                                        llm=None,
                                    )
                            stagnation_count += 1
                            self._run_stagnation_count = stagnation_count
                            last_reflection = reflection.to_dict()
                            last_reflection["trigger"] = "step_repeat"
                            if not force_hard:
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
                                **self._stats_progress_extra(),
                            )
                            if reflection.decision == "abort":
                                abort_msg = reflection.cause or "step repeat abort"
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
                            # continue / alternate：注入约束后进入下一轮规划
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
                        self.supervisor.fail(task_id, message=plan.thought or "brain failed")
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
                    # 问询/状态类：空 actions success 也须过答复覆盖闸
                    if (
                        plan.status == "success"
                        and not plan.actions
                        and goal_needs_answer_coverage_gate(task.goal)
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
                            thought, goal=task.goal
                        )
                        if reason is not None:
                            # 过薄且仍有未读 dump：优先逼读（若上一轮已 forced 仍未读则计入拒因）
                            if unread and reason in {
                                "inventory_thin",
                                "meta_narration",
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
                                            goal=task.goal,
                                            thought=thought,
                                            reason=reason,
                                            unread_dumps=rel_unread or None,
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
                        elif goal_needs_answer_coverage_gate(task.goal):
                            content = format_answer_coverage_prompt(
                                goal=task.goal,
                                unread_dumps=unread_compressed_dumps(
                                    compressed_dump_paths, read_ok_paths
                                )
                                or None,
                            )
                        else:
                            content = (
                                "请根据以上执行结果，给出面向用户的最终自然语言回答："
                                "直接回答用户目标（例如高不高、是否成功、关键数字含义）；"
                                "若含多字段配置/硬件，用短结论 + 分项列表或表写全证据已有字段；"
                                "不要复述或粘贴原始 stdout/top 全文；不要写出明文口令。"
                                "本步必须 status=success、done=true、actions 为空数组；"
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
        }
        if last_reflection:
            out["reflection"] = last_reflection
        if error:
            out["error"] = error
        return out

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
        proposed = normalize_task_title(plan.title or "")
        if not proposed:
            # 规划未给 title 时：若仍无标题，用 goal 启发式兜底一次
            task = self.supervisor.get_task(task_id)
            if task is None:
                return
            meta = dict(task.meta or {})
            if meta.get("title"):
                return
            proposed = suggest_task_title(task.goal)
            source = "heuristic"
        else:
            source = "llm"
            task = self.supervisor.get_task(task_id)
            if task is None:
                return
            meta = dict(task.meta or {})
            existing = normalize_task_title(str(meta.get("title") or ""))
            existing_source = str(meta.get("title_source") or "")
            # 已有正式 LLM 标题则不覆盖；启发式可被首轮 LLM 覆盖
            if existing and existing_source == "llm":
                return
            if existing == proposed and existing_source == source:
                return

        self.supervisor.merge_task_meta(
            task_id,
            {"title": proposed, "title_source": source},
        )
        self._progress(
            f"任务命名：{proposed}",
            phase="title",
            step=step,
            title=proposed,
            title_source=source,
        )

    def _observe(
        self, goal: str, *, methods: list[str] | None = None
    ) -> Observation:
        chosen = methods or choose_observe_methods(goal, capture=self.capture)
        store = getattr(self.controller, "system_log", None)

        def _screen_observe() -> Observation:
            from fawai_avatar.observe.screen import observe as screen_observe

            try:
                obs = screen_observe()
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

        return collect_observation(
            chosen,
            screen_observe=_screen_observe,
            goal=goal,
            capture=self.capture,
        )

    def _verify_file_state(
        self, actions: list[Action], results: list[ExecResult]
    ) -> list[ExecResult]:
        bg = getattr(self.controller, "_background", None)
        if bg is None or not hasattr(bg, "fs"):
            return []
        from fawai_avatar.observe.verify import verify_actions_file_state

        return verify_actions_file_state(bg.fs.work_root, actions, results)

    def _execute_actions(self, task_id: str, actions: list[Action]) -> list[ExecResult]:
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
            if action.needs_foreground:
                if not self.supervisor.holding_foreground(task_id):
                    if not self.supervisor.claim_foreground(task_id):
                        results.append(
                            ExecResult(ok=False, message="cannot claim foreground_lease")
                        )
                        break
            if action.write_resources:
                items = [
                    ResourceItem(kind="file", key=path, mode="write")
                    for path in action.write_resources
                ]
                if not self.supervisor.allows_writes(task_id, list(action.write_resources)):
                    if not self.supervisor.claim_resources(task_id, items):
                        results.append(
                            ExecResult(ok=False, message="cannot claim resource_lease")
                        )
                        break
            # confirm 模式 / 会话 Diff 需要 task_id
            if "task_id" not in (action.args or {}):
                action = Action(
                    kind=action.kind,
                    args={**dict(action.args), "task_id": task_id},
                    needs_foreground=action.needs_foreground,
                    write_resources=list(action.write_resources),
                )
            bg = getattr(self.controller, "_background", None)
            if bg is not None:
                bg.task_id = task_id
            result = self.controller.execute(action, task_id=task_id)
            results.append(result)
            if not result.ok:
                break
        return results


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
    if len(actions) == 1:
        return _action_progress_meta(actions[0])
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


def _summarize_action(action: Action, *, max_len: int = 320) -> str:
    """会话时间线用的动作摘要：优先展示 shell 命令 / 文件路径等关键参数。"""
    kind = (action.kind or "?").strip() or "?"
    args = action.args or {}
    cmd = args.get("cmd") or args.get("command")
    if cmd is None and isinstance(args.get("argv"), list) and args["argv"]:
        cmd = " ".join(str(x) for x in args["argv"])
    path = args.get("path")
    text = args.get("text") or args.get("keys")
    seconds = args.get("seconds")

    detail = ""
    if cmd is not None and str(cmd).strip():
        detail = _clip_progress_text(cmd, max_len=max_len)
    elif path is not None and str(path).strip():
        detail = f"path={_clip_progress_text(path, max_len=max_len)}"
    elif isinstance(text, list) and text:
        detail = _clip_progress_text("+".join(str(x) for x in text), max_len=max_len)
    elif text is not None and str(text).strip():
        detail = f"text={_clip_progress_text(text, max_len=max_len)!r}"
    elif seconds is not None:
        detail = f"{seconds}s"
    if detail:
        return f"{kind}: {detail}"
    return kind


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
    for tr in traces:
        out.append(
            {
                "observation": tr.observation.to_dict() if tr.observation else None,
                "plan": tr.plan.to_dict() if tr.plan else None,
                "results": [r.to_dict() for r in tr.results],
            }
        )
    return out
