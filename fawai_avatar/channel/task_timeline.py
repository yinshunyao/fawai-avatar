# term:会话通道  doc:doc/功能/会话通道/说明.md
"""当会话历史裁掉 progress 时，从任务 meta.steps 回放执行时间线。"""
from __future__ import annotations

from typing import Any

from fawai_avatar.channel.message import Message
from fawai_avatar.channel.redact import redact_secrets
from fawai_avatar.task.models import TaskRecord

_RESULT_PREVIEW = 160
_THOUGHT_PREVIEW = 200


def timeline_needs_rebuild(messages: list[Message], task: TaskRecord) -> bool:
    """会话侧已无 progress，但任务库仍有 steps / 终态结论 → 需要回放。"""
    if any(m.kind == "progress" for m in messages):
        return False
    meta = task.meta or {}
    steps = meta.get("steps") or []
    if isinstance(steps, list) and steps:
        return True
    if str(meta.get("final_thought") or "").strip():
        return True
    return False


def rebuild_timeline_from_task(task: TaskRecord) -> list[Message]:
    """由 meta.steps / final_thought / status 合成 progress + 终态卡 + 助手摘要。

    结果正文刻意短截，避免把配置口令等长输出原样塞回时间线。
    """
    tid = task.task_id
    meta = task.meta or {}
    steps = [s for s in (meta.get("steps") or []) if isinstance(s, dict)]
    base_ts = float(task.created_at or 0.0) or Message().ts
    end_ts = float(task.updated_at or 0.0) or (base_ts + max(len(steps), 1) * 5.0)
    if end_ts <= base_ts:
        end_ts = base_ts + max(len(steps), 1) * 5.0
    span = end_ts - base_ts
    n = max(len(steps), 1)
    out: list[Message] = []
    run_id = f"recon-{tid[:12]}"

    out.append(
        _progress(
            "开始执行任务（历史回放）",
            task_id=tid,
            ts=base_ts,
            meta={
                "phase": "started",
                "step": 0,
                "status": "running",
                "run_id": run_id,
                "reconstructed": True,
            },
            mid=f"recon-{tid}-started",
        )
    )

    for i, step in enumerate(steps):
        plan = step.get("plan") if isinstance(step.get("plan"), dict) else {}
        results = step.get("results") if isinstance(step.get("results"), list) else []
        actions = plan.get("actions") if isinstance(plan.get("actions"), list) else []
        thought = _redact(str(plan.get("thought") or "").strip())
        status = str(plan.get("status") or "").strip() or "continue"
        # 步骤时间摊在 created→updated 之间，便于与真实追问气泡交错
        step_ts = base_ts + span * ((i + 1) / (n + 1))

        preview = thought
        if len(preview) > _THOUGHT_PREVIEW:
            preview = preview[:_THOUGHT_PREVIEW] + "…"
        planned = f"规划完成 · {status}"
        if preview:
            planned = f"{planned}：{preview}"
        out.append(
            _progress(
                planned,
                task_id=tid,
                ts=step_ts,
                meta={
                    "phase": "planned",
                    "step": i,
                    "run_id": run_id,
                    "reconstructed": True,
                },
                mid=f"recon-{tid}-planned-{i}",
            )
        )

        if actions:
            act_lines = [_summarize_action_dict(a) for a in actions if isinstance(a, dict)]
            act_text = "执行中：" + ("；".join(act_lines) if act_lines else "动作")
            if len(act_text) > 900:
                act_text = act_text[:899] + "…"
            tool = ""
            if act_lines:
                tool = str((actions[0] or {}).get("kind") or "")
            out.append(
                _progress(
                    act_text,
                    task_id=tid,
                    ts=step_ts + 0.001,
                    meta={
                        "phase": "act",
                        "step": i,
                        "run_id": run_id,
                        "tool": tool or None,
                        "reconstructed": True,
                    },
                    mid=f"recon-{tid}-act-{i}",
                )
            )

        for j, raw in enumerate(results):
            if not isinstance(raw, dict):
                continue
            ok = bool(raw.get("ok"))
            # 回放不回灌原始 stdout（配置/口令文件常落在这里）；只留动作摘要 + 成败
            head = ""
            if j < len(actions) and isinstance(actions[j], dict):
                head = _summarize_action_dict(actions[j])
            err = ""
            if not ok:
                err = _clip(str(raw.get("message") or "").strip().splitlines()[0] if raw.get("message") else "", 80)
            prefix = "执行: " if ok else "失败: "
            if ok:
                body = head or "ok"
            else:
                body = f"{head}\n{err}" if head and err else (head or err or "failed")
            out.append(
                _progress(
                    prefix + body,
                    task_id=tid,
                    ts=step_ts + 0.002 + 0.0001 * j,
                    meta={
                        "phase": "acted",
                        "ok": ok,
                        "step": i,
                        "run_id": run_id,
                        "reconstructed": True,
                    },
                    mid=f"recon-{tid}-acted-{i}-{j}",
                )
            )

    final = _redact(str(meta.get("final_thought") or "").strip())
    if not final and steps:
        last_plan = steps[-1].get("plan") if isinstance(steps[-1].get("plan"), dict) else {}
        final = _redact(str(last_plan.get("thought") or "").strip())

    status = str(task.status or "")
    ok = bool(meta.get("ok")) if "ok" in meta else status == "completed"
    card_meta: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "reconstructed": True,
    }
    stats = meta.get("stats")
    if isinstance(stats, dict):
        card_meta["stats"] = stats
    out.append(
        Message(
            kind="card",
            role="assistant",
            text=f"任务 {tid[:8]}… → {status}",
            task_id=tid,
            meta=card_meta,
            message_id=f"recon-{tid}-card",
            ts=end_ts,
        )
    )
    if final:
        out.append(
            Message(
                kind="text",
                role="assistant",
                text=final,
                task_id=tid,
                meta={"reconstructed": True},
                message_id=f"recon-{tid}-final",
                ts=end_ts + 0.001,
            )
        )
    return out


def merge_live_and_rebuilt(
    live: list[Message], rebuilt: list[Message]
) -> list[Message]:
    """保留会话里真实用户/出站信封，缺 progress 时插入回放段。"""
    if not rebuilt:
        return list(live)
    live_ids = {m.message_id for m in live if m.message_id}
    extra = [m for m in rebuilt if m.message_id and m.message_id not in live_ids]
    if not extra:
        return list(live)
    # 用户消息保持原位；回放 progress 插在首条用户之后、终态卡之前
    users = [m for m in live if m.role == "user"]
    others = [m for m in live if m.role != "user"]
    progress = [m for m in extra if m.kind == "progress"]
    finals = [m for m in extra if m.kind != "progress"]
    merged = [*users, *progress, *others, *finals]
    merged.sort(key=lambda m: (float(m.ts or 0.0), 0 if m.role == "user" else 1))
    return merged


def _progress(
    text: str,
    *,
    task_id: str,
    ts: float,
    meta: dict[str, Any],
    mid: str,
) -> Message:
    return Message(
        kind="progress",
        role="assistant",
        text=text,
        task_id=task_id,
        meta=meta,
        message_id=mid,
        ts=ts,
    )


def _summarize_action_dict(action: dict[str, Any]) -> str:
    kind = str(action.get("kind") or "?").strip() or "?"
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    cmd = args.get("cmd") or args.get("command")
    path = args.get("path")
    if cmd is not None and str(cmd).strip():
        return f"{kind}: {_clip(_redact(str(cmd)), 120)}"
    if path is not None and str(path).strip():
        return f"{kind}: path={_clip(str(path), 120)}"
    return kind


def _redact(text: str) -> str:
    return redact_secrets(text or "")


def _clip(text: str, max_len: int) -> str:
    s = _redact((text or "").replace("\r\n", "\n").strip())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
