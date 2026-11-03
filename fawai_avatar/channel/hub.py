# term:会话通道  doc:doc/功能/会话通道/说明.md
# term:进程中断续跑  doc:doc/功能/进程中断续跑/说明.md
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fawai_avatar.channel.message import Message
from fawai_avatar.channel.system_log import SystemLogEntry, SystemLogStore
from fawai_avatar.task.supervisor import Supervisor

BroadcastFn = Callable[[Message], Awaitable[None] | None]
WorkerFn = Callable[[str], dict[str, Any]]

logger = logging.getLogger(__name__)

_CONTROL_RE = re.compile(
    r"^(?:/)?"
    r"(暂停|pause|恢复|resume|继续|取消|cancel|删除|delete|归档|archive|"
    r"取消归档|unarchive|复盘|review|任务列表|tasks|"
    r"确认补丁|确认diff|confirm_diff|confirm-patch|"
    r"拒绝补丁|拒绝diff|reject_diff|reject-patch)"
    r"\s*([a-f0-9]{8,})?$",
    re.IGNORECASE,
)

_HISTORY_SOFT_MAX = 2000
_HISTORY_KEEP = 1600
_HISTORY_USER_PIN_MAX = 120
# 单任务预算紧张时：每轮 run 至少留头尾 progress，避免追问后只剩最后一段时间线
_HISTORY_PROGRESS_PER_RUN_MIN = 24
# 单任务 progress 硬上限，防止一个胖任务吃光配额后其它任务只剩用户气泡
_HISTORY_PROGRESS_PER_TASK_MAX = 240
_HISTORY_FILENAME = "session_history.jsonl"


def _trim_history(messages: list[Message]) -> list[Message]:
    """裁剪历史：优先保留用户消息（含附图）及其所属任务的多轮时间线，再补最近其它出站。

    同一任务多次追问会产生多段 progress（不同 run_id）。旧逻辑按时间从头塞满 keep
    配额，预算不够时容易留下早期碎片、丢掉最近几轮，或相反只剩末轮。现按任务
    优先整段保留；若仍超预算，则：先钉非 progress，再按 run 从新到旧各留头尾。
    """
    if len(messages) <= _HISTORY_SOFT_MAX:
        return messages
    pinned = [m for m in messages if m.role == "user"]
    if len(pinned) > _HISTORY_USER_PIN_MAX:
        pinned = pinned[-_HISTORY_USER_PIN_MAX:]
    keep_ids: set[str] = {m.message_id for m in pinned if m.message_id}
    task_ids: list[str] = []
    seen_tasks: set[str] = set()
    for m in reversed(pinned):
        tid = (m.task_id or "").strip()
        if tid and tid not in seen_tasks:
            seen_tasks.add(tid)
            task_ids.append(tid)

    def _add_task_messages(tid: str, budget: int) -> None:
        """在预算内尽量保留该任务完整多轮时间线。"""
        nonlocal keep_ids
        if budget <= 0:
            return
        task_msgs = [m for m in messages if (m.task_id or "").strip() == tid]
        if not task_msgs:
            return
        # 先钉用户/卡片/终态文本等非 progress（段与段之间的锚点）
        anchors = [
            m
            for m in task_msgs
            if m.kind != "progress" and m.message_id and m.message_id not in keep_ids
        ]
        for m in anchors:
            if len(keep_ids) >= _HISTORY_KEEP:
                return
            keep_ids.add(m.message_id)
            budget -= 1
            if budget <= 0:
                return
        progress = [
            m
            for m in task_msgs
            if m.kind == "progress" and m.message_id and m.message_id not in keep_ids
        ]
        if not progress:
            return
        # 单任务硬上限：再胖也不得超过，把名额留给其它置顶任务
        task_cap = min(budget, _HISTORY_PROGRESS_PER_TASK_MAX, _HISTORY_KEEP - len(keep_ids))
        if task_cap <= 0:
            return
        if len(progress) <= task_cap:
            for m in progress:
                keep_ids.add(m.message_id)
            return
        # 按 run_id 分段（无 run_id 的归入空键，常为 route 开段）
        runs: dict[str, list[Message]] = {}
        run_order: list[str] = []
        for m in progress:
            rid = str((m.meta or {}).get("run_id") or "") or (
                f"seg:{m.message_id}" if (m.meta or {}).get("timeline_segment") == "new" else ""
            )
            if rid not in runs:
                runs[rid] = []
                run_order.append(rid)
            runs[rid].append(m)
        # 新 → 旧：每轮至少留头尾，避免「只有追问后时间线」
        per_run = max(_HISTORY_PROGRESS_PER_RUN_MIN, task_cap // max(len(run_order), 1))
        taken = 0
        for rid in reversed(run_order):
            if taken >= task_cap or len(keep_ids) >= _HISTORY_KEEP:
                break
            chunk = runs[rid]
            take = min(len(chunk), per_run, task_cap - taken, _HISTORY_KEEP - len(keep_ids))
            if take <= 0:
                break
            if take >= len(chunk):
                chosen = chunk
            else:
                head = max(1, take // 3)
                tail = take - head
                chosen = chunk[:head] + chunk[-tail:] if tail else chunk[:take]
            for m in chosen:
                if m.message_id and m.message_id not in keep_ids:
                    keep_ids.add(m.message_id)
                    taken += 1
                    if taken >= task_cap or len(keep_ids) >= _HISTORY_KEEP:
                        break

    # 近 → 远：均分剩余配额，禁止第一个胖任务吃光导致旧任务只剩末轮
    remaining_slots = max(0, _HISTORY_KEEP - len(keep_ids))
    n_tasks = max(len(task_ids), 1)
    base = max(32, remaining_slots // n_tasks)
    for i, tid in enumerate(task_ids):
        if len(keep_ids) >= _HISTORY_KEEP:
            break
        left_tasks = n_tasks - i
        left_slots = max(0, _HISTORY_KEEP - len(keep_ids))
        budget = left_slots if left_tasks <= 1 else min(left_slots, max(base, left_slots // left_tasks))
        _add_task_messages(tid, budget)
    if len(keep_ids) < _HISTORY_KEEP:
        for m in reversed(messages):
            mid = m.message_id
            if mid and mid not in keep_ids:
                keep_ids.add(mid)
                if len(keep_ids) >= _HISTORY_KEEP:
                    break
    return [m for m in messages if m.message_id in keep_ids]


class SessionHub:
    """会话枢纽：入站 → 总管；出站广播到已注册通道。"""

    def __init__(
        self,
        supervisor: Supervisor,
        *,
        worker: WorkerFn | None = None,
        auto_run_worker: bool = True,
        history_path: str | Path | None = None,
    ) -> None:
        self.supervisor = supervisor
        self._worker = worker
        self._auto_run_worker = auto_run_worker
        self._broadcasters: list[BroadcastFn] = []
        self._lock = threading.Lock()
        self._active_task_id: str | None = None
        self._worker_threads: dict[str, threading.Thread] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        if history_path is None:
            self._history_path = Path(supervisor.data_dir) / _HISTORY_FILENAME
        else:
            self._history_path = Path(history_path)
        self._history: list[Message] = self._load_history()
        self.system_log = SystemLogStore(str(supervisor.data_dir))

    def log_system(
        self,
        action: str,
        *,
        category: str = "task",
        message: str = "",
        ok: bool = True,
        task_id: str | None = None,
        actor: str = "api",
        detail: dict[str, Any] | None = None,
    ) -> SystemLogEntry | None:
        """控制/系统操作写入系统日志；不进入会话历史。受订阅闸门约束。"""
        return self.system_log.append(
            action,
            category=category,
            message=message,
            ok=ok,
            task_id=task_id,
            actor=actor,
            detail=detail,
        )

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定网关主事件循环，供工作线程安全出站。"""
        self._loop = loop

    def add_broadcaster(self, fn: BroadcastFn) -> None:
        self._broadcasters.append(fn)

    def publish_threadsafe(self, message: Message, *, timeout: float = 60.0) -> None:
        """从工作线程把出站消息投递到主循环。"""
        loop = self._loop
        if loop is not None and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.publish(message), loop)
            fut.result(timeout=timeout)
            return
        asyncio.run(self.publish(message))

    @property
    def history(self) -> list[Message]:
        with self._lock:
            return list(self._history)

    @property
    def history_path(self) -> Path:
        return self._history_path

    def history_for_task(self, task_id: str) -> list[Message]:
        """返回某任务在会话历史中的全部信封（含执行时间线 progress）。

        若会话软裁剪已丢掉 progress，但任务库仍有 meta.steps / final_thought，
        则按步骤回放时间线，避免打开旧任务只剩用户问题、看不见处理过程。
        """
        tid = (task_id or "").strip()
        if not tid:
            return []
        with self._lock:
            live = [m for m in self._history if (m.task_id or "") == tid]
        try:
            task = self.supervisor.get_task(tid)
        except Exception:  # noqa: BLE001
            task = None
        if task is None:
            return live
        from fawai_avatar.channel.task_timeline import (
            merge_live_and_rebuilt,
            rebuild_timeline_from_task,
            timeline_needs_rebuild,
        )

        if not timeline_needs_rebuild(live, task):
            return live
        rebuilt = rebuild_timeline_from_task(task)
        return merge_live_and_rebuilt(live, rebuilt)

    def remember(self, message: Message) -> None:
        with self._lock:
            self._history.append(message)
            if len(self._history) > _HISTORY_SOFT_MAX:
                self._history = _trim_history(self._history)
            self._persist_history_locked()
        # 个人长记忆旁路：用户入站文本（不堵会话）
        if message.role == "user" and message.kind in {"text", "image"} and (
            message.text or ""
        ).strip():
            try:
                from fawai_avatar.lore_sidecar import observe_lore_event

                observe_lore_event(
                    text=message.text.strip(),
                    event_kind="user_text",
                    task_id=str(message.task_id or ""),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("lore sidecar schedule failed: %s", exc)

    def bind_inbound_to_task(self, inbound: Message, task_id: str) -> None:
        """把已落盘的用户入站消息改挂到实际任务（新建/承接后）。"""
        if not inbound or not task_id:
            return
        inbound.task_id = task_id
        with self._lock:
            for msg in reversed(self._history):
                if msg.message_id == inbound.message_id:
                    msg.task_id = task_id
                    self._persist_history_locked()
                    return

    def _load_history(self) -> list[Message]:
        path = self._history_path
        if not path.is_file():
            return []
        loaded: list[Message] = []
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    loaded.append(Message.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    logger.warning("skip bad history line in %s: %s", path, exc)
        except OSError as exc:
            logger.warning("load session history failed (%s): %s", path, exc)
            return []
        if len(loaded) > _HISTORY_SOFT_MAX:
            loaded = _trim_history(loaded)
        return loaded

    def _persist_history_locked(self) -> None:
        path = self._history_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            payload = "\n".join(json.dumps(m.to_dict(), ensure_ascii=False) for m in self._history)
            if payload:
                payload += "\n"
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("persist session history failed (%s): %s", path, exc)
    async def publish(self, message: Message) -> None:
        self.remember(message)
        await self.broadcast(message)

    async def broadcast(self, message: Message) -> None:
        """只推给已注册通道，不再写入历史（入站已 remember 时用）。"""
        for fn in list(self._broadcasters):
            result = fn(message)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                await result  # type: ignore[arg-type]

    async def handle_inbound(self, message: Message) -> Message | None:
        """处理用户入站；返回立即回执（若有）。"""
        self.remember(message)
        if message.kind == "control" or (
            message.kind == "text" and _looks_like_control(message.text)
        ):
            # 复盘勾选闸上的裸「取消」须走第二道闸，勿被总管 cancel 抢走；
            # 「取消 <task前缀>」仍走控制口令。
            if message.kind == "text" and self._prefer_review_apply_over_control(message):
                return await self._dispatch_goal(message.text.strip(), inbound=message)
            return await self._handle_control(message)

        if message.kind == "image":
            # 用户图已落历史；广播带 media_url 的气泡，再进建任务/追问/追加
            if message.media_url or message.media_path:
                await self.broadcast(message)
            goal = message.text.strip() or "请根据附图理解并执行相关操作"
            # 优先绝对路径（大脑读图）；会话 UI 会把 basename 映射为 /media/…
            # 若仅有 URL（无落盘路径）则写入 /media/… 供前端直接展示
            image_ref = (message.media_path or "").strip() or (message.media_url or "").strip()
            if image_ref:
                goal = f"{goal}\n[image:{image_ref}]"
            return await self._dispatch_goal(goal, inbound=message)

        if message.kind == "text":
            text = message.text.strip()
            if not text:
                err = Message.error("空消息")
                await self.publish(err)
                return err
            return await self._dispatch_goal(text, inbound=message)

        err = Message.error(f"暂不支持入站类型: {message.kind}")
        await self.publish(err)
        return err

    async def _dispatch_goal(self, goal: str, *, inbound: Message) -> Message:
        """无 task_id → 新建；有线程则 LLM/规则判断续跑或另开（终态不一律 sibling）。"""
        raw_focus = (inbound.task_id or "").strip() or None
        if not raw_focus:
            return await self._create_and_maybe_run(goal, inbound=inbound)

        resolved = self.supervisor.resolve_task_id(raw_focus)
        if resolved is None:
            err = Message.error(f"未知任务: {raw_focus}")
            await self.publish(err)
            return err

        task = self.supervisor.get_task(resolved)
        if task is None:
            err = Message.error(f"未知任务: {raw_focus}")
            await self.publish(err)
            return err

        if task.status == "archived":
            err = Message.error(
                f"任务 {resolved[:8]}… 已归档。请取消归档后再追问，或新开任务。",
                task_id=resolved,
            )
            await self.publish(err)
            return err

        if task.status == "running":
            return await self._append_while_running(task, goal, inbound=inbound)

        from fawai_avatar.brain.clarification import is_awaiting_clarification
        from fawai_avatar.skills.review import (
            is_review_awaiting_apply,
            is_review_awaiting_approval,
        )

        if is_awaiting_clarification(task):
            return await self._answer_clarification(task, goal, inbound=inbound)
        if is_review_awaiting_approval(task):
            return await self._approve_and_run_review(task, goal, inbound=inbound)
        if is_review_awaiting_apply(task):
            return await self._handle_review_apply_reply(task, goal, inbound=inbound)

        from fawai_avatar.brain.task_route import route_followup

        notes = str((task.meta or {}).get("final_thought") or "")
        decision = route_followup(task, goal, recent_notes=notes)
        action = str(decision.get("action") or "continue")
        reason = str(decision.get("reason") or "")
        source = str(decision.get("source") or "")
        await self.publish(
            Message.progress(
                f"任务路由 → {'续跑' if action == 'continue' else '新建'}（{source}）{('：' + reason) if reason else ''}",
                task_id=resolved,
                meta={
                    "route": action,
                    "reason": reason,
                    "source": source,
                    "phase": "route",
                    "timeline_segment": "new",
                },
            )
        )
        if action == "new_task":
            return await self._create_and_maybe_run(
                goal,
                inbound=inbound,
                related_task_id=resolved,
            )
        return await self._continue_task(task, goal, inbound=inbound)

    async def _append_while_running(
        self, task: Any, followup: str, *, inbound: Message
    ) -> Message:
        """执行中追加：LLM 分析后 apply（pending_appends）或 new_task；不打断当前工具。"""
        from fawai_avatar.brain.task_route import (
            analyze_running_append,
            summarize_recent_steps,
        )

        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        steps_summary = summarize_recent_steps(task)
        decision = analyze_running_append(
            task, followup, recent_steps_summary=steps_summary
        )
        action = str(decision.get("action") or "apply")
        reason = str(decision.get("reason") or "")
        source = str(decision.get("source") or "")
        instruction = str(decision.get("instruction") or "").strip()
        append_progress_text = (
            f"执行中追加 → {'并入' if action == 'apply' else '新建'}（{source}）"
            f"{('：' + reason) if reason else ''}"
        )
        if action == "new_task":
            # 另开：先记路由进度，再建 sibling（不拆当前 run 的时间线）
            await self.publish(
                Message.progress(
                    append_progress_text,
                    task_id=task_id,
                    meta={
                        "route": action,
                        "reason": reason,
                        "source": source,
                        "phase": "append",
                        "instruction": instruction[:120],
                    },
                )
            )
            return await self._create_and_maybe_run(
                followup,
                inbound=inbound,
                related_task_id=task_id,
            )

        # 追问/追加不并入 goal 正文（会话气泡各自独立）；靠 pending_appends 注入大脑 history
        followups = list((task.meta or {}).get("followups") or [])
        followups.append(
            {
                "text": followup[:500],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
                "mode": "append",
            }
        )
        pending = list((task.meta or {}).get("pending_appends") or [])
        pending.append(
            {
                "text": followup[:500],
                "instruction": instruction[:120] or followup[:80],
                "reason": reason[:80],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
            }
        )
        self.supervisor.merge_task_meta(
            task_id,
            {
                "followups": followups[-20:],
                "pending_appends": pending[-20:],
                "last_inbound_id": inbound.message_id,
                "mode": "append",
            },
        )
        self._active_task_id = task_id
        # 先发卡再开新时间线段：会话里顺序为「前段时间线 → 用户气泡 → 追加卡 → 后段时间线」
        card = Message.card(
            f"已追加到任务 {task_id[:8]}…（当前步骤结束后生效）",
            task_id=task_id,
            meta={
                "status": "running",
                "goal": (task.goal or "")[:200],
                "mode": "append",
                "reason": reason,
                "instruction": instruction[:120],
            },
        )
        await self.publish(card)
        await self.publish(
            Message.progress(
                append_progress_text,
                task_id=task_id,
                meta={
                    "route": action,
                    "reason": reason,
                    "source": source,
                    "phase": "append",
                    "instruction": instruction[:120],
                    "timeline_segment": "new",
                },
            )
        )
        return card

    async def _create_and_maybe_run(
        self,
        goal: str,
        *,
        inbound: Message,
        related_task_id: str | None = None,
    ) -> Message:
        from fawai_avatar.task.title import suggest_task_title

        title = suggest_task_title(goal)
        meta: dict[str, Any] = {
            "source": "session_hub",
            "inbound_id": inbound.message_id,
            "title": title,
            "title_source": "heuristic",
        }
        if related_task_id:
            meta["related_task_id"] = related_task_id
            meta["parent_task_id"] = related_task_id
            meta["routed_from"] = "thread_new_task"
        task_id = self.supervisor.create_task(goal, meta=meta)
        self.bind_inbound_to_task(inbound, task_id)
        self._active_task_id = task_id
        card = Message.card(
            f"已创建任务「{title}」"
            + (f"（从 {related_task_id[:8]}… 线程拆出）" if related_task_id else ""),
            task_id=task_id,
            meta={
                "status": "pending",
                "goal": goal[:200],
                "title": title,
                "title_source": "heuristic",
                "mode": "create",
                "related_task_id": related_task_id,
                "parent_task_id": related_task_id,
            },
        )
        await self.publish(card)
        await self._maybe_start_worker(task_id)
        return card

    async def _answer_clarification(
        self, task: Any, followup: str, *, inbound: Message
    ) -> Message:
        """waiting + awaiting_input：本条视为澄清答复，注入后续跑。"""
        from fawai_avatar.brain.clarification import (
            CLARIFICATION_KEY,
            format_answer_for_history,
            match_answer,
        )
        from fawai_avatar.runtime.checkpoint import CHECKPOINT_KEY

        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        clar = dict((task.meta or {}).get(CLARIFICATION_KEY) or {})
        options = list(clar.get("options") or [])
        question = str(clar.get("question") or "")
        resolved, matched = match_answer(followup, options)
        if not resolved:
            err = Message.error("请回复选项编号、标签，或简短说明。", task_id=task_id)
            await self.publish(err)
            return err
        inject = format_answer_for_history(
            resolved, matched=matched, question=question
        )
        # 澄清答复只注入 history，不改写 goal 正文（避免与首条用户话合并展示）
        followups = list((task.meta or {}).get("followups") or [])
        followups.append(
            {
                "text": followup[:500],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
                "mode": "clarification",
                "matched_id": (matched or {}).get("id"),
            }
        )
        meta = dict(task.meta or {})
        clar["phase"] = "answered"
        clar["answer"] = resolved[:500]
        clar["answered_at"] = inbound.ts
        meta[CLARIFICATION_KEY] = clar
        meta["followups"] = followups[-20:]
        meta["last_inbound_id"] = inbound.message_id
        meta["mode"] = "clarification"
        # 检查点 history 注入，续跑大脑能看到答复
        ck = meta.get(CHECKPOINT_KEY)
        if isinstance(ck, dict):
            history = list(ck.get("history") or [])
            history.append({"role": "user", "content": inject})
            ck = dict(ck)
            ck["history"] = history
            meta[CHECKPOINT_KEY] = ck
        self.supervisor.replace_task_meta(task_id, meta)
        if not self.supervisor.requeue_task(task_id, reopen=False):
            err = Message.error(
                f"无法续跑澄清任务 {task_id[:8]}…（状态={task.status}）",
                task_id=task_id,
            )
            await self.publish(err)
            return err
        self._active_task_id = task_id
        label = resolved
        if matched:
            label = f"[{matched.get('id')}] {matched.get('label') or resolved}"
        card = Message.card(
            f"已收到选择：{label}，继续执行…",
            task_id=task_id,
            meta={
                "status": "pending",
                "phase": "clarification_answered",
                "mode": "clarification",
                "answer": resolved[:200],
                "timeline_segment": "new",
            },
        )
        await self.publish(card)
        await self._maybe_start_worker(task_id)
        return card

    async def _continue_task(self, task: Any, followup: str, *, inbound: Message) -> Message:
        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        # 追问单独成会话气泡；goal 保持首条目标，续跑时由 Worker 从 meta.followups 注入 history
        followups = list((task.meta or {}).get("followups") or [])
        followups.append(
            {"text": followup[:500], "inbound_id": inbound.message_id, "ts": inbound.ts}
        )
        # 若历史 goal 已被旧逻辑拼过【追问】，剥回首条，避免列表/合成气泡继续合并展示
        cleaned_goal = _original_goal(task.goal)
        if cleaned_goal != (task.goal or "").strip():
            self.supervisor.update_task_goal(task_id, cleaned_goal)
        self.supervisor.merge_task_meta(
            task_id,
            {
                "followups": followups[-20:],
                "last_inbound_id": inbound.message_id,
                "mode": "continue",
            },
        )
        # 终态续跑须 reopen（archived 已在 dispatch 拒绝）
        reopen = task.status in {"completed", "failed", "cancelled"}
        if not self.supervisor.requeue_task(task_id, reopen=reopen):
            err = Message.error(f"无法续跑任务 {task_id[:8]}…（状态={task.status}）", task_id=task_id)
            await self.publish(err)
            return err
        self._active_task_id = task_id
        card = Message.card(
            f"已追问任务 {task_id[:8]}…",
            task_id=task_id,
            meta={
                "status": "pending",
                "goal": cleaned_goal[:200],
                "mode": "continue",
                "reopen": reopen,
            },
        )
        await self.publish(card)
        await self._maybe_start_worker(task_id)
        return card

    def has_live_worker(self, task_id: str) -> bool:
        with self._lock:
            t = self._worker_threads.get(task_id)
            return t is not None and t.is_alive()

    async def _maybe_start_worker(self, task_id: str) -> bool:
        """拉起 Worker；已有存活线程则跳过（防双开）。返回是否新启动。"""
        if not (self._auto_run_worker and self._worker is not None):
            await self.publish(
                Message.text_assistant(
                    f"任务已入队：{task_id[:8]}…（未自动执行工作任务）",
                    task_id=task_id,
                )
            )
            return False
        with self._lock:
            existing = self._worker_threads.get(task_id)
            if existing is not None and existing.is_alive():
                return False
            thread = threading.Thread(
                target=self._run_worker_safe,
                args=(task_id,),
                name=f"fawai-worker-{task_id[:8]}",
                daemon=True,
            )
            self._worker_threads[task_id] = thread
            thread.start()
            return True

    def recover_interrupted_tasks(self) -> list[str]:
        """网关启动：为孤儿 running/waiting/blocked/pending 补拉 Worker（同步）。"""
        if not (self._auto_run_worker and self._worker is not None):
            return []
        started: list[str] = []
        for task_id in self.supervisor.list_recoverable_task_ids():
            with self._lock:
                existing = self._worker_threads.get(task_id)
                if existing is not None and existing.is_alive():
                    continue
                thread = threading.Thread(
                    target=self._run_worker_safe,
                    args=(task_id,),
                    name=f"fawai-worker-{task_id[:8]}",
                    daemon=True,
                )
                self._worker_threads[task_id] = thread
                thread.start()
            started.append(task_id)
            self.log_system(
                "recover",
                message=f"进程恢复续跑 {task_id[:8]}…",
                ok=True,
                task_id=task_id,
                actor="runtime",
            )
        if started:
            logger.info("recovered %d interrupted task(s)", len(started))
        return started

    def _run_worker_safe(self, task_id: str) -> None:
        assert self._worker is not None
        result: dict[str, Any] | None = None
        try:
            result = self._worker(task_id)
        except Exception as exc:  # noqa: BLE001 — 崩溃须收口为 failed，禁止只抛文案后卡住 running
            logger.exception("worker crashed task_id=%s", task_id)
            err_msg = _format_worker_crash(exc)
            fail_recorded = False
            try:
                fail_recorded = bool(self.supervisor.fail(task_id, message=err_msg))
            except Exception:  # noqa: BLE001 — 库坏时仍要出站告知用户
                logger.exception(
                    "supervisor.fail after worker crash also failed task_id=%s",
                    task_id,
                )
            try:
                self.log_system(
                    "worker_crash",
                    category="runtime",
                    message=err_msg[:300],
                    ok=False,
                    task_id=task_id,
                    actor="runtime",
                    detail={"fail_recorded": fail_recorded, "exc_type": type(exc).__name__},
                )
            except Exception:  # noqa: BLE001
                pass
            self.publish_threadsafe(
                Message.error(f"工作任务异常: {err_msg}", task_id=task_id)
            )
            # 合成失败结果，走下方统一终态卡 / 助手摘要（勿直接 return）
            result = {
                "ok": False,
                "error": err_msg,
                "worker_crash": True,
                "fail_recorded": fail_recorded,
            }
        finally:
            with self._lock:
                cur = self._worker_threads.get(task_id)
                if cur is threading.current_thread():
                    self._worker_threads.pop(task_id, None)

        if result is None:
            return
        ok = bool(result.get("ok"))
        task = None
        try:
            task = self.supervisor.get_task(task_id)
        except Exception:  # noqa: BLE001 — 读库失败仍按结果出站
            logger.exception("get_task after worker finish failed task_id=%s", task_id)
        status = task.status if task else ("completed" if ok else "failed")
        # 崩溃且 fail 写库失败时，任务可能仍显示 running；出站按 failed 收口
        if result.get("worker_crash") and status == "running":
            status = "failed"
        # 执行中澄清：停在 waiting，发卡问句，勿当终态「完成」
        if result.get("awaiting_input") or status == "waiting":
            clar = result.get("clarification") or (task.meta or {}).get("clarification") or {}
            from fawai_avatar.brain.clarification import format_clarification_text

            question = str(
                (clar or {}).get("question")
                or result.get("final_thought")
                or "需要你确认后才能继续。"
            )
            options = list((clar or {}).get("options") or [])
            card_text = format_clarification_text(question, options)
            self.publish_threadsafe(
                Message.card(
                    card_text,
                    task_id=task_id,
                    meta={
                        "ok": True,
                        "status": "waiting",
                        "phase": "clarification",
                        "question": question,
                        "options": options,
                        "awaiting_input": True,
                    },
                )
            )
            self.publish_threadsafe(
                Message.text_assistant(
                    "请回复编号或选项标签后继续。",
                    task_id=task_id,
                )
            )
            return
        image_path = _first_image_from_result(result)
        card_meta: dict[str, Any] = {
            "ok": ok,
            "status": status,
            "result": _clip_result(result),
        }
        try:
            from fawai_avatar.task import stats as task_stats

            raw_stats = (task.meta or {}).get(task_stats.STATS_KEY) if task else None
            if isinstance(raw_stats, dict):
                card_meta["stats"] = task_stats.summary_for_api(raw_stats)
        except Exception:  # noqa: BLE001 — 统计失败不影响终态卡
            pass
        # 执行时间线已由 WorkerLoop.on_progress 流式推送；此处不再批量重放 steps。
        self.publish_threadsafe(
            Message.card(
                f"任务 {task_id[:8]}… → {status}",
                task_id=task_id,
                meta=card_meta,
            )
        )
        if image_path:
            self.publish_threadsafe(
                Message.image_out(
                    media_path=image_path,
                    text=_observe_image_caption(result),
                    task_id=task_id,
                )
            )
        if ok:
            summary = _success_summary(result) or "完成"
        elif status == "cancelled" or str(result.get("error") or "") == "cancelled":
            summary = "任务已取消"
        else:
            summary = str(result.get("error") or _last_fail_message(result) or "未完成")
        self.publish_threadsafe(Message.text_assistant(str(summary), task_id=task_id))

    async def _handle_control(self, message: Message) -> Message:
        control = (message.control or "").lower().strip()
        task_id = message.task_id
        text = message.text.strip()
        if not control:
            m = _CONTROL_RE.match(text)
            if m:
                verb = m.group(1).lower()
                if verb in {"暂停", "pause"}:
                    control = "pause"
                elif verb in {"恢复", "resume", "继续"}:
                    control = "resume"
                elif verb in {"取消", "cancel"}:
                    control = "cancel"
                elif verb in {"删除", "delete"}:
                    control = "delete"
                elif verb in {"归档", "archive"}:
                    control = "archive"
                elif verb in {"取消归档", "unarchive"}:
                    control = "unarchive"
                elif verb in {"复盘", "review"}:
                    control = "review"
                elif verb in {"任务列表", "tasks"}:
                    control = "tasks"
                elif verb.lower() in {
                    "确认补丁",
                    "确认diff",
                    "confirm_diff",
                    "confirm-patch",
                }:
                    control = "confirm_diff"
                elif verb.lower() in {
                    "拒绝补丁",
                    "拒绝diff",
                    "reject_diff",
                    "reject-patch",
                }:
                    control = "reject_diff"
                else:
                    control = "cancel"
                if m.group(2):
                    task_id = m.group(2)

        if control == "tasks":
            return await self._handle_list_tasks()

        if not task_id:
            task_id = self._active_task_id
        if not task_id:
            err = Message.error("缺少 task_id，无法执行控制口令")
            await self.publish(err)
            return err

        # 允许短前缀匹配（SQLite 任务表）
        resolved = self.supervisor.resolve_task_id(task_id)
        if resolved is None:
            err = Message.error(f"未知任务: {task_id}")
            await self.publish(err)
            return err
        task_id = resolved

        if control == "delete":
            return await self._handle_delete(task_id)
        if control == "review":
            return await self._handle_review(task_id)
        if control == "archive":
            return await self._handle_archive(task_id)
        if control == "unarchive":
            return await self._handle_unarchive(task_id)
        if control in {"confirm_diff", "reject_diff"}:
            return await self._handle_diff_control(control, task_id)

        ok = False
        if control == "pause":
            ok = self.supervisor.pause(task_id)
        elif control == "resume":
            ok = self.supervisor.resume(task_id)
        elif control == "cancel":
            ok = self.supervisor.cancel(task_id)
        else:
            err = Message.error(f"未知控制口令: {control}")
            await self.publish(err)
            return err

        task = self.supervisor.get_task(task_id)
        status = task.status if task else "unknown"
        # 进程死后 resume：无存活 Worker 时补拉，按检查点继续（同进程暂停中不双开）
        resumed_worker = False
        if ok and control == "resume":
            resumed_worker = await self._maybe_start_worker(task_id)
        msg = f"{control} → {status}" + ("" if ok else "（未生效）")
        if resumed_worker:
            msg = f"{msg}（已续跑）"
        self.log_system(
            control,
            message=msg,
            ok=ok,
            task_id=task_id,
            actor="control",
            detail={"status": status, "worker_started": resumed_worker},
        )
        out = Message.card(
            msg,
            task_id=task_id,
            meta={
                "ok": ok,
                "control": control,
                "status": status,
                "worker_started": resumed_worker,
            },
        )
        if not ok:
            err = Message.error(msg, task_id=task_id)
            await self.publish(err)
            return err
        # WS 入站丢弃返回值，必须 publish；否则侧栏徽章/会话卡收不到终态
        await self.publish(out)
        return out

    def _prefer_review_apply_over_control(self, message: Message) -> bool:
        """awaiting_apply 下裸「取消」优先复盘第二道闸，而非总管 cancel。"""
        text = (message.text or "").strip()
        matched = _CONTROL_RE.match(text)
        if matched is None or matched.group(2):
            return False
        verb = matched.group(1).lower()
        if verb not in {"取消", "cancel"}:
            return False
        raw_tid = (message.task_id or "").strip() or self._active_task_id
        if not raw_tid:
            return False
        resolved = self.supervisor.resolve_task_id(raw_tid)
        if resolved is None:
            return False
        task = self.supervisor.get_task(resolved)
        if task is None:
            return False
        from fawai_avatar.skills.review import is_review_awaiting_apply

        return is_review_awaiting_apply(task)

    async def _handle_list_tasks(self) -> Message:
        tasks = self.supervisor.list_tasks(limit=20)
        if not tasks:
            out = Message.text_assistant("当前没有任务。")
            await self.publish(out)
            return out
        lines = ["任务列表："]
        for t in tasks:
            lines.append(f"- {t.task_id[:8]}… [{t.status}] {t.goal[:60]}")
        out = Message.text_assistant("\n".join(lines))
        await self.publish(out)
        return out

    async def _handle_archive(self, task_id: str) -> Message:
        ok, message = self.supervisor.archive_task(task_id)
        if not ok:
            self.log_system(
                "archive",
                message=message,
                ok=False,
                task_id=task_id,
                actor="control",
            )
            err = Message.error(message, task_id=task_id)
            await self.publish(err)
            return err
        self.log_system(
            "archive",
            message=f"archive → archived",
            ok=True,
            task_id=task_id,
            actor="control",
            detail={"status": "archived"},
        )
        return Message.card(
            f"archive → archived",
            task_id=task_id,
            meta={"ok": True, "control": "archive", "status": "archived"},
        )

    async def _handle_unarchive(self, task_id: str) -> Message:
        ok, message = self.supervisor.unarchive_task(task_id)
        task = self.supervisor.get_task(task_id)
        status = task.status if task else "unknown"
        if not ok:
            self.log_system(
                "unarchive",
                message=message,
                ok=False,
                task_id=task_id,
                actor="control",
                detail={"status": status},
            )
            err = Message.error(message, task_id=task_id)
            await self.publish(err)
            return err
        self.log_system(
            "unarchive",
            message=f"unarchive → {status}",
            ok=True,
            task_id=task_id,
            actor="control",
            detail={"status": status},
        )
        return Message.card(
            f"unarchive → {status}",
            task_id=task_id,
            meta={"ok": True, "control": "unarchive", "status": status},
        )

    async def _handle_delete(self, task_id: str) -> Message:
        ok, message = self.supervisor.delete_task(task_id)
        if not ok:
            self.log_system(
                "delete",
                message=message,
                ok=False,
                task_id=task_id,
                actor="control",
            )
            err = Message.error(message, task_id=task_id)
            await self.publish(err)
            return err
        self.log_system(
            "delete",
            message=f"delete → deleted",
            ok=True,
            task_id=task_id,
            actor="control",
            detail={"status": "deleted"},
        )
        return Message.card(
            f"delete → deleted",
            task_id=task_id,
            meta={"ok": True, "control": "delete", "status": "deleted"},
        )

    async def _handle_review(self, task_id: str) -> Message:
        return await self.start_review([task_id], distill=True, ratchet=False)

    async def start_startup_repair(self, *, force: bool = False) -> Message:
        """一键修复启动错误：读后台崩溃日志，自动建整改任务并开跑。"""
        from fawai_avatar.channel.startup_repair import (
            build_startup_repair_goal,
            repair_intro,
            repair_title,
        )
        from fawai_avatar.runtime.console_log import read_console_log, read_last_crash

        data_dir = str(self.supervisor.data_dir)
        snap = read_console_log(data_dir, max_lines=120, max_chars=40000)
        crash = read_last_crash(data_dir) or ""
        if not crash.strip():
            crash = (snap.get("text") or "")[-8000:]
        degraded = {
            "system_log_degraded": not getattr(self.system_log, "available", True),
            "system_log_error": getattr(self.system_log, "init_error", None),
            "degraded": not getattr(self.system_log, "available", True),
            "init_error": getattr(self.system_log, "init_error", None),
        }
        if (
            not force
            and not (crash or "").strip()
            and getattr(self.system_log, "available", True)
        ):
            err = Message.error(
                "未发现启动崩溃摘录，且系统日志库正常；无需一键修复。"
                "若刚崩溃过，请稍候再试或先「刷新日志」。"
            )
            await self.publish(err)
            return err

        goal = build_startup_repair_goal(
            crash_text=crash,
            console_tail=str(snap.get("text") or ""),
            degraded=degraded,
        )
        title = repair_title(degraded)
        meta = {
            "title": title,
            "source": "startup_repair",
            "control": "startup_repair",
            "degraded": degraded,
        }
        task_id = self.supervisor.create_task(goal, meta=meta)
        self._active_task_id = task_id
        await self.publish(Message.text_user(goal, task_id=task_id))
        card = Message.card(
            f"已创建启动错误修复「{title}」",
            task_id=task_id,
            meta={
                "ok": True,
                "control": "startup_repair",
                "status": "pending",
                "mode": "create",
                "goal": goal[:200],
                "title": title,
                "title_source": "startup_repair",
            },
        )
        await self.publish(card)
        await self.publish(
            Message.text_assistant(repair_intro(task_id=task_id), task_id=task_id)
        )
        await self._maybe_start_worker(task_id)
        return card

    async def start_log_failure_analysis(
        self,
        log_ids: list[str] | None = None,
        *,
        limit: int = 8,
        force: bool = False,
    ) -> Message:
        """系统日志失败一键分析：复用任务对话，自动写入目标/要求/边界并开跑。

        默认跳过已标记分析的日志；若指定 id 均已分析且原整改任务仍在，则复开该任务。
        force=True 时清除标记并重新开跑。
        """
        from fawai_avatar.channel.log_analyze import (
            already_analyzed_entries,
            analysis_intro,
            analysis_title,
            build_failure_analysis_goal,
            meta_for_analysis,
            resolve_failure_entries,
        )

        if not getattr(self.system_log, "available", True):
            err = Message.error(
                "系统日志库降级不可用，无法做失败日志一键分析。"
                "请到左侧「服务与版本」查看后台日志，或使用「一键修复启动错误」。"
            )
            await self.publish(err)
            return err

        ids = [str(x).strip() for x in (log_ids or []) if str(x).strip()] or None

        if force and ids:
            self.system_log.clear_analyzed(ids)

        try:
            entries = resolve_failure_entries(
                self.system_log,
                log_ids=ids,
                limit=limit,
                skip_analyzed=not force,
            )
        except Exception as exc:  # noqa: BLE001
            err = Message.error(f"读取失败日志失败：{exc}")
            await self.publish(err)
            return err

        if not entries and ids and not force:
            prior = already_analyzed_entries(self.system_log, ids)
            if prior:
                reopen = await self._reopen_existing_log_analysis(prior)
                if reopen is not None:
                    return reopen
                # 原任务已删：清标记后重取
                self.system_log.clear_analyzed([e.log_id for e in prior])
                entries = resolve_failure_entries(
                    self.system_log, log_ids=ids, limit=limit, skip_analyzed=False
                )

        if not entries:
            err = Message.error(
                "没有可分析的失败系统日志（已全部分析过、或未筛到失败；"
                "可对单条点「查看」或传 force=true 强制重分析）"
            )
            await self.publish(err)
            return err

        try:
            goal = build_failure_analysis_goal(entries)
        except ValueError as exc:
            err = Message.error(str(exc))
            await self.publish(err)
            return err

        title = analysis_title(entries)
        meta = {
            "title": title,
            **meta_for_analysis(entries),
        }
        task_id = self.supervisor.create_task(goal, meta=meta)
        self.system_log.mark_analyzed([e.log_id for e in entries], task_id)
        self._active_task_id = task_id
        await self.publish(
            Message.text_user(goal, task_id=task_id)
        )
        card = Message.card(
            f"已创建失败日志整改「{title}」",
            task_id=task_id,
            meta={
                "ok": True,
                "control": "system_log_analyze",
                "status": "pending",
                "mode": "create",
                "already": False,
                "goal": goal[:200],
                "title": title,
                "title_source": "system_log_analyze",
                "log_ids": meta.get("log_ids") or [],
                "source_task_ids": meta.get("source_task_ids") or [],
            },
        )
        await self.publish(card)
        await self.publish(
            Message.text_assistant(analysis_intro(entries), task_id=task_id)
        )
        await self._maybe_start_worker(task_id)
        return card

    async def _reopen_existing_log_analysis(
        self, prior: list[Any]
    ) -> Message | None:
        """已分析日志：若整改任务仍在则复开，不新建。"""
        from fawai_avatar.channel.system_log import SystemLogEntry

        entries = [e for e in prior if isinstance(e, SystemLogEntry)]
        if not entries:
            return None
        # 优先用最近一次分析任务
        ordered = sorted(
            entries,
            key=lambda e: float(e.analyze_ts or 0),
            reverse=True,
        )
        for entry in ordered:
            tid = (entry.analyze_task_id or "").strip()
            if not tid:
                continue
            task = self.supervisor.get_task(tid)
            if task is None:
                continue
            self._active_task_id = tid
            card = Message.card(
                f"这些失败日志已分析过，打开原整改任务 {tid[:8]}…",
                task_id=tid,
                meta={
                    "ok": True,
                    "control": "system_log_analyze",
                    "status": task.status,
                    "mode": "reopen",
                    "already": True,
                    "goal": (task.goal or "")[:200],
                    "title": (task.meta or {}).get("title") or "",
                    "title_source": "system_log_analyze",
                    "log_ids": [e.log_id for e in entries],
                    "analyze_task_id": tid,
                },
            )
            await self.publish(card)
            await self.publish(
                Message.text_assistant(
                    "所选失败日志已有整改任务，已避免重复分析。可在本对话继续追问；"
                    "若要强制重跑请使用「强制重分析」。",
                    task_id=tid,
                )
            )
            return card
        return None

    async def start_review(
        self,
        source_task_ids: list[str],
        *,
        distill: bool = True,
        ratchet: bool = False,
    ) -> Message:
        """创建复盘任务：写出指令，waiting 等用户放行/附加建议。"""
        from fawai_avatar.skills.review import propose_review_session

        resolved: list[str] = []
        for raw in source_task_ids:
            tid = self.supervisor.resolve_task_id(str(raw))
            if tid is None:
                err = Message.error(f"未知任务: {raw}")
                await self.publish(err)
                return err
            if tid not in resolved:
                resolved.append(tid)

        result = propose_review_session(
            self.supervisor,
            resolved,
            distill=distill,
            ratchet=ratchet,
        )
        if not result.get("ok"):
            err = Message.error(str(result.get("message") or "无法创建复盘任务"))
            await self.publish(err)
            return err

        review_tid = str(result["task_id"])
        self._active_task_id = review_tid
        # 指令正文与「放行分析」合并为一张卡：气泡内先文后钮，避免操作卡跑到说明前面。
        # 对应 doc/功能/任务复盘/说明.md（awaiting_approval 单卡）
        instruction = str(result.get("message") or "").strip() or (
            f"已创建复盘任务 {review_tid[:8]}…（等待放行）"
        )
        card = Message.card(
            instruction,
            task_id=review_tid,
            meta={
                "ok": True,
                "control": "review",
                "phase": "awaiting_approval",
                "status": "waiting",
                "joint": bool(result.get("joint")),
                "source_task_ids": result.get("source_task_ids") or resolved,
                "goal": (self.supervisor.get_task(review_tid).goal if self.supervisor.get_task(review_tid) else "")[:200],
                "actions": [{"id": "approve", "label": "放行分析"}],
            },
        )
        await self.publish(card)
        return card

    async def _approve_and_run_review(
        self, task: Any, followup: str, *, inbound: Message
    ) -> Message:
        from fawai_avatar.skills.review import parse_review_approval

        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        parsed = parse_review_approval(followup)
        if not parsed.get("ok"):
            err = Message.error(str(parsed.get("message") or "请先放行"), task_id=task_id)
            await self.publish(err)
            return err

        guidance = str(parsed.get("guidance") or "").strip()
        followups = list((task.meta or {}).get("followups") or [])
        followups.append(
            {"text": followup[:500], "inbound_id": inbound.message_id, "ts": inbound.ts}
        )
        self.supervisor.merge_task_meta(
            task_id,
            {
                "review_phase": "analyzing",
                "user_guidance": guidance,
                "followups": followups[-20:],
                "last_inbound_id": inbound.message_id,
            },
        )
        self._active_task_id = task_id
        tip = (
            "已放行，开始全维分析（不落盘）…"
            if not guidance
            else "已收到附加建议，开始全维分析（不落盘）…"
        )
        card = Message.card(
            tip,
            task_id=task_id,
            meta={
                "ok": True,
                "control": "review_approve",
                "phase": "analyzing",
                "status": "running",
                "has_guidance": bool(guidance),
            },
        )
        await self.publish(card)
        await self.publish(Message.progress(tip, task_id=task_id))
        threading.Thread(
            target=self._run_review_analyze_safe,
            args=(task_id,),
            name=f"fawai-review-analyze-{task_id[:8]}",
            daemon=True,
        ).start()
        return card

    async def _handle_review_apply_reply(
        self, task: Any, followup: str, *, inbound: Message
    ) -> Message:
        from fawai_avatar.skills.review import parse_review_apply_reply

        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        meta = task.meta or {}
        items = list(meta.get("review_items") or [])
        parsed = parse_review_apply_reply(followup, items)
        if not parsed.get("ok"):
            err = Message.error(
                str(parsed.get("message") or "请勾选优化项"),
                task_id=task_id,
            )
            await self.publish(err)
            return err

        followups = list(meta.get("followups") or [])
        followups.append(
            {"text": followup[:500], "inbound_id": inbound.message_id, "ts": inbound.ts}
        )
        self.supervisor.merge_task_meta(
            task_id,
            {"followups": followups[-20:], "last_inbound_id": inbound.message_id},
        )
        self._active_task_id = task_id

        mode = str(parsed.get("mode") or "")
        if mode == "cancel":
            tip = "已取消优化，复盘结束（未备份、未改动）"
            self.supervisor.merge_task_meta(
                task_id,
                {
                    "review_phase": "completed",
                    "ok": True,
                    "final_thought": tip,
                    "last_review": {
                        "cancelled": True,
                        "selected_ids": [],
                        "source_task_ids": list(meta.get("source_task_ids") or []),
                        "joint": bool(meta.get("joint")),
                    },
                },
            )
            self.supervisor.complete(task_id)
            card = Message.card(
                tip,
                task_id=task_id,
                meta={"ok": True, "control": "review_cancel", "phase": "completed"},
            )
            await self.publish(card)
            await self.publish(Message.text_assistant(tip, task_id=task_id))
            return card

        if mode == "revise":
            guidance = str(parsed.get("revise_guidance") or "").strip()
            self.supervisor.merge_task_meta(
                task_id,
                {
                    "review_phase": "analyzing",
                    "user_guidance": guidance,
                },
            )
            tip = "已收到改写建议，重新分析清单…"
            card = Message.card(
                tip,
                task_id=task_id,
                meta={"ok": True, "control": "review_revise", "phase": "analyzing"},
            )
            await self.publish(card)
            await self.publish(Message.progress(tip, task_id=task_id))
            threading.Thread(
                target=self._run_review_analyze_safe,
                args=(task_id,),
                name=f"fawai-review-revise-{task_id[:8]}",
                daemon=True,
            ).start()
            return card

        selected = list(parsed.get("selected_ids") or [])
        self.supervisor.merge_task_meta(
            task_id,
            {
                "review_phase": "applying",
                "selected_review_ids": selected,
            },
        )
        tip = (
            f"开始备份并执行选中项 #{','.join(selected)}…"
            if selected
            else "未选中项，将结束且不改动…"
        )
        card = Message.card(
            tip,
            task_id=task_id,
            meta={
                "ok": True,
                "control": "review_apply",
                "phase": "applying",
                "status": "running",
                "selected_ids": selected,
            },
        )
        await self.publish(card)
        await self.publish(Message.progress(tip, task_id=task_id))
        threading.Thread(
            target=self._run_review_apply_safe,
            args=(task_id, selected),
            name=f"fawai-review-apply-{task_id[:8]}",
            daemon=True,
        ).start()
        return card

    def _ensure_review_running(self, review_task_id: str) -> bool:
        task = self.supervisor.get_task(review_task_id)
        if task is None:
            return False
        if self.supervisor.set_running(review_task_id):
            return True
        self.supervisor.requeue_task(review_task_id, reopen=False)
        return self.supervisor.set_running(review_task_id)

    def _run_review_analyze_safe(self, review_task_id: str) -> None:
        from fawai_avatar.skills.review import analyze_review_tasks

        task = self.supervisor.get_task(review_task_id)
        if task is None:
            self.publish_threadsafe(Message.error("复盘任务不存在", task_id=review_task_id))
            return
        meta = task.meta or {}
        source_ids = list(meta.get("source_task_ids") or [])
        if not source_ids:
            self.supervisor.fail(review_task_id, message="缺少 source_task_ids")
            self.publish_threadsafe(
                Message.error("复盘任务缺少源任务列表", task_id=review_task_id)
            )
            return

        if not self._ensure_review_running(review_task_id):
            cur = self.supervisor.get_task(review_task_id)
            self.publish_threadsafe(
                Message.error(
                    f"无法开始复盘分析（状态={cur.status if cur else '?'}）",
                    task_id=review_task_id,
                )
            )
            return

        guidance = str(meta.get("user_guidance") or "").strip() or None
        distill = bool(meta.get("distill", True))
        ratchet = bool(meta.get("ratchet", False))
        try:
            result = analyze_review_tasks(
                self.supervisor,
                source_ids,
                distill=distill,
                ratchet=ratchet,
                user_guidance=guidance,
            )
        except Exception as exc:  # noqa: BLE001
            self.supervisor.fail(review_task_id, message=str(exc))
            self.supervisor.merge_task_meta(
                review_task_id,
                {"review_phase": "failed", "ok": False, "error": str(exc)},
            )
            self.publish_threadsafe(
                Message.error(f"复盘分析异常: {exc}", task_id=review_task_id)
            )
            return

        text = str(result.get("message") or "复盘分析结束")
        ok = bool(result.get("ok"))
        self.publish_threadsafe(
            Message.progress("全维分析完成，等待勾选优化项…", task_id=review_task_id)
        )
        self.publish_threadsafe(Message.text_assistant(text, task_id=review_task_id))

        if not ok:
            self.supervisor.fail(review_task_id, message=text[:200])
            self.supervisor.merge_task_meta(
                review_task_id,
                {
                    "review_phase": "failed",
                    "ok": False,
                    "final_thought": text[:1500],
                },
            )
            self.publish_threadsafe(
                Message.card(
                    "复盘分析失败",
                    task_id=review_task_id,
                    meta={
                        "ok": False,
                        "control": "review",
                        "phase": "failed",
                        "status": "failed",
                    },
                )
            )
            return

        self.supervisor.merge_task_meta(
            review_task_id,
            {
                "review_phase": "awaiting_apply",
                "ok": True,
                "final_thought": text[:1500],
                "review_items": result.get("items") or [],
                "review_dimensions": result.get("dimensions") or {},
                "review_outcome": result.get("outcome"),
                "review_analysis": str(result.get("analysis") or "")[:4000],
                "last_review": {
                    "detail_fetched": bool(result.get("detail_fetched")),
                    "outcome": result.get("outcome"),
                    "actions": result.get("decisions"),
                    "item_count": len(result.get("items") or []),
                    "phase": "awaiting_apply",
                    "source_task_ids": source_ids,
                    "joint": bool(result.get("joint")),
                },
            },
        )
        self.supervisor.set_waiting(review_task_id)
        detail = "steps" if result.get("detail_fetched") else "meta"
        from fawai_avatar.skills.review import items_for_ui

        ui_items = items_for_ui(result.get("items") or [])
        self.publish_threadsafe(
            Message.card(
                f"复盘分析完成（{detail}）→ 等待勾选优化",
                task_id=review_task_id,
                meta={
                    "ok": True,
                    "control": "review",
                    "phase": "awaiting_apply",
                    "status": "waiting",
                    "detail_fetched": bool(result.get("detail_fetched")),
                    "item_count": len(ui_items),
                    "items": ui_items,
                    "source_task_ids": source_ids,
                    "joint": bool(result.get("joint")),
                },
            )
        )

    def _run_review_apply_safe(
        self, review_task_id: str, selected_ids: list[str]
    ) -> None:
        from fawai_avatar.skills.review import apply_review_plan

        task = self.supervisor.get_task(review_task_id)
        if task is None:
            self.publish_threadsafe(Message.error("复盘任务不存在", task_id=review_task_id))
            return
        meta = task.meta or {}
        source_ids = list(meta.get("source_task_ids") or [])
        items = list(meta.get("review_items") or [])

        if not self._ensure_review_running(review_task_id):
            cur = self.supervisor.get_task(review_task_id)
            self.publish_threadsafe(
                Message.error(
                    f"无法执行优化（状态={cur.status if cur else '?'}）",
                    task_id=review_task_id,
                )
            )
            return

        try:
            result = apply_review_plan(
                self.supervisor,
                task_ids=source_ids,
                items=items,
                selected_ids=list(selected_ids or []),
                dimensions=meta.get("review_dimensions"),
                analysis=str(meta.get("review_analysis") or ""),
                outcome=str(meta.get("review_outcome") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            self.supervisor.fail(review_task_id, message=str(exc))
            self.supervisor.merge_task_meta(
                review_task_id,
                {"review_phase": "failed", "ok": False, "error": str(exc)},
            )
            self.publish_threadsafe(
                Message.error(f"复盘执行异常: {exc}", task_id=review_task_id)
            )
            return

        text = str(result.get("message") or "复盘执行结束")
        ok = bool(result.get("ok"))
        self.publish_threadsafe(Message.progress("优化执行完成，整理结果…", task_id=review_task_id))
        self.publish_threadsafe(Message.text_assistant(text, task_id=review_task_id))

        if ok:
            self.supervisor.complete(review_task_id)
            phase = "completed"
        else:
            self.supervisor.fail(review_task_id, message=text[:200])
            phase = "failed"

        backup = result.get("backup") or {}
        self.supervisor.merge_task_meta(
            review_task_id,
            {
                "review_phase": phase,
                "ok": ok,
                "final_thought": text[:1500],
                "last_review": {
                    "outcome": result.get("outcome"),
                    "actions": result.get("decisions"),
                    "selected_ids": result.get("selected_ids"),
                    "skill_id": (result.get("skill") or {}).get("id"),
                    "backup": backup,
                    "source_task_ids": source_ids,
                    "joint": bool(meta.get("joint")),
                },
            },
        )
        self.publish_threadsafe(
            Message.card(
                f"复盘执行 → {'ok' if ok else 'fail'}"
                + (f"；存档 {backup.get('release_id')}" if backup.get("release_id") else ""),
                task_id=review_task_id,
                meta={
                    "ok": ok,
                    "control": "review",
                    "phase": phase,
                    "status": phase,
                    "skill_id": (result.get("skill") or {}).get("id"),
                    "release_id": backup.get("release_id"),
                    "selected_ids": result.get("selected_ids"),
                    "source_task_ids": source_ids,
                    "joint": bool(meta.get("joint")),
                },
            )
        )

    def emit_progress(self, text: str, *, task_id: str | None = None, meta: dict[str, Any] | None = None) -> None:
        self.publish_threadsafe(Message.progress(text, task_id=task_id, meta=meta))

    async def _handle_diff_control(self, control: str, task_id: str) -> Message:
        from fawai_avatar.control.background import BackgroundTools
        from fawai_avatar.types import Action

        tools = BackgroundTools(task_id=task_id)
        kind = "diff_confirm" if control == "confirm_diff" else "diff_reject"
        result = tools.execute(Action(kind=kind, args={"task_id": task_id}))
        self.log_system(
            control,
            message=result.message,
            ok=result.ok,
            task_id=task_id,
            actor="control",
            detail=result.data,
        )
        if not result.ok:
            err = Message.error(result.message, task_id=task_id)
            await self.publish(err)
            return err
        out = Message.card(
            result.message,
            task_id=task_id,
            meta={"ok": True, "control": control, **(result.data or {})},
        )
        await self.publish(out)
        if result.data and result.data.get("diff"):
            await self.publish(
                Message.progress(
                    f"Diff\n{str(result.data.get('diff'))[:2500]}",
                    task_id=task_id,
                    meta={"phase": "diff", "control": control},
                )
            )
        return out


def _looks_like_control(text: str) -> bool:
    return bool(_CONTROL_RE.match(text.strip()))


_FOLLOWUP_GOAL_SPLIT_RE = re.compile(r"\n\s*【追问】\s*")


def _original_goal(goal: str) -> str:
    """取 goal 首段（去掉旧版拼进去的【追问】后缀）。"""
    raw = (goal or "").strip()
    if not raw:
        return ""
    return _FOLLOWUP_GOAL_SPLIT_RE.split(raw, maxsplit=1)[0].strip()


def _merge_followup_goal(previous: str, followup: str) -> str:
    """兼容旧调用/测试：拼【追问】；新路径不再把追问写入 goal。"""
    prev = (previous or "").strip()
    nxt = (followup or "").strip()
    if not prev:
        return nxt
    if not nxt:
        return prev
    if nxt in prev:
        return prev
    return f"{prev}\n\n【追问】{nxt}"


def _format_worker_crash(exc: BaseException) -> str:
    """Worker 未捕获异常 → 给用户看的短说明（可行动，避免裸英文堆栈）。"""
    raw = str(exc).strip() or type(exc).__name__
    low = raw.lower()
    if "unable to open database file" in low:
        return (
            "任务库无法打开（unable to open database file）。"
            "常见原因：数据目录不存在、相对路径因工作目录变化失效、或磁盘权限问题；"
            "请确认 FAWAI_DATA_DIR 可写后重启网关，再重开任务。"
        )
    return raw[:500]


def _clip_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "error": result.get("error"),
        "task_id": result.get("task_id"),
        "step_count": len(result.get("steps") or []),
        "worker_crash": result.get("worker_crash"),
    }


def _first_image_from_result(result: dict[str, Any]) -> str | None:
    for step in result.get("steps") or []:
        obs = (step or {}).get("observation") or {}
        path = obs.get("image_path")
        if path and Path(path).is_file():
            return str(path)
    return None


def _observe_image_caption(result: dict[str, Any]) -> str:
    """观察媒体气泡标题：按实际观察方式，禁止写死「观察截图」。"""
    from fawai_avatar.observe.methods import image_caption_for_observation
    from fawai_avatar.types import Observation

    for step in result.get("steps") or []:
        raw = (step or {}).get("observation") or {}
        if not isinstance(raw, dict):
            continue
        if not (raw.get("image_path") or raw.get("summary")):
            continue
        obs = Observation(
            summary=str(raw.get("summary") or ""),
            image_path=raw.get("image_path"),
            width=raw.get("width"),
            height=raw.get("height"),
            meta=dict(raw.get("meta") or {}),
        )
        if obs.image_path:
            return image_caption_for_observation(obs)
    return "观察（截屏）"


def _last_fail_message(result: dict[str, Any]) -> str | None:
    for step in reversed(result.get("steps") or []):
        for r in reversed((step or {}).get("results") or []):
            if r and not r.get("ok") and r.get("message"):
                return str(r["message"])
    return None


def _clip_user_text(text: str, *, limit: int = 1200) -> str:
    from fawai_avatar.channel.redact import redact_secrets

    text = redact_secrets(text.strip())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _final_success_thought(result: dict[str, Any]) -> str | None:
    """收尾轮面向用户的结论：优先 success/done 的 plan.thought。"""
    for step in reversed(result.get("steps") or []):
        plan = (step or {}).get("plan") or {}
        thought = str(plan.get("thought") or "").strip()
        if not thought:
            continue
        status = str(plan.get("status") or "").strip().lower()
        if status == "success" or (plan.get("done") and status != "failed"):
            return thought
    return None


def _is_raw_tool_dump(message: str, data: dict[str, Any]) -> bool:
    """判定是否为不宜直接贴给用户的原始工具输出（如 top/stdout）。"""
    msg = (message or "").strip()
    if not msg:
        return True
    stdout = str(data.get("stdout") or "").strip()
    if stdout and msg == stdout:
        return True
    if len(msg) > 400 and "\n" in msg:
        return True
    # macOS top 头常见字段
    markers = ("Load Avg:", "PhysMem:", "Processes:", "CPU usage:")
    if sum(1 for m in markers if m in msg) >= 2:
        return True
    return False


def _success_summary(result: dict[str, Any]) -> str | None:
    """面向用户的最终回复：优先收尾 thought，禁止无条件转发 shell stdout。"""
    thought = _final_success_thought(result)
    if thought:
        return _clip_user_text(thought)

    for step in reversed(result.get("steps") or []):
        for r in reversed((step or {}).get("results") or []):
            if not r or not r.get("ok"):
                continue
            msg = str(r.get("message") or "").strip()
            if not msg or msg in {"done"}:
                continue
            data = r.get("data") or {}
            if _is_raw_tool_dump(msg, data if isinstance(data, dict) else {}):
                continue
            return _clip_user_text(msg)
    return None
