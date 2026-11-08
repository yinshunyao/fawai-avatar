# term:会话通道  doc:doc/功能/会话通道/说明.md
# term:进程中断续跑  doc:doc/功能/进程中断续跑/说明.md
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
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
        # 建卡后异步编排/出图；须挂住引用防 GC
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        # 入站幂等：同 message_id 处理中 / 已落盘则跳过，防刷新重发与 WS 重投
        self._inbound_inflight: set[str] = set()
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

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """把同步 LLM/编排脑调用丢到线程池，避免堵死 uvicorn 事件循环（整站无响应）。"""
        return await asyncio.to_thread(fn, *args, **kwargs)

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

    def broadcast_threadsafe(self, message: Message, *, timeout: float = 60.0) -> None:
        """只广播、不落历史（LLM 直播增量用）。

        直播路径**不** ``fut.result()``：否则每个 delta 都堵在 WS 发送上，
        HTTP 流式读循环被拖成「攒一坨再吐」，前端看不到逐段涨字。
        """
        del timeout  # 直播火递；保留形参与 publish_threadsafe 对齐
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)
            return
        asyncio.run(self.broadcast(message))

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

        计划父任务还会并入监管脑子任务历史（旧数据可能仍挂在子 task_id 上），
        读出时统一改写为父 task_id，便于单会话展示。
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

        # 父计划：并入子任务信封（去重后按时间排）
        try:
            from fawai_avatar.brain.plan_scheduler import (
                is_orchestrator_task,
                list_plan_child_ids,
            )

            if is_orchestrator_task(task.meta):
                child_ids = list_plan_child_ids(self.supervisor, tid)
                if child_ids:
                    seen = {
                        (m.message_id or "", m.ts, m.kind, (m.text or "")[:80])
                        for m in live
                    }
                    with self._lock:
                        for m in self._history:
                            if (m.task_id or "") not in child_ids:
                                continue
                            key = (
                                m.message_id or "",
                                m.ts,
                                m.kind,
                                (m.text or "")[:80],
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            # 浅拷贝改挂父会话，不改动历史原件
                            cloned = Message.from_dict(
                                {**m.to_dict(), "task_id": tid}
                            )
                            meta = dict(cloned.meta or {})
                            meta.setdefault("plan_child_task_id", m.task_id)
                            meta.setdefault("mirrored_from_plan_child", True)
                            # 子任务终态不得冒充父计划 status（侧栏徽章以整图为准）
                            child_st = str(meta.get("status") or "")
                            if child_st in {
                                "completed",
                                "failed",
                                "cancelled",
                                "archived",
                            } and str(meta.get("mode") or "") != "plan_finish":
                                meta.setdefault("node_status", child_st)
                                meta["status"] = "running"
                                meta.setdefault("mode", "plan_node_finish")
                            cloned.meta = meta
                            live.append(cloned)
                    live.sort(key=lambda x: float(x.ts or 0))
        except Exception:  # noqa: BLE001
            logger.exception("merge plan child history failed task_id=%s", tid)

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

    def _is_near_duplicate_user(self, message: Message, *, window_s: float = 3.0) -> bool:
        """同任务、同正文的用户文本在短窗内重入 → 视为误触/刷新重发。"""
        if message.role != "user" or message.kind != "text":
            return False
        text = (message.text or "").strip()
        if not text:
            return False
        task_key = (message.task_id or "").strip()
        now = float(message.ts or time.time())
        with self._lock:
            for m in reversed(self._history):
                if m.role != "user" or m.kind != "text":
                    continue
                if now - float(m.ts or 0.0) > window_s:
                    break
                if (m.message_id or "") == (message.message_id or ""):
                    return True
                prev_task = (m.task_id or "").strip()
                # 两侧都有 task_id 且不同 → 不是同线程误触；任一侧空（compose）仍可比同文
                if task_key and prev_task and task_key != prev_task:
                    break
                if (m.text or "").strip() == text:
                    return True
                break
        return False

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
        """处理用户入站；返回立即回执（若有）。

        用户文本/图：先落盘并广播气泡（PWA 立刻有回执），再跑路由/LLM，避免
        「页面无响应 → 刷新再发」造成双气泡。同 ``message_id`` 或短窗同文重入跳过。
        """
        mid = (message.message_id or "").strip()
        existing: Message | None = None
        with self._lock:
            if mid and mid in self._inbound_inflight:
                logger.info("skip in-flight inbound message_id=%s", mid[:16])
                return None
            if mid:
                for m in self._history:
                    if m.message_id == mid:
                        existing = m
                        break
            if existing is not None:
                logger.info("skip duplicate inbound message_id=%s", mid[:16])
            elif mid:
                self._inbound_inflight.add(mid)
        if existing is not None:
            # 同 id 重投：再推一次已落盘气泡作回执，不二次路由
            if existing.role == "user" and existing.kind in {"text", "image"}:
                await self.broadcast(existing)
            return None
        try:
            if self._is_near_duplicate_user(message):
                logger.info(
                    "skip near-duplicate user text task=%s text=%r",
                    (message.task_id or "")[:8],
                    (message.text or "")[:40],
                )
                # 不落盘、不二次路由；仍回推同 message_id，让 PWA 解除「发送中」
                if message.kind == "text":
                    await self.broadcast(message)
                return None

            self.remember(message)
            # 文本/图立刻回推：与乐观气泡同 message_id 时可幂等合并；路由 LLM 前 UI 已有响应
            if message.role == "user" and message.kind == "text":
                await self.broadcast(message)
            elif message.role == "user" and message.kind == "image":
                if message.media_url or message.media_path:
                    await self.broadcast(message)

            if message.kind == "control" or (
                message.kind == "text" and _looks_like_control(message.text)
            ):
                # 复盘勾选闸上的裸「取消」须走第二道闸，勿被总管 cancel 抢走；
                # 「取消 <task前缀>」仍走控制口令。
                if message.kind == "text" and self._prefer_review_apply_over_control(message):
                    return await self._dispatch_goal(message.text.strip(), inbound=message)
                return await self._handle_control(message)

            if message.kind == "image":
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
        finally:
            if mid:
                with self._lock:
                    self._inbound_inflight.discard(mid)

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

        from fawai_avatar.brain.clarification import is_awaiting_clarification
        from fawai_avatar.brain.plan_scheduler import (
            is_awaiting_plan,
            is_orchestrator_task,
            list_plan_child_ids,
        )

        # 建卡后编排脑仍在判定/出图：勿追问抢跑或另开 sibling
        if is_awaiting_plan(task.meta):
            note = Message.progress(
                "任务仍在编排（判定/出图），请稍候…",
                task_id=resolved,
                meta={
                    "phase": "orchestrate",
                    "inbound_id": inbound.message_id,
                },
            )
            await self.publish(note)
            return note
        from fawai_avatar.skills.review import (
            is_review_awaiting_apply,
            is_review_awaiting_approval,
        )

        # 父计划仍 running、监管脑子任务停在澄清等待：本条视为澄清答复，勿走执行中追加。
        if task.status == "running" and is_orchestrator_task(task.meta):
            for cid in list_plan_child_ids(self.supervisor, resolved):
                child = self.supervisor.get_task(cid)
                if is_awaiting_clarification(child):
                    return await self._answer_clarification(
                        child, goal, inbound=inbound
                    )

        if task.status == "running":
            return await self._append_while_running(task, goal, inbound=inbound)

        if is_awaiting_clarification(task):
            return await self._answer_clarification(task, goal, inbound=inbound)
        if is_review_awaiting_approval(task):
            return await self._approve_and_run_review(task, goal, inbound=inbound)
        if is_review_awaiting_apply(task):
            return await self._handle_review_apply_reply(task, goal, inbound=inbound)

        from fawai_avatar.brain.task_route import route_followup

        # LLM 路由前先推受理进度，避免长静默导致用户刷新重发
        await self.publish(
            Message.progress(
                "已收到，正在处理…",
                task_id=resolved,
                meta={
                    "phase": "accepted",
                    "inbound_id": inbound.message_id,
                    "timeline_segment": "new",
                },
            )
        )
        notes = str((task.meta or {}).get("final_thought") or "")
        decision = await self._run_blocking(
            route_followup, task, goal, recent_notes=notes
        )
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
        """执行中追加：LLM 分析后 apply（pending_appends）或 new_task；不打断当前工具。

        计划模式：编排脑先分析图/租约影响，再下发监管脑。
        """
        from fawai_avatar.brain.plan_scheduler import (
            is_orchestrator_task,
            is_supervisor_task,
        )
        from fawai_avatar.brain.task_route import (
            analyze_running_append,
            summarize_recent_steps,
        )

        task_id = task.task_id
        self.bind_inbound_to_task(inbound, task_id)
        base_ts = float(inbound.ts or time.time())

        if is_orchestrator_task(task.meta) or is_supervisor_task(task.meta):
            return await self._append_plan_mode(
                task, followup, inbound=inbound, base_ts=base_ts
            )

        await self.publish(
            Message.progress(
                "已收到，正在分析追加…",
                task_id=task_id,
                meta={
                    "phase": "accepted",
                    "inbound_id": inbound.message_id,
                    "timeline_segment": "new",
                },
            )
        )
        steps_summary = summarize_recent_steps(task)
        decision = await self._run_blocking(
            analyze_running_append,
            task,
            followup,
            recent_steps_summary=steps_summary,
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
            route_prog = Message.progress(
                append_progress_text,
                task_id=task_id,
                meta={
                    "route": action,
                    "reason": reason,
                    "source": source,
                    "phase": "append",
                    "instruction": instruction[:120],
                    "inbound_id": inbound.message_id,
                },
            )
            route_prog.ts = base_ts + 0.001
            await self.publish(route_prog)
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
                "inbound_id": inbound.message_id,
            },
        )
        card.ts = base_ts + 0.001
        await self.publish(card)
        append_prog = Message.progress(
            append_progress_text,
            task_id=task_id,
            meta={
                "route": action,
                "reason": reason,
                "source": source,
                "phase": "append",
                "instruction": instruction[:120],
                "timeline_segment": "new",
                "inbound_id": inbound.message_id,
            },
        )
        append_prog.ts = base_ts + 0.002
        await self.publish(append_prog)
        return card

    async def _append_plan_mode(
        self,
        task: Any,
        followup: str,
        *,
        inbound: Message,
        base_ts: float,
    ) -> Message:
        """计划模式追加：编排脑分析 → 下发监管脑 / 改图提示 / 新建。"""
        from fawai_avatar.brain.orchestrator import analyze_plan_append
        from fawai_avatar.brain.plan_scheduler import (
            META_PLAN_NODE_ID,
            META_PLAN_PARENT_ID,
            apply_append_to_running_supervisor,
            is_supervisor_task,
            load_graph,
        )

        task_id = task.task_id
        parent_id = task_id
        node_id: str | None = None
        if is_supervisor_task(task.meta):
            parent_id = str((task.meta or {}).get(META_PLAN_PARENT_ID) or task_id)
            node_id = str((task.meta or {}).get(META_PLAN_NODE_ID) or "") or None
        parent = self.supervisor.get_task(parent_id) or task
        graph = load_graph(parent.meta)
        if graph is None:
            # 无图则退回普通追加路径语义：并入本任务
            graph_root = str(parent.goal or "")
            from fawai_avatar.brain.plan_graph import PlanGraph

            graph = PlanGraph(plan_id="missing", root_goal=graph_root, nodes=[])

        decision = await self._run_blocking(
            analyze_plan_append,
            root_goal=str(parent.goal or graph.root_goal),
            graph=graph,
            user_text=followup,
            node_id=node_id,
        )
        kind = str(decision.get("kind") or "no_graph_change")
        reason = str(decision.get("reason") or "")
        source = str(decision.get("source") or "")
        instruction = str(decision.get("instruction") or followup[:80]).strip()
        progress_text = (
            f"计划追加 → {kind}（{source}）{('：' + reason) if reason else ''}"
        )
        route_prog = Message.progress(
            progress_text,
            task_id=parent_id,
            meta={
                "phase": "plan_append",
                "kind": kind,
                "reason": reason,
                "source": source,
                "inbound_id": inbound.message_id,
            },
        )
        route_prog.ts = base_ts + 0.001
        await self.publish(route_prog)

        if kind == "new_task":
            return await self._create_and_maybe_run(
                followup, inbound=inbound, related_task_id=parent_id
            )

        # 记录追加到父计划
        followups = list((parent.meta or {}).get("followups") or [])
        followups.append(
            {
                "text": followup[:500],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
                "mode": "plan_append",
                "kind": kind,
            }
        )
        self.supervisor.merge_task_meta(
            parent_id,
            {
                "followups": followups[-20:],
                "last_inbound_id": inbound.message_id,
                "mode": "plan_append",
                "last_plan_append_kind": kind,
            },
        )

        # reorder / lease_adjust：真改图 + cancel 过时节点 + 按就绪集重拉
        if kind in {"reorder", "lease_adjust"}:
            from fawai_avatar.brain.plan_scheduler import apply_plan_revision

            rev = apply_plan_revision(
                self.supervisor,
                parent_task_id=parent_id,
                kind=kind,
                instruction=instruction or followup[:80],
                inbound_id=inbound.message_id,
            )
            for cid in list(rev.get("spawned") or []):
                await self._maybe_start_worker(cid)
            self._active_task_id = parent_id
            parent2 = self.supervisor.get_task(parent_id) or parent
            card = Message.card(
                (
                    f"编排脑已{kind}（{rev.get('source') or source}）"
                    f"：取消 {len(rev.get('cancelled') or [])} · "
                    f"新拉起 {len(rev.get('spawned') or [])} · "
                    f"v{rev.get('version') or '?'}"
                ),
                task_id=parent_id,
                meta={
                    "status": parent2.status,
                    "mode": "plan_revision",
                    "kind": kind,
                    "reason": str(rev.get("reason") or reason)[:80],
                    "instruction": instruction[:120],
                    "cancelled": list(rev.get("cancelled") or []),
                    "spawned": list(rev.get("spawned") or []),
                    "kept": list(rev.get("kept") or []),
                    "ok": bool(rev.get("ok")),
                    "inbound_id": inbound.message_id,
                },
            )
            card.ts = base_ts + 0.002
            await self.publish(card)
            from fawai_avatar.brain.plan_scheduler import (
                load_graph,
                plan_graph_progress_meta,
            )

            parent_rev = self.supervisor.get_task(parent_id)
            g_rev = load_graph(parent_rev.meta if parent_rev else None)
            rev_meta = plan_graph_progress_meta(g_rev)
            rev_meta.update({"kind": kind, "revision": True})
            sketch = str(rev.get("sketch") or "").strip() or "任务图已修订"
            await self.publish(
                Message.progress(
                    sketch,
                    task_id=parent_id,
                    meta=rev_meta,
                )
            )
            return card

        # no_graph_change / dispatch_note：只下发监管脑，不改图
        targets: list[str] = []
        if is_supervisor_task(task.meta):
            targets = [task_id]
        else:
            for n in graph.nodes:
                if n.status == "running" and n.task_id:
                    targets.append(n.task_id)
        for cid in targets:
            apply_append_to_running_supervisor(
                self.supervisor,
                child_task_id=cid,
                instruction=instruction or followup[:80],
                inbound_id=inbound.message_id,
            )

        self._active_task_id = parent_id
        card = Message.card(
            f"编排脑已处理追加（{kind}）→ 已下发 {len(targets)} 个监管脑",
            task_id=parent_id,
            meta={
                "status": parent.status,
                "mode": "plan_append",
                "kind": kind,
                "reason": reason,
                "instruction": instruction[:120],
                "targets": targets,
                "inbound_id": inbound.message_id,
            },
        )
        card.ts = base_ts + 0.002
        await self.publish(card)
        return card

    def _spawn_bg(self, coro: Any) -> asyncio.Task[Any]:
        """后台编排/出图：挂住 task 防 GC；异常记日志。"""
        task = asyncio.create_task(coro)

        def _done(t: asyncio.Task[Any]) -> None:
            self._bg_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "background create/orchestrate failed: %s", exc, exc_info=exc
                )

        self._bg_tasks.add(task)
        task.add_done_callback(_done)
        return task

    async def _create_and_maybe_run(
        self,
        goal: str,
        *,
        inbound: Message,
        related_task_id: str | None = None,
    ) -> Message:
        """先建卡 + 受理进度，再异步判定/出图，避免发消息后黑屏等 LLM。"""
        from fawai_avatar.brain.plan_scheduler import META_AWAITING_PLAN
        from fawai_avatar.task.title import suggest_task_title

        title = suggest_task_title(goal)
        extra: dict[str, Any] = {
            "inbound_id": inbound.message_id,
            "source": "session_hub",
            "title": title,
            "title_source": "heuristic",
            META_AWAITING_PLAN: True,
        }
        if related_task_id:
            extra["related_task_id"] = related_task_id
            extra["parent_task_id"] = related_task_id
            extra["routed_from"] = "thread_new_task"

        task_id = self.supervisor.create_task(goal, meta=extra)
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
                "awaiting_plan": True,
                "related_task_id": related_task_id,
                "parent_task_id": related_task_id,
            },
        )
        await self.publish(card)
        await self.publish(
            Message.progress(
                "已收到，正在处理…",
                task_id=task_id,
                meta={
                    "phase": "accepted",
                    "inbound_id": inbound.message_id,
                    "timeline_segment": "new",
                },
            )
        )
        # 判定 + 出图放后台：建卡已回；同步 LLM 仍走 _run_blocking 不堵事件循环
        self._spawn_bg(
            self._finish_create_orchestrate(
                task_id,
                goal=goal,
                title=title,
                related_task_id=related_task_id,
                inbound_id=inbound.message_id,
            )
        )
        return card

    async def _finish_create_orchestrate(
        self,
        task_id: str,
        *,
        goal: str,
        title: str,
        related_task_id: str | None,
        inbound_id: str | None = None,
    ) -> None:
        """建卡后：编排脑判定 →（可选）出图调度，或拉普通 Worker。"""
        from fawai_avatar.brain.orchestrator import (
            build_plan_graph,
            decide_complexity,
        )
        from fawai_avatar.brain.plan_scheduler import (
            META_AWAITING_PLAN,
            bootstrap_parent_meta,
            is_awaiting_plan,
            load_graph,
            plan_graph_progress_meta,
            spawn_ready_children,
        )

        def _still_awaiting() -> bool:
            t = self.supervisor.get_task(task_id)
            if t is None:
                return False
            if t.status in {"cancelled", "failed", "completed", "archived"}:
                return False
            return is_awaiting_plan(t.meta)

        if not _still_awaiting():
            return

        from fawai_avatar.runtime.llm_live import LlmLiveProgress

        def _emit_orch(text: str, *, phase: str, step: int = 0, **extra: Any) -> None:
            # 不带 step：避免并进 Worker 的 #0 步骤簇
            del step
            meta = {"phase": phase, "inbound_id": inbound_id, **extra}
            self.emit_progress(text, task_id=task_id, meta=meta)

        orch_live = LlmLiveProgress(_emit_orch, phase="orchestrate", step=-1)

        await self.publish(
            Message.progress(
                "正在判定是否进入计划模式…",
                task_id=task_id,
                meta={
                    "phase": "orchestrate",
                    "inbound_id": inbound_id,
                },
            )
        )

        def _run_decide() -> dict[str, Any]:
            try:
                return decide_complexity(goal, on_llm_delta=orch_live.on_delta)
            finally:
                orch_live.flush()

        # 同步 LLM 必须进线程池，否则进模判定可堵事件循环数十秒
        complexity = await self._run_blocking(_run_decide)
        if not _still_awaiting():
            return

        reason = str(complexity.get("reason") or "")
        source = str(complexity.get("source") or "")
        decision = (
            f"判定进计划模式（{source}）"
            if bool(complexity.get("enter"))
            else "按单任务执行（未进计划模式）"
        )
        if reason:
            decision = f"{decision}：{reason}"
        await self.publish(
            Message.progress(
                decision,
                task_id=task_id,
                meta={
                    "phase": "orchestrate",
                    "live": False,
                    "complexity_reason": reason[:80],
                    "complexity_source": source,
                    "enter_plan": bool(complexity.get("enter")),
                    "inbound_id": inbound_id,
                    **orch_live.preview(),
                },
            )
        )

        if bool(complexity.get("enter")):
            graph_live = LlmLiveProgress(_emit_orch, phase="plan_build", step=-1)
            await self.publish(
                Message.progress(
                    "正在出任务图…",
                    task_id=task_id,
                    meta={
                        "phase": "plan_build",
                        "complexity_reason": reason,
                        "complexity_source": source,
                        "inbound_id": inbound_id,
                    },
                )
            )

            def _run_graph() -> Any:
                try:
                    return build_plan_graph(goal, on_llm_delta=graph_live.on_delta)
                finally:
                    graph_live.flush()

            graph = await self._run_blocking(_run_graph)
            if not _still_awaiting():
                return

            extra: dict[str, Any] = {
                "inbound_id": inbound_id,
                "plan_complexity": {
                    "reason": reason[:80],
                    "source": source,
                },
            }
            if related_task_id:
                extra["related_task_id"] = related_task_id
                extra["parent_task_id"] = related_task_id
                extra["routed_from"] = "thread_new_task"
            meta = bootstrap_parent_meta(graph=graph, title=title, extra=extra)
            # bootstrap 不含 awaiting_plan；整表替换清掉闸门键
            self.supervisor.replace_task_meta(task_id, meta)
            sketch = "\n".join(graph.summary_lines())
            plan_card = Message.card(
                f"计划模式「{title}」· {len(graph.nodes)} 节点",
                task_id=task_id,
                meta={
                    "status": "pending",
                    "goal": goal[:200],
                    "title": title,
                    "title_source": "heuristic",
                    "mode": "plan_create",
                    "plan_mode": True,
                    "plan_id": graph.plan_id,
                    "complexity_reason": reason,
                    "complexity_source": source,
                    "related_task_id": related_task_id,
                    "parent_task_id": related_task_id,
                },
            )
            await self.publish(plan_card)

            child_ids = spawn_ready_children(
                self.supervisor, parent_task_id=task_id, graph=graph
            )
            parent_after = self.supervisor.get_task(task_id)
            g2 = load_graph(parent_after.meta if parent_after else None) or graph
            parent_status = str(
                (parent_after.status if parent_after else "") or "pending"
            )
            graph_meta = plan_graph_progress_meta(g2)
            graph_meta.update(
                {
                    "complexity_source": source,
                    "complexity_reason": reason,
                    "auto_dispatch": True,
                    "status": parent_status,
                    "parent_status": parent_status,
                }
            )
            await self.publish(
                Message.progress(
                    sketch or "任务图已生成",
                    task_id=task_id,
                    meta={
                        "phase": "plan_build",
                        "live": False,
                        "complexity_reason": reason[:80],
                        "complexity_source": source,
                        "inbound_id": inbound_id,
                        **graph_live.preview(),
                    },
                )
            )
            await self.publish(
                Message.progress(
                    "任务图已更新",
                    task_id=task_id,
                    meta=graph_meta,
                )
            )
            for cid in child_ids:
                await self._maybe_start_worker(cid)
            if not child_ids:
                await self.publish(
                    Message.text_assistant(
                        "计划图无就绪节点，请检查依赖或产物契约。",
                        task_id=task_id,
                    )
                )
            return

        # 简单任务：清闸门后拉 Worker
        cur = self.supervisor.get_task(task_id)
        if cur is None or not _still_awaiting():
            return
        cleared = dict(cur.meta or {})
        cleared.pop(META_AWAITING_PLAN, None)
        self.supervisor.replace_task_meta(task_id, cleared)
        await self._maybe_start_worker(task_id)

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
            await self._publish_for_worker_task(err, worker_task_id=task_id)
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
            await self._publish_for_worker_task(err, worker_task_id=task_id)
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
        await self._publish_for_worker_task(card, worker_task_id=task_id)
        await self._maybe_start_worker(task_id)
        return card

    async def _continue_task(self, task: Any, followup: str, *, inbound: Message) -> Message:
        from fawai_avatar.brain.plan_scheduler import is_orchestrator_task

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

        # 计划父任务续跑：失败默认重试失败节点；显式「整图重规划」口令才整图重做
        if is_orchestrator_task(task.meta):
            return await self._continue_plan_orchestrator(
                task,
                followup=followup,
                cleaned_goal=cleaned_goal,
                inbound=inbound,
            )

        # 终态续跑须 reopen（archived 已在 dispatch 拒绝）
        reopen = task.status in {"completed", "failed", "cancelled"}
        if reopen:
            # 检查点会在成功/失败时清掉；重开前快照结论与 steps，供下一跑垫上下文
            snap: dict[str, Any] = {}
            cur_meta = dict(task.meta or {})
            ft = str(cur_meta.get("final_thought") or "").strip()
            if ft and not str(cur_meta.get("prior_final_thought") or "").strip():
                snap["prior_final_thought"] = ft[:4000]
            steps = cur_meta.get("steps")
            if (
                isinstance(steps, list)
                and steps
                and not isinstance(cur_meta.get("prior_steps"), list)
            ):
                # 只留末几步，避免 meta 膨胀
                snap["prior_steps"] = steps[-12:]
            if snap:
                self.supervisor.merge_task_meta(task_id, snap)
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
                "inbound_id": inbound.message_id,
            },
        )
        # 锚定入站之后，保证对话序：用户气泡 → 追问卡 → 后段步骤流
        card.ts = float(inbound.ts or time.time()) + 0.001
        await self.publish(card)
        await self._maybe_start_worker(task_id)
        return card

    async def retry_plan_nodes(
        self,
        parent_task_id: str,
        *,
        node_ids: list[str] | None = None,
        reason: str = "重试失败节点",
        inbound_id: str = "",
    ) -> dict[str, Any]:
        """重试父计划失败节点并拉起新监管脑 Worker。"""
        from fawai_avatar.brain.plan_graph import PlanGraph
        from fawai_avatar.brain.plan_scheduler import (
            plan_graph_progress_meta,
            plan_parent_stats_summary,
            retry_plan_nodes as sched_retry,
        )

        task = self.supervisor.get_task(parent_task_id)
        if task is None:
            return {"ok": False, "message": "unknown task"}
        rev = await self._run_blocking(
            sched_retry,
            self.supervisor,
            parent_task_id=parent_task_id,
            node_ids=node_ids,
            reason=reason,
            inbound_id=inbound_id,
        )
        if not rev.get("ok"):
            return {
                "ok": False,
                "message": str(rev.get("reason") or "retry failed"),
                **{k: rev.get(k) for k in ("retried", "skipped", "spawned", "graph")},
            }

        self._active_task_id = parent_task_id
        g_raw = rev.get("graph")
        g_obj = PlanGraph.from_dict(g_raw) if isinstance(g_raw, dict) else None
        graph_meta = plan_graph_progress_meta(g_obj)
        parent_live = self.supervisor.get_task(parent_task_id)
        parent_status = str(
            rev.get("parent_status")
            or (parent_live.status if parent_live else "")
            or "running"
        )
        graph_meta.update(
            {
                "mode": "retry_nodes",
                "parent_status": parent_status,
                "status": parent_status,
                "retried_nodes": list(rev.get("retried") or []),
                "auto_dispatch": True,
            }
        )
        parent_stats = plan_parent_stats_summary(self.supervisor, parent_task_id)
        if parent_stats is not None:
            graph_meta["parent_stats"] = parent_stats
            graph_meta["stats"] = parent_stats
        sketch = str(rev.get("sketch") or "")
        ids = ", ".join(rev.get("retried") or []) or "?"
        await self.publish(
            Message.progress(
                f"已重试节点 {ids}"
                f"（拉起 {len(rev.get('spawned') or [])}）\n{sketch}",
                task_id=parent_task_id,
                meta=graph_meta,
            )
        )
        for cid in list(rev.get("spawned") or []):
            await self._maybe_start_worker(cid)
        if not rev.get("spawned"):
            await self.publish(
                Message.text_assistant(
                    "已重置失败节点，但暂无就绪可拉起的监管脑（检查依赖或产物契约）。",
                    task_id=parent_task_id,
                )
            )
        return {
            "ok": True,
            "message": f"retried {ids}",
            "parent_status": parent_status,
            **{
                k: rev.get(k)
                for k in ("retried", "skipped", "spawned", "graph", "reason", "kind")
            },
        }

    async def remove_plan_nodes(
        self,
        parent_task_id: str,
        *,
        node_ids: list[str],
        reason: str = "手工删除节点",
        inbound_id: str = "",
    ) -> dict[str, Any]:
        """从父计划活跃图删除节点并刷新会话进度。"""
        from fawai_avatar.brain.plan_graph import PlanGraph
        from fawai_avatar.brain.plan_scheduler import (
            plan_graph_progress_meta,
            plan_parent_stats_summary,
            remove_plan_nodes as sched_remove,
        )

        task = self.supervisor.get_task(parent_task_id)
        if task is None:
            return {"ok": False, "message": "unknown task"}
        rev = await self._run_blocking(
            sched_remove,
            self.supervisor,
            parent_task_id=parent_task_id,
            node_ids=node_ids,
            reason=reason,
            inbound_id=inbound_id,
        )
        if not rev.get("ok"):
            return {
                "ok": False,
                "message": str(rev.get("reason") or "remove failed"),
                **{
                    k: rev.get(k)
                    for k in ("removed", "skipped", "cancelled_children", "spawned", "graph")
                },
            }

        self._active_task_id = parent_task_id
        g_raw = rev.get("graph")
        g_obj = PlanGraph.from_dict(g_raw) if isinstance(g_raw, dict) else None
        graph_meta = plan_graph_progress_meta(g_obj)
        parent_live = self.supervisor.get_task(parent_task_id)
        parent_status = str(
            rev.get("parent_status")
            or (parent_live.status if parent_live else "")
            or "running"
        )
        graph_meta.update(
            {
                "mode": "remove_nodes",
                "parent_status": parent_status,
                "status": parent_status,
                "removed_nodes": list(rev.get("removed") or []),
                "auto_dispatch": bool(rev.get("spawned")),
            }
        )
        parent_stats = plan_parent_stats_summary(self.supervisor, parent_task_id)
        if parent_stats is not None:
            graph_meta["parent_stats"] = parent_stats
            graph_meta["stats"] = parent_stats
        sketch = str(rev.get("sketch") or "")
        ids = ", ".join(rev.get("removed") or []) or "?"
        await self.publish(
            Message.progress(
                f"已删除节点 {ids}\n{sketch}",
                task_id=parent_task_id,
                meta=graph_meta,
            )
        )
        for cid in list(rev.get("spawned") or []):
            await self._maybe_start_worker(cid)
        return {
            "ok": True,
            "message": f"removed {ids}",
            "parent_status": parent_status,
            **{
                k: rev.get(k)
                for k in (
                    "removed",
                    "skipped",
                    "cancelled_children",
                    "spawned",
                    "graph",
                    "reason",
                    "kind",
                )
            },
        }

    async def _continue_plan_orchestrator(
        self,
        task: Any,
        *,
        followup: str,
        cleaned_goal: str,
        inbound: Message,
    ) -> Message:
        """编排器父计划续跑。

        - 显式「整图重规划」口令 → 整图重做；
        - 有 failed 节点 → 重试失败节点（保留已完成），并把追问约束带给新监管脑；
        - 其余追加约束 → 软续跑（analyze_plan_append / 续未决），**禁止**默认整图。
        """
        from fawai_avatar.brain.plan_graph import PlanGraph
        from fawai_avatar.brain.plan_scheduler import (
            load_graph,
            plan_graph_progress_meta,
            replan_full_graph,
            wants_full_replan_followup,
        )

        task_id = task.task_id
        self._active_task_id = task_id
        base_ts = float(inbound.ts or time.time())
        force_full = wants_full_replan_followup(followup)
        graph = load_graph(task.meta)
        failed_ids = (
            [n.id for n in graph.nodes if n.status == "failed"] if graph else []
        )

        # 追问约束写入父计划，供新 spawn 的监管脑 pending_appends 继承
        note = (followup or "").strip()[:500]
        if note:
            self.supervisor.merge_task_meta(
                task_id, {"plan_dispatch_note": note}
            )

        # failed / cancelled 且图上仍有 failed 节点时，默认重试失败节点
        prefer_retry = (
            not force_full
            and str(task.status or "") in {"failed", "cancelled"}
            and bool(failed_ids)
        )
        if prefer_retry:
            card = Message.card(
                f"已追问任务 {task_id[:8]}…（重试失败节点）",
                task_id=task_id,
                meta={
                    "status": "pending",
                    "goal": cleaned_goal[:200],
                    "mode": "retry_nodes",
                    "reopen": True,
                    "inbound_id": inbound.message_id,
                    "plan_mode": True,
                    "parent_status": "running",
                },
            )
            card.ts = base_ts + 0.001
            await self.publish(card)
            result = await self.retry_plan_nodes(
                task_id,
                node_ids=None,
                reason=f"追问重试：{(followup or '')[:120]}",
                inbound_id=str(inbound.message_id or ""),
            )
            if not result.get("ok"):
                err = Message.error(
                    f"重试失败节点失败：{result.get('message') or 'unknown'}",
                    task_id=task_id,
                )
                await self.publish(err)
                return err
            return card

        if not force_full:
            return await self._continue_plan_soft_append(
                task,
                followup=followup,
                cleaned_goal=cleaned_goal,
                inbound=inbound,
                base_ts=base_ts,
            )

        card = Message.card(
            f"已追问任务 {task_id[:8]}…（整图重规划）",
            task_id=task_id,
            meta={
                "status": "pending",
                "goal": cleaned_goal[:200],
                "mode": "full_replan",
                "reopen": task.status in {"completed", "failed", "cancelled"},
                "inbound_id": inbound.message_id,
                "plan_mode": True,
            },
        )
        card.ts = base_ts + 0.001
        await self.publish(card)

        await self.publish(
            Message.progress(
                "编排脑整图重规划中…",
                task_id=task_id,
                meta={"phase": "replan", "mode": "full_replan"},
            )
        )

        # build_plan_graph 内同步 chat()；不进线程池会冻住整站 HTTP/WS（含网页查询）
        rev = await self._run_blocking(
            replan_full_graph,
            self.supervisor,
            parent_task_id=task_id,
            root_goal=cleaned_goal,
            reason=f"重新执行：{(followup or '')[:120]}",
            inbound_id=str(inbound.message_id or ""),
        )
        if not rev.get("ok"):
            err = Message.error(
                f"整图重规划失败：{rev.get('reason') or 'unknown'}",
                task_id=task_id,
            )
            await self.publish(err)
            return err

        g_raw = rev.get("graph")
        g_obj = PlanGraph.from_dict(g_raw) if isinstance(g_raw, dict) else None
        graph_meta = plan_graph_progress_meta(g_obj)
        parent_live = self.supervisor.get_task(task_id)
        parent_status = str(parent_live.status or "running") if parent_live else "running"
        graph_meta.update(
            {
                "mode": "full_replan",
                "plan_version": rev.get("version"),
                "auto_dispatch": True,
                "cancelled_children": list(rev.get("cancelled") or []),
                "parent_status": parent_status,
                "status": parent_status,
            }
        )
        sketch = str(rev.get("sketch") or "")
        ver = rev.get("version")
        await self.publish(
            Message.progress(
                f"整图重规划完成 v{ver}"
                f"（取消旧子任务 {len(rev.get('cancelled') or [])}，"
                f"拉起 {len(rev.get('spawned') or [])}）\n{sketch}",
                task_id=task_id,
                meta=graph_meta,
            )
        )
        for cid in list(rev.get("spawned") or []):
            await self._maybe_start_worker(cid)
        if not rev.get("spawned"):
            await self.publish(
                Message.text_assistant(
                    "重规划后无就绪节点，请检查依赖或产物契约。",
                    task_id=task_id,
                )
            )
        return card

    async def _continue_plan_soft_append(
        self,
        task: Any,
        *,
        followup: str,
        cleaned_goal: str,
        inbound: Message,
        base_ts: float,
    ) -> Message:
        """非显式整图的追问：保留已完成节点，按追加分析续跑未决。"""
        from fawai_avatar.brain.orchestrator import analyze_plan_append
        from fawai_avatar.brain.plan_scheduler import (
            apply_append_to_running_supervisor,
            apply_plan_revision,
            load_graph,
            plan_graph_progress_meta,
            spawn_ready_children,
        )

        task_id = task.task_id
        self._active_task_id = task_id
        instruction = (followup or "").strip()[:500]
        card = Message.card(
            f"已追问任务 {task_id[:8]}…（保留已完成，续跑未决）",
            task_id=task_id,
            meta={
                "status": "pending",
                "goal": cleaned_goal[:200],
                "mode": "plan_append",
                "reopen": task.status in {"completed", "failed", "cancelled"},
                "inbound_id": inbound.message_id,
                "plan_mode": True,
                "parent_status": "running",
            },
        )
        card.ts = base_ts + 0.001
        await self.publish(card)

        parent = self.supervisor.get_task(task_id) or task
        graph = load_graph(parent.meta)
        if graph is None:
            err = Message.error("无任务图，无法软续跑；请显式「整图重规划」。", task_id=task_id)
            await self.publish(err)
            return err

        decision = await self._run_blocking(
            analyze_plan_append,
            root_goal=str(parent.goal or graph.root_goal or cleaned_goal),
            graph=graph,
            user_text=followup,
            node_id=None,
        )
        kind = str(decision.get("kind") or "no_graph_change")
        reason = str(decision.get("reason") or "")
        source = str(decision.get("source") or "")
        instr = str(decision.get("instruction") or instruction).strip() or instruction
        await self.publish(
            Message.progress(
                f"计划追加 → {kind}（{source}）{('：' + reason) if reason else ''}",
                task_id=task_id,
                meta={
                    "phase": "plan_append",
                    "kind": kind,
                    "reason": reason,
                    "source": source,
                    "inbound_id": inbound.message_id,
                },
            )
        )

        if instr:
            self.supervisor.merge_task_meta(
                task_id,
                {
                    "plan_dispatch_note": instr[:500],
                    "last_plan_append_kind": kind,
                    "mode": "plan_append",
                },
            )

        if kind == "new_task":
            return await self._create_and_maybe_run(
                followup, inbound=inbound, related_task_id=task_id
            )

        # 终态父计划须重开，否则无法拉起未决节点
        parent = self.supervisor.get_task(task_id) or parent
        if parent.status in {"failed", "cancelled", "completed"}:
            self.supervisor.requeue_task(task_id, reopen=True)
        parent = self.supervisor.get_task(task_id) or parent
        if parent.status in {"pending", "waiting", "paused"}:
            if parent.status == "paused":
                self.supervisor.resume(task_id)
            self.supervisor.set_running(task_id)

        if kind in {"reorder", "lease_adjust"}:
            rev = apply_plan_revision(
                self.supervisor,
                parent_task_id=task_id,
                kind=kind,
                instruction=instr or followup[:80],
                inbound_id=str(inbound.message_id or ""),
            )
            for cid in list(rev.get("spawned") or []):
                await self._maybe_start_worker(cid)
            parent2 = self.supervisor.get_task(task_id) or parent
            g_rev = load_graph(parent2.meta if parent2 else None)
            rev_meta = plan_graph_progress_meta(g_rev)
            rev_meta.update(
                {
                    "kind": kind,
                    "revision": True,
                    "parent_status": str(parent2.status or "") if parent2 else "",
                }
            )
            await self.publish(
                Message.progress(
                    str(rev.get("sketch") or "任务图已修订"),
                    task_id=task_id,
                    meta=rev_meta,
                )
            )
            return card

        # no_graph_change / dispatch_note：有失败则重试；否则下发 running + 拉就绪
        parent = self.supervisor.get_task(task_id) or parent
        graph = load_graph(parent.meta) or graph
        failed_ids = [n.id for n in graph.nodes if n.status == "failed"]
        if failed_ids:
            result = await self.retry_plan_nodes(
                task_id,
                node_ids=None,
                reason=f"追问续跑：{(followup or '')[:120]}",
                inbound_id=str(inbound.message_id or ""),
            )
            if not result.get("ok"):
                err = Message.error(
                    f"续跑失败节点失败：{result.get('message') or 'unknown'}",
                    task_id=task_id,
                )
                await self.publish(err)
                return err
            return card

        targets: list[str] = []
        for n in graph.nodes:
            if n.status == "running" and n.task_id:
                targets.append(n.task_id)
                apply_append_to_running_supervisor(
                    self.supervisor,
                    child_task_id=n.task_id,
                    instruction=instr or followup[:80],
                    inbound_id=str(inbound.message_id or ""),
                )
        spawned = spawn_ready_children(
            self.supervisor, parent_task_id=task_id, graph=graph
        )
        parent_live = self.supervisor.get_task(task_id)
        g_meta = plan_graph_progress_meta(graph)
        g_meta.update(
            {
                "mode": "plan_append",
                "kind": kind,
                "parent_status": str(parent_live.status or "running")
                if parent_live
                else "running",
                "status": str(parent_live.status or "running")
                if parent_live
                else "running",
            }
        )
        await self.publish(
            Message.progress(
                f"已下发追加约束 → running {len(targets)} · 新拉起 {len(spawned)}"
                f"{('：' + instr[:80]) if instr else ''}",
                task_id=task_id,
                meta=g_meta,
            )
        )
        for cid in spawned:
            await self._maybe_start_worker(cid)
        return card

    def _resolve_excise_target(
        self, task_id: str, *, plan_node_id: str = ""
    ) -> tuple[Any | None, str]:
        """解析挖步目标任务：监管脑子任务直接用；父计划须带 plan_node_id。"""
        from fawai_avatar.brain.plan_scheduler import (
            is_orchestrator_task,
            is_supervisor_task,
            load_graph,
        )

        task = self.supervisor.get_task(task_id)
        if task is None:
            return None, "unknown task"
        meta = dict(task.meta or {})
        if is_orchestrator_task(meta):
            nid = (plan_node_id or "").strip()
            if not nid:
                return None, "计划父任务挖步须指定 plan_node_id"
            graph = load_graph(meta)
            if graph is None:
                return None, "无任务图"
            node = graph.get(nid)
            if node is None:
                return None, f"节点 {nid} 不在活跃图上"
            child_id = str(node.task_id or "").strip()
            if not child_id:
                return None, f"节点 {nid} 尚无监管脑子任务"
            child = self.supervisor.get_task(child_id)
            if child is None:
                return None, "监管脑子任务不存在"
            if not is_supervisor_task(child.meta):
                return None, "目标不是监管脑子任务"
            return child, ""
        if is_supervisor_task(meta):
            return task, ""
        # 普通单任务也可挖步（与 resume-from-step 对称）
        return task, ""

    async def excise_step(
        self,
        task_id: str,
        step: int,
        *,
        plan_node_id: str = "",
    ) -> dict[str, Any]:
        """从执行上下文挖掉指定闭环步；不自动拉 Worker。

        计划模式：须绑监管脑子任务（或父 id + plan_node_id）；禁对编排器父直接挖。
        """
        from fawai_avatar.brain.plan_scheduler import (
            is_orchestrator_task,
            session_publish_task_id,
        )
        from fawai_avatar.runtime.checkpoint import (
            CHECKPOINT_KEY,
            excise_step_from_context,
        )

        target, err = self._resolve_excise_target(task_id, plan_node_id=plan_node_id)
        if target is None:
            return {"ok": False, "message": err or "unknown task"}
        worker_id = target.task_id
        if is_orchestrator_task(target.meta):
            return {
                "ok": False,
                "message": "计划父任务请对节点步骤「删除此步」",
                "status": target.status,
            }
        if self.has_live_worker(worker_id) or target.status == "running":
            return {
                "ok": False,
                "message": "任务执行中，请先停止再删除步骤",
                "status": target.status,
            }
        if target.status == "archived":
            return {"ok": False, "message": "已归档任务无法挖步"}
        try:
            step_n = int(step)
        except (TypeError, ValueError):
            return {"ok": False, "message": "invalid step"}
        if step_n < 0:
            return {"ok": False, "message": "step must be >= 0"}

        meta = dict(target.meta or {})
        try:
            patch = excise_step_from_context(meta, step=step_n)
        except KeyError:
            return {"ok": False, "message": f"步骤 {step_n} 不存在"}
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}

        meta[CHECKPOINT_KEY] = patch[CHECKPOINT_KEY]
        meta["steps"] = list(patch.get("steps") or [])
        meta["excised_step"] = step_n
        meta["excise_at"] = time.time()
        self.supervisor.replace_task_meta(worker_id, meta)

        session_id, mirror_extra = session_publish_task_id(self.supervisor, worker_id)
        self._active_task_id = session_id or worker_id
        card_meta: dict[str, Any] = {
            "ok": True,
            "status": str(target.status or ""),
            "phase": "excise_step",
            "mode": "excise_step",
            "step": step_n,
            "excised_step": step_n,
            "next_step": patch.get("next_step"),
        }
        if mirror_extra:
            card_meta.update(mirror_extra)
        await self.publish(
            Message.progress(
                f"已从上下文删除步骤 {step_n}",
                task_id=session_id or worker_id,
                meta=card_meta,
            )
        )
        self.log_system(
            "excise_step",
            message=f"挖掉步骤 {step_n}",
            ok=True,
            task_id=session_id or worker_id,
            actor="api",
            detail={
                "step": step_n,
                "worker_task_id": worker_id,
                "next_step": patch.get("next_step"),
                "plan_node_id": (mirror_extra or {}).get("plan_node_id"),
            },
        )
        out: dict[str, Any] = {
            "ok": True,
            "task_id": worker_id,
            "step": step_n,
            "excised_step": step_n,
            "next_step": patch.get("next_step"),
            "status": target.status,
            "message": f"excised step {step_n}",
        }
        if session_id and session_id != worker_id:
            out["parent_task_id"] = session_id
            if mirror_extra and mirror_extra.get("plan_node_id"):
                out["plan_node_id"] = mirror_extra.get("plan_node_id")
        return out

    async def resume_from_step(self, task_id: str, step: int) -> dict[str, Any]:
        """从指定闭环步骤恢复：写入检查点 → reopen/resume → 拉 Worker。

        计划模式监管脑子任务：同时把图节点标回 running、重开终态父计划；
        会话卡归并到父计划（不绑编排器父 id 做步骤恢复）。
        """
        from fawai_avatar.brain.plan_scheduler import (
            is_orchestrator_task,
            is_supervisor_task,
            plan_graph_progress_meta,
            plan_parent_stats_summary,
            resume_plan_child_node,
            session_publish_task_id,
        )
        from fawai_avatar.brain.plan_graph import PlanGraph
        from fawai_avatar.runtime.checkpoint import (
            CHECKPOINT_KEY,
            prepare_resume_from_step,
        )

        task = self.supervisor.get_task(task_id)
        if task is None:
            return {"ok": False, "message": "unknown task"}
        meta0 = dict(task.meta or {})
        if is_orchestrator_task(meta0):
            return {
                "ok": False,
                "message": "计划父任务请从节点步骤「从此恢复」，或使用「重试本节点」",
                "status": task.status,
            }
        if self.has_live_worker(task_id) or task.status == "running":
            return {
                "ok": False,
                "message": "任务执行中，请先停止再从步骤恢复",
                "status": task.status,
            }
        if task.status == "archived":
            return {"ok": False, "message": "已归档任务无法恢复，请先取消归档"}
        try:
            step_n = int(step)
        except (TypeError, ValueError):
            return {"ok": False, "message": "invalid step"}
        if step_n < 0:
            return {"ok": False, "message": "step must be >= 0"}

        plan_extra: dict[str, Any] = {}
        if is_supervisor_task(meta0):
            plan_extra = resume_plan_child_node(
                self.supervisor, child_task_id=task_id
            )
            if not plan_extra.get("ok"):
                return {
                    "ok": False,
                    "message": str(plan_extra.get("reason") or "计划节点无法从步骤恢复"),
                    "status": task.status,
                    **{
                        k: plan_extra.get(k)
                        for k in ("parent_task_id", "plan_node_id", "graph")
                    },
                }

        meta = dict(task.meta or {})
        meta[CHECKPOINT_KEY] = prepare_resume_from_step(meta, step=step_n)
        meta["resume_from_step"] = step_n
        meta.pop("error", None)
        # 澄清等待态恢复：清 awaiting，避免 Worker 误判
        clar = meta.get("clarification")
        if isinstance(clar, dict) and clar.get("phase") == "awaiting_input":
            clar = dict(clar)
            clar["phase"] = "resumed_from_step"
            meta["clarification"] = clar
        self.supervisor.replace_task_meta(task_id, meta)

        status_before = task.status
        if status_before == "paused":
            if not self.supervisor.resume(task_id):
                return {
                    "ok": False,
                    "message": f"无法恢复暂停任务（状态={status_before}）",
                    "status": status_before,
                }
        else:
            reopen = status_before in {"completed", "failed", "cancelled"}
            if status_before not in {
                "pending",
                "waiting",
                "blocked",
                "completed",
                "failed",
                "cancelled",
            }:
                return {
                    "ok": False,
                    "message": f"无法从状态 {status_before} 恢复",
                    "status": status_before,
                }
            if not self.supervisor.requeue_task(task_id, reopen=reopen):
                return {
                    "ok": False,
                    "message": f"无法续跑任务（状态={status_before}）",
                    "status": status_before,
                }

        session_id, mirror_extra = session_publish_task_id(self.supervisor, task_id)
        self._active_task_id = session_id or task_id
        started = await self._maybe_start_worker(task_id)
        updated = self.supervisor.get_task(task_id)
        status = updated.status if updated else "pending"
        parent_status = str(plan_extra.get("parent_status") or "")
        if mirror_extra and not parent_status:
            parent_live = self.supervisor.get_task(session_id)
            parent_status = str(parent_live.status or "") if parent_live else ""
        card_meta: dict[str, Any] = {
            "ok": True,
            "status": parent_status or status,
            "phase": "resumed",
            "mode": "resume_from_step",
            "step": step_n,
            "timeline_segment": "new",
            "worker_started": started,
        }
        if mirror_extra:
            card_meta.update(mirror_extra)
            card_meta["node_status"] = "running"
            if parent_status:
                card_meta["parent_status"] = parent_status
            g_raw = plan_extra.get("graph")
            if isinstance(g_raw, dict):
                g_meta = plan_graph_progress_meta(PlanGraph.from_dict(g_raw))
                card_meta.update(g_meta)
            parent_stats = plan_parent_stats_summary(self.supervisor, session_id)
            if parent_stats is not None:
                card_meta["parent_stats"] = parent_stats
        card = Message.card(
            f"从步骤 {step_n} 恢复执行…",
            task_id=session_id,
            meta=card_meta,
        )
        await self.publish(card)
        self.log_system(
            "resume_from_step",
            message=f"从步骤 {step_n} 恢复",
            ok=True,
            task_id=session_id,
            actor="api",
            detail={
                "step": step_n,
                "status": status,
                "worker_started": started,
                "worker_task_id": task_id,
                "plan_node_id": plan_extra.get("plan_node_id"),
                "parent_status": parent_status or None,
            },
        )
        out: dict[str, Any] = {
            "ok": True,
            "task_id": task_id,
            "step": step_n,
            "status": status,
            "worker_started": started,
            "message": f"resumed from step {step_n}",
        }
        if plan_extra.get("ok"):
            out["parent_task_id"] = plan_extra.get("parent_task_id")
            out["plan_node_id"] = plan_extra.get("plan_node_id")
            out["parent_status"] = parent_status or plan_extra.get("parent_status")
            if isinstance(plan_extra.get("graph"), dict):
                out["graph"] = plan_extra["graph"]
        return out

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
        task = self.supervisor.get_task(task_id)
        if task is not None:
            from fawai_avatar.brain.plan_scheduler import (
                is_awaiting_plan,
                is_orchestrator_task,
            )

            if is_awaiting_plan(task.meta):
                # 建卡后编排未完成，禁止抢跑逐步 Worker
                return False
            if is_orchestrator_task(task.meta):
                # 父计划只编排，不跑逐步执行脑
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
        """网关启动：为孤儿 running/waiting/blocked/pending 补拉 Worker（同步）。

        ``awaiting_plan`` 的 pending 不拉 Worker，改在已绑定事件循环后重跑编排。
        """
        if not (self._auto_run_worker and self._worker is not None):
            return []
        started: list[str] = []
        awaiting_plan_ids: list[str] = []
        from fawai_avatar.brain.plan_scheduler import (
            is_awaiting_plan,
            is_orchestrator_task,
        )
        from fawai_avatar.task.title import suggest_task_title

        for task_id in self.supervisor.list_recoverable_task_ids():
            task = self.supervisor.get_task(task_id)
            if task is not None and is_orchestrator_task(task.meta):
                continue
            if task is not None and is_awaiting_plan(task.meta):
                awaiting_plan_ids.append(task_id)
                continue
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
        loop = self._loop
        for task_id in awaiting_plan_ids:
            task = self.supervisor.get_task(task_id)
            if task is None:
                continue
            title = str((task.meta or {}).get("title") or "") or suggest_task_title(
                task.goal or ""
            )
            related = str((task.meta or {}).get("related_task_id") or "") or None
            inbound_id = str((task.meta or {}).get("inbound_id") or "") or None
            if loop is not None and loop.is_running():
                self._spawn_bg(
                    self._finish_create_orchestrate(
                        task_id,
                        goal=task.goal or "",
                        title=title,
                        related_task_id=related,
                        inbound_id=inbound_id,
                    )
                )
            else:
                # lifespan 里 bind_loop 后调用；无 loop 时记日志，下回启动再试
                logger.warning(
                    "awaiting_plan recover skipped (no loop) task=%s", task_id[:8]
                )
            started.append(task_id)
            self.log_system(
                "recover",
                message=f"进程恢复编排 {task_id[:8]}…",
                ok=True,
                task_id=task_id,
                actor="runtime",
            )
        if started:
            logger.info("recovered %d interrupted task(s)", len(started))
        return started

    def _run_worker_safe(self, task_id: str) -> None:
        assert self._worker is not None
        # 会话可见 id：监管脑子任务归并到父计划线程
        session_id, mirror_extra = self._session_publish_id(task_id)
        node_label = str(mirror_extra.get("plan_node_id") or "").strip()

        def _pub_meta(extra: dict[str, Any] | None = None) -> dict[str, Any]:
            out = dict(mirror_extra)
            if extra:
                out.update(extra)
            return out

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
            err_out = Message.error(f"工作任务异常: {err_msg}", task_id=session_id)
            if mirror_extra:
                err_out.meta = _pub_meta()
            self.publish_threadsafe(err_out)
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
            # 有选项时 PWA 在澄清卡内渲染可点按钮；无选项时卡文案已写「请直接回复」。
            # 不再另发 tip 文本——计划节点下 tip 会进 notes 并随恢复重复叠加。
            self.publish_threadsafe(
                Message.card(
                    card_text,
                    task_id=session_id,
                    meta=_pub_meta(
                        {
                            "ok": True,
                            "status": "waiting",
                            "phase": "clarification",
                            "question": question,
                            "options": options,
                            "awaiting_input": True,
                        }
                    ),
                )
            )
            return
        image_path = _first_image_from_result(result)
        # 监管脑归并到父会话时：节点终态写 node_status，父计划 status 仍取父任务库态
        # （仅 plan_finish 才把父计划标 completed/failed）
        publish_status = status
        card_extra: dict[str, Any] = {
            "ok": ok,
            "status": publish_status,
            "result": _clip_result(result),
        }
        if mirror_extra:
            parent_task = None
            try:
                parent_task = self.supervisor.get_task(session_id)
            except Exception:  # noqa: BLE001
                parent_task = None
            parent_st = str(parent_task.status if parent_task else "running") or "running"
            if parent_st in {"completed", "failed", "cancelled", "archived"}:
                # 子节点刚完成时父计划尚未收束；中间节点勿沿用误粘终态
                parent_st = "running"
            card_extra["status"] = parent_st
            card_extra["node_status"] = status
            card_extra["mode"] = "plan_node_finish"
            # ok 仍表示本节点成败；父计划完成只看后续 mode=plan_finish
        card_meta: dict[str, Any] = _pub_meta(card_extra)
        try:
            from fawai_avatar.task import stats as task_stats

            raw_stats = (task.meta or {}).get(task_stats.STATS_KEY) if task else None
            if isinstance(raw_stats, dict):
                card_meta["stats"] = task_stats.summary_for_api(raw_stats)
            if mirror_extra and session_id:
                from fawai_avatar.brain.plan_scheduler import plan_parent_stats_summary

                parent_live = self.supervisor.get_task(session_id)
                if parent_live is not None:
                    card_meta["parent_status"] = str(parent_live.status or "")
                parent_stats = plan_parent_stats_summary(self.supervisor, session_id)
                if parent_stats is not None:
                    card_meta["parent_stats"] = parent_stats
        except Exception:  # noqa: BLE001 — 统计失败不影响终态卡
            pass
        # 执行时间线已由 WorkerLoop.on_progress 流式推送；此处不再批量重放 steps。
        wrap_label = (
            f"节点 {node_label} → {status}"
            if node_label
            else f"任务 {task_id[:8]}… → {status}"
        )
        self.publish_threadsafe(
            Message.card(
                wrap_label,
                task_id=session_id,
                meta=card_meta,
            )
        )
        if image_path:
            img_out = Message.image_out(
                media_path=image_path,
                text=_observe_image_caption(result),
                task_id=session_id,
            )
            if mirror_extra:
                img_out.meta = _pub_meta()
            self.publish_threadsafe(img_out)
        if ok:
            summary = _success_summary(result) or "完成"
        elif status == "cancelled" or str(result.get("error") or "") == "cancelled":
            summary = "任务已取消"
        else:
            summary = str(result.get("error") or _last_fail_message(result) or "未完成")
        sum_out = Message.text_assistant(str(summary), task_id=session_id)
        if mirror_extra:
            sum_out.meta = _pub_meta()
        self.publish_threadsafe(sum_out)

        # 计划模式：监管脑终态 → 规则衔接下游 / 收束父计划
        try:
            from fawai_avatar.brain.plan_scheduler import (
                is_supervisor_task,
                load_graph,
                on_supervisor_finished,
                persist_plan_parent_stats,
                plan_graph_progress_meta,
                plan_parent_stats_summary,
            )

            if task is not None and is_supervisor_task(task.meta):
                advance = on_supervisor_finished(
                    self.supervisor,
                    child_task_id=task_id,
                    ok=ok and status == "completed",
                    final_thought=str(
                        (result or {}).get("final_thought")
                        or ((task.meta or {}).get("final_thought") if task else "")
                        or summary
                        or ""
                    ),
                    error=str((result or {}).get("error") or ""),
                )
                parent_id = str(advance.get("parent_task_id") or "") or session_id
                if parent_id:
                    persist_plan_parent_stats(self.supervisor, parent_id)
                # 刷新流程图（含下游 running）
                parent = self.supervisor.get_task(parent_id) if parent_id else None
                g_live = load_graph(parent.meta if parent else None)
                if parent_id and g_live is not None:
                    g_meta = plan_graph_progress_meta(g_live)
                    if parent is not None:
                        g_meta["status"] = str(parent.status or "")
                        g_meta["parent_status"] = str(parent.status or "")
                    parent_stats = plan_parent_stats_summary(self.supervisor, parent_id)
                    if parent_stats is not None:
                        g_meta["parent_stats"] = parent_stats
                        g_meta["stats"] = parent_stats
                    self.publish_threadsafe(
                        Message.progress(
                            "任务图已更新",
                            task_id=parent_id,
                            meta=g_meta,
                        )
                    )
                for cid in list(advance.get("spawned") or []):
                    # 同步上下文里拉起下游 Worker
                    thread = threading.Thread(
                        target=self._run_worker_safe,
                        args=(cid,),
                        name=f"fawai-worker-{cid[:8]}",
                        daemon=True,
                    )
                    with self._lock:
                        existing = self._worker_threads.get(cid)
                        if existing is not None and existing.is_alive():
                            continue
                        self._worker_threads[cid] = thread
                        thread.start()
                if parent_id and (
                    advance.get("parent_completed") or advance.get("parent_failed")
                ):
                    parent_stats = persist_plan_parent_stats(self.supervisor, parent_id)
                    parent = self.supervisor.get_task(parent_id)
                    p_status = parent.status if parent else "unknown"
                    p_thought = str((parent.meta or {}).get("final_thought") or "") if parent else ""
                    finish_meta: dict[str, Any] = {
                        "ok": bool(advance.get("parent_completed")),
                        "status": p_status,
                        "parent_status": p_status,
                        "mode": "plan_finish",
                    }
                    if parent_stats is not None:
                        finish_meta["stats"] = parent_stats
                        finish_meta["parent_stats"] = parent_stats
                    self.publish_threadsafe(
                        Message.card(
                            f"计划 {parent_id[:8]}… → {p_status}",
                            task_id=parent_id,
                            meta=finish_meta,
                        )
                    )
                    if p_thought:
                        # plan_conclusion：PWA 把结论文挂到节点卡之后（#msgsTail）
                        self.publish_threadsafe(
                            Message.text_assistant(
                                p_thought,
                                task_id=parent_id,
                                meta={
                                    "mode": "plan_finish",
                                    "plan_conclusion": True,
                                    "status": p_status,
                                    "parent_status": p_status,
                                },
                            )
                        )
        except Exception:  # noqa: BLE001 — 计划调度失败不影响子任务已发出的终态
            logger.exception("plan advance after worker failed task_id=%s", task_id)

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
            {
                "text": followup[:500],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
                "mode": "review",
            }
        )
        hist = list((task.meta or {}).get("user_guidance_history") or [])
        if guidance and (not hist or hist[-1] != guidance):
            hist.append(guidance)
        self.supervisor.merge_task_meta(
            task_id,
            {
                "review_phase": "analyzing",
                "user_guidance": guidance,
                "user_guidance_history": hist[-12:],
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
        # 与普通任务规划同口径：立刻给出 phase=analyzing 步骤卡，后续由分析线程直播/心跳
        await self.publish(
            Message.progress(
                tip,
                task_id=task_id,
                meta={"phase": "analyzing", "step": 0, "status": "running"},
            )
        )
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
            {
                "text": followup[:500],
                "inbound_id": inbound.message_id,
                "ts": inbound.ts,
                "mode": "review",
            }
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
            # 与说明一致：裸取消 → completed + last_review.cancelled（可再开新复盘任务）
            self.supervisor.complete(task_id)
            card = Message.card(
                tip,
                task_id=task_id,
                meta={
                    "ok": True,
                    "control": "review_cancel",
                    "phase": "completed",
                    "status": "completed",
                },
            )
            await self.publish(card)
            await self.publish(Message.text_assistant(tip, task_id=task_id))
            return card

        if mode == "revise":
            guidance = str(parsed.get("revise_guidance") or "").strip()
            # 保留上一轮 review_analysis / review_items，供分析线程回灌；只刷新 guidance
            hist = list(meta.get("user_guidance_history") or [])
            prev_g = str(meta.get("user_guidance") or "").strip()
            if prev_g and (not hist or hist[-1] != prev_g):
                hist.append(prev_g)
            if guidance and (not hist or hist[-1] != guidance):
                hist.append(guidance)
            self.supervisor.merge_task_meta(
                task_id,
                {
                    "review_phase": "analyzing",
                    "user_guidance": guidance,
                    "user_guidance_history": hist[-12:],
                },
            )
            tip = "已收到改写建议，重新分析清单…"
            card = Message.card(
                tip,
                task_id=task_id,
                meta={"ok": True, "control": "review_revise", "phase": "analyzing"},
            )
            await self.publish(card)
            await self.publish(
                Message.progress(
                    tip,
                    task_id=task_id,
                    meta={"phase": "analyzing", "step": 0, "status": "running"},
                )
            )
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
        await self.publish(
            Message.progress(
                tip,
                task_id=task_id,
                meta={
                    "phase": "applying",
                    "step": 0,
                    "status": "running",
                    "selected_ids": selected,
                },
            )
        )
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

    def _review_stats_load(self, review_task_id: str) -> dict[str, Any]:
        from fawai_avatar.task import stats as task_stats

        try:
            task = self.supervisor.get_task(review_task_id)
            meta = dict(task.meta) if task else {}
            stats = task_stats.ensure_task_stats(meta)
            # 新一段：丢弃未闭合墙钟，避免脏时间；既有 token/轮次保留累加
            stats["run_started_at"] = None
            return stats
        except Exception as exc:  # noqa: BLE001
            logger.warning("review stats load failed task=%s: %s", review_task_id, exc)
            return task_stats.empty_stats()

    def _review_stats_persist(self, review_task_id: str, stats: dict[str, Any]) -> None:
        from fawai_avatar.task import stats as task_stats

        try:
            self.supervisor.merge_task_meta(
                review_task_id,
                {task_stats.STATS_KEY: task_stats.persistable(stats)},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("review stats persist failed task=%s: %s", review_task_id, exc)

    def _review_stats_summary(self, stats: dict[str, Any]) -> dict[str, Any]:
        from fawai_avatar.task import stats as task_stats

        try:
            return task_stats.summary_for_api(stats)
        except Exception:  # noqa: BLE001
            return {}

    def _review_stats_begin(self, review_task_id: str, stats: dict[str, Any]) -> None:
        from fawai_avatar.task import stats as task_stats

        task_stats.begin_run_segment(stats)
        self._review_stats_persist(review_task_id, stats)

    def _review_stats_finish_segment(
        self, review_task_id: str, stats: dict[str, Any], *, count_round: bool = True
    ) -> dict[str, Any]:
        from fawai_avatar.task import stats as task_stats

        if count_round:
            task_stats.apply_round(stats)
        task_stats.end_run_segment(stats)
        self._review_stats_persist(review_task_id, stats)
        return self._review_stats_summary(stats)

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

        stats = self._review_stats_load(review_task_id)
        self._review_stats_begin(review_task_id, stats)

        def _review_progress(
            text: str, *, phase: str = "analyzing", step: int = 0, **extra: Any
        ) -> None:
            meta_p: dict[str, Any] = {"phase": phase, "step": step, **extra}
            self.emit_progress(text, task_id=review_task_id, meta=meta_p)

        guidance = str(meta.get("user_guidance") or "").strip() or None
        distill = bool(meta.get("distill", True))
        ratchet = bool(meta.get("ratchet", False))
        # 改写轮：回灌上一轮分析/清单 + 复盘会话多轮（材料契约见 skills/review.py）
        prior_analysis = str(meta.get("review_analysis") or "").strip() or None
        prior_items = list(meta.get("review_items") or []) or None
        review_followups = list(meta.get("followups") or []) or None
        try:
            result = analyze_review_tasks(
                self.supervisor,
                source_ids,
                distill=distill,
                ratchet=ratchet,
                user_guidance=guidance,
                prior_analysis=prior_analysis,
                prior_items=prior_items,
                review_followups=review_followups,
                stats=stats,
                on_progress=_review_progress,
            )
        except Exception as exc:  # noqa: BLE001
            summary = self._review_stats_finish_segment(review_task_id, stats)
            self.supervisor.fail(review_task_id, message=str(exc))
            self.supervisor.merge_task_meta(
                review_task_id,
                {"review_phase": "failed", "ok": False, "error": str(exc)},
            )
            self.publish_threadsafe(
                Message.error(f"复盘分析异常: {exc}", task_id=review_task_id)
            )
            fail_meta: dict[str, Any] = {
                "ok": False,
                "control": "review",
                "phase": "failed",
                "status": "failed",
            }
            if summary:
                fail_meta["stats"] = summary
            self.publish_threadsafe(
                Message.card("复盘分析异常", task_id=review_task_id, meta=fail_meta)
            )
            return

        summary = self._review_stats_finish_segment(review_task_id, stats)
        text = str(result.get("message") or "复盘分析结束")
        ok = bool(result.get("ok"))
        progress_meta: dict[str, Any] = {
            "phase": "analyzing",
            "step": 0,
            "live": False,
        }
        if summary:
            progress_meta["stats"] = summary
        self.publish_threadsafe(
            Message.progress(
                "全维分析完成，等待勾选优化项…",
                task_id=review_task_id,
                meta=progress_meta,
            )
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
            fail_card: dict[str, Any] = {
                "ok": False,
                "control": "review",
                "phase": "failed",
                "status": "failed",
            }
            if summary:
                fail_card["stats"] = summary
            self.publish_threadsafe(
                Message.card(
                    "复盘分析失败",
                    task_id=review_task_id,
                    meta=fail_card,
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
        card_meta: dict[str, Any] = {
            "ok": True,
            "control": "review",
            "phase": "awaiting_apply",
            "status": "waiting",
            "detail_fetched": bool(result.get("detail_fetched")),
            "item_count": len(ui_items),
            "items": ui_items,
            "source_task_ids": source_ids,
            "joint": bool(result.get("joint")),
        }
        if summary:
            card_meta["stats"] = summary
        self.publish_threadsafe(
            Message.card(
                f"复盘分析完成（{detail}）→ 等待勾选优化",
                task_id=review_task_id,
                meta=card_meta,
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

        stats = self._review_stats_load(review_task_id)
        self._review_stats_begin(review_task_id, stats)

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
            summary = self._review_stats_finish_segment(review_task_id, stats)
            self.supervisor.fail(review_task_id, message=str(exc))
            self.supervisor.merge_task_meta(
                review_task_id,
                {"review_phase": "failed", "ok": False, "error": str(exc)},
            )
            self.publish_threadsafe(
                Message.error(f"复盘执行异常: {exc}", task_id=review_task_id)
            )
            fail_meta: dict[str, Any] = {
                "ok": False,
                "control": "review",
                "phase": "failed",
                "status": "failed",
            }
            if summary:
                fail_meta["stats"] = summary
            self.publish_threadsafe(
                Message.card("复盘执行异常", task_id=review_task_id, meta=fail_meta)
            )
            return

        summary = self._review_stats_finish_segment(review_task_id, stats)
        text = str(result.get("message") or "复盘执行结束")
        ok = bool(result.get("ok"))
        progress_meta = {
            "phase": "applying",
            "step": 0,
            "live": False,
        }
        if summary:
            progress_meta["stats"] = summary
        self.publish_threadsafe(
            Message.progress(
                "优化执行完成，整理结果…",
                task_id=review_task_id,
                meta=progress_meta,
            )
        )
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
        card_meta: dict[str, Any] = {
            "ok": ok,
            "control": "review",
            "phase": phase,
            "status": phase,
            "skill_id": (result.get("skill") or {}).get("id"),
            "release_id": backup.get("release_id"),
            "selected_ids": result.get("selected_ids"),
            "source_task_ids": source_ids,
            "joint": bool(meta.get("joint")),
        }
        if summary:
            card_meta["stats"] = summary
        self.publish_threadsafe(
            Message.card(
                f"复盘执行 → {'ok' if ok else 'fail'}"
                + (f"；存档 {backup.get('release_id')}" if backup.get("release_id") else ""),
                task_id=review_task_id,
                meta=card_meta,
            )
        )

    def emit_progress(self, text: str, *, task_id: str | None = None, meta: dict[str, Any] | None = None) -> None:
        """进度默认归并到父计划会话（监管脑子任务不单独占侧栏线程）。"""
        pub_id = task_id
        pub_meta = dict(meta or {})
        if task_id:
            try:
                from fawai_avatar.brain.plan_scheduler import (
                    plan_parent_stats_summary,
                    session_publish_task_id,
                )

                pub_id, extra = session_publish_task_id(self.supervisor, task_id)
                pub_meta.update(extra)
                # 节点卡继续用 meta.stats（子任务）；侧栏用 parent_stats / parent_status（整任务）
                if extra.get("mirrored_from_plan_child") and pub_id:
                    parent = self.supervisor.get_task(pub_id)
                    if parent is not None:
                        # 子节点已在跑而父库仍 pending：纠正为 running（侧栏整任务态）
                        if parent.status == "pending":
                            try:
                                self.supervisor.set_running(pub_id)
                                parent = self.supervisor.get_task(pub_id) or parent
                            except Exception:  # noqa: BLE001
                                pass
                        pub_meta["parent_status"] = str(parent.status or "")
                    parent_stats = plan_parent_stats_summary(self.supervisor, pub_id)
                    if parent_stats is not None:
                        pub_meta["parent_stats"] = parent_stats
            except Exception:  # noqa: BLE001
                pub_id = task_id
        msg = Message.progress(text, task_id=pub_id, meta=pub_meta)
        # 直播增量：只推前端，避免 token 级刷爆 session_history
        if pub_meta.get("live"):
            self.broadcast_threadsafe(msg)
        else:
            self.publish_threadsafe(msg)

    def _session_publish_id(self, task_id: str) -> tuple[str, dict[str, Any]]:
        from fawai_avatar.brain.plan_scheduler import session_publish_task_id

        return session_publish_task_id(self.supervisor, task_id)

    async def _publish_for_worker_task(
        self, message: Message, *, worker_task_id: str
    ) -> None:
        """监管脑子任务出站归并到父计划会话（澄清答复卡等）。"""
        session_id, extra = self._session_publish_id(worker_task_id)
        message.task_id = session_id
        if extra:
            meta = dict(message.meta or {})
            meta.update(extra)
            message.meta = meta
        await self.publish(message)

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
        return _clip_user_text(thought, limit=8000)

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
