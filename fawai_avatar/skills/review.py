# term:任务复盘  doc:doc/功能/任务复盘/说明.md
"""按需复盘：全维深析 → 优化清单勾选 → 备份后只执行选中项。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fawai_avatar.channel.message import strip_image_marks
from fawai_avatar.skills.store import SkillStore, SkillValidationError, validate_skill_payload
from fawai_avatar.task.models import TaskRecord
from fawai_avatar.task.supervisor import Supervisor

_VALID_KINDS = frozenset({"skill", "lesson", "code_patch", "rule_patch", "prompt_patch"})
_PATCH_KINDS = frozenset({"code_patch", "rule_patch", "prompt_patch"})
_DIMENSION_KEYS = (
    "planning",
    "execution",
    "scheduling",
    "tool_results",
    "outcome_judgment",
)
_DIMENSION_LABELS = {
    "planning": "任务规划",
    "execution": "任务执行",
    "scheduling": "调度过程",
    "tool_results": "工具使用结果",
    "outcome_judgment": "结果判断",
}
_MAX_JOINT = 12
_APPROVE_ONLY_RE = re.compile(
    r"^(?:放行|执行|确认|同意|开始|go|ok|yes)[!！。.\s]*$",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"^(?:取消|不改|不用了|跳过全部|全部跳过|abort|cancel|no)[!！。.\s]*$",
    re.IGNORECASE,
)
_ALL_APPLY_RE = re.compile(
    r"^(?:全部|全部执行|全部放行|执行全部|放行|确认执行|go|ok|yes)[!！。.\s]*$",
    re.IGNORECASE,
)
_EXEC_IDS_RE = re.compile(
    r"(?:执行|只要|仅|只选|选)\s*([0-9]+(?:\s*[,，、\s]\s*[0-9]+)*)",
    re.IGNORECASE,
)
_SKIP_IDS_RE = re.compile(
    r"(?:不要|跳过|排除)\s*([0-9]+(?:\s*[,，、\s]\s*[0-9]+)*)",
    re.IGNORECASE,
)
_ID_TOKEN_RE = re.compile(r"[0-9]+")


def review_task(
    supervisor: Supervisor,
    task_id: str,
    *,
    material: str | None = None,
    distill: bool = True,
    ratchet: bool = False,
    skills_dir: str | None = None,
    work_root: str | Path | None = None,
    use_llm: bool | None = None,
    user_guidance: str | None = None,
    auto_apply: bool = True,
) -> dict[str, Any]:
    """单任务复盘（兼容入口）。默认分析后自动 apply 建议勾选项（引擎单测用）。"""
    return review_tasks(
        supervisor,
        [task_id],
        material=material,
        distill=distill,
        ratchet=ratchet,
        skills_dir=skills_dir,
        work_root=work_root,
        use_llm=use_llm,
        user_guidance=user_guidance,
        auto_apply=auto_apply,
    )


def propose_review_session(
    supervisor: Supervisor,
    task_ids: list[str],
    *,
    distill: bool = True,
    ratchet: bool = False,
) -> dict[str, Any]:
    """创建复盘任务：先写指令并进入 waiting，等用户放行或附加建议。"""
    ids = _normalize_task_ids(task_ids)
    if not ids:
        return {"ok": False, "message": "缺少 task_ids", "task_ids": []}
    if len(ids) > _MAX_JOINT:
        return {
            "ok": False,
            "message": f"一次最多联合 {_MAX_JOINT} 个任务",
            "task_ids": ids,
        }

    sources: list[TaskRecord] = []
    missing: list[str] = []
    for tid in ids:
        task = supervisor.get_task(tid)
        if task is None:
            missing.append(tid)
        else:
            sources.append(task)
    if missing:
        return {
            "ok": False,
            "message": f"未知任务: {', '.join(x[:8] for x in missing)}",
            "task_ids": ids,
        }

    joint = len(sources) > 1
    instruction = build_review_instruction(sources)
    goal = _review_goal_title(sources)
    review_task_id = supervisor.create_task(
        goal,
        meta={
            "kind": "review",
            "review_phase": "awaiting_approval",
            "source_task_ids": [t.task_id for t in sources],
            "review_instruction": instruction,
            "distill": bool(distill),
            "ratchet": bool(ratchet),
            "joint": joint,
            "source": "review_propose",
        },
    )
    supervisor.set_waiting(review_task_id)
    prompt = (
        instruction
        + "\n\n---\n"
        + "请回复「放行」开始全维分析；也可直接写附加建议（发送即按建议分析）。"
        + "\n分析只出【优化清单】，不会直接改代码/写 Skill；勾选同意后再备份执行。"
    )
    return {
        "ok": True,
        "message": prompt,
        "instruction": instruction,
        "phase": "awaiting_approval",
        "task_id": review_task_id,
        "source_task_ids": [t.task_id for t in sources],
        "joint": joint,
        "distill": bool(distill),
        "ratchet": bool(ratchet),
    }


def is_review_awaiting_approval(task: TaskRecord | None) -> bool:
    if task is None:
        return False
    meta = task.meta or {}
    return meta.get("kind") == "review" and meta.get("review_phase") == "awaiting_approval"


def is_review_awaiting_apply(task: TaskRecord | None) -> bool:
    if task is None:
        return False
    meta = task.meta or {}
    return meta.get("kind") == "review" and meta.get("review_phase") == "awaiting_apply"


def parse_review_approval(text: str) -> dict[str, Any]:
    """任意非空跟进都视为放行；纯放行话术无附加建议。"""
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "message": "请回复「放行」或附加建议后再发送"}
    if _APPROVE_ONLY_RE.match(raw):
        return {"ok": True, "guidance": ""}
    return {"ok": True, "guidance": raw}


def parse_review_apply_reply(
    text: str,
    items: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """解析第二道闸：全部 / 执行编号 / 不要编号 / 取消 / 改写建议。"""
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "message": "请回复「全部」「执行 1,3」「不要 2」或「取消」"}
    catalog = _normalize_items(items)
    all_ids = [str(it["id"]) for it in catalog]

    if _CANCEL_RE.match(raw):
        return {
            "ok": True,
            "mode": "cancel",
            "selected_ids": [],
            "revise_guidance": "",
            "cancel": True,
        }

    if _ALL_APPLY_RE.match(raw):
        return {
            "ok": True,
            "mode": "apply",
            "selected_ids": all_ids,
            "revise_guidance": "",
            "cancel": False,
        }

    exec_m = _EXEC_IDS_RE.search(raw)
    skip_m = _SKIP_IDS_RE.search(raw)
    if exec_m or skip_m:
        selected = set(all_ids)
        if exec_m:
            wanted = _parse_id_list(exec_m.group(1), all_ids)
            if not wanted:
                return {"ok": False, "message": "未识别到有效编号，请用「执行 1,3」"}
            selected = set(wanted)
        if skip_m:
            skipped = set(_parse_id_list(skip_m.group(1), all_ids))
            if exec_m:
                selected -= skipped
            else:
                selected = set(all_ids) - skipped
        ordered = [i for i in all_ids if i in selected]
        return {
            "ok": True,
            "mode": "apply",
            "selected_ids": ordered,
            "revise_guidance": "",
            "cancel": False,
        }

    # 纯数字列表也视为勾选
    only_ids = _ID_TOKEN_RE.findall(raw)
    if only_ids and re.fullmatch(r"[\d\s,，、]+", raw):
        wanted = _parse_id_list(raw, all_ids)
        if wanted:
            return {
                "ok": True,
                "mode": "apply",
                "selected_ids": wanted,
                "revise_guidance": "",
                "cancel": False,
            }

    return {
        "ok": True,
        "mode": "revise",
        "selected_ids": [],
        "revise_guidance": raw,
        "cancel": False,
    }


def build_review_instruction(tasks: list[TaskRecord]) -> str:
    joint = len(tasks) > 1
    lines = [
        "【复盘指令】" + ("联合复盘" if joint else "单任务复盘"),
        f"待复盘任务数：{len(tasks)}",
        "流程：放行后全维深析（规划/执行/调度/工具结果/结果判断）→ 出示优化清单 → "
        "勾选同意后先存档再执行（skill/lesson/代码/规则/提示词）。",
        "成功与失败都可沉淀；禁止一律写成成功 Skill；分析阶段不直接改代码。",
        "",
        "对象一览：",
    ]
    for i, t in enumerate(tasks, start=1):
        meta = t.meta or {}
        thought = str(meta.get("final_thought") or "").strip()
        err = str(meta.get("error") or "").strip()
        goal_disp = strip_image_marks(t.goal or "")
        has_img = "[image:" in (t.goal or "").lower()
        if has_img and goal_disp:
            goal_disp = f"{goal_disp} （附图）"
        elif has_img:
            goal_disp = "（附图）"
        lines.append(
            f"{i}. [{t.task_id[:8]}…] status={t.status} "
            f"goal={goal_disp[:120]}"
        )
        if thought:
            lines.append(f"   结论：{thought[:200]}")
        if err:
            lines.append(f"   错误：{err[:200]}")
        lines.append(f"   有逐步轨迹：{'是' if _has_step_trace(t) else '否'}")
        lines.append(
            f"   调度：replan={meta.get('replan_count', 0)} "
            f"reflect={meta.get('reflect_count', 0)} "
            f"stagnation={meta.get('stagnation_count', 0)}"
        )
    lines.append("")
    lines.append("简要素材预览（分析时若有 steps 将喂逐步轨迹）：")
    lines.append(build_materials_text(tasks, include_steps=False)[:3500])
    return "\n".join(lines)


def _review_goal_title(tasks: list[TaskRecord]) -> str:
    if len(tasks) == 1:
        raw = tasks[0].goal or ""
        g = strip_image_marks(raw).strip()
        if "[image:" in raw.lower() and not g:
            g = "（附图）"
        elif "[image:" in raw.lower() and "（附图）" not in g:
            g = f"{g} （附图）".strip()
        g = g[:80]
        return f"【复盘】{tasks[0].task_id[:8]}… {g}".strip()
    shorts = "、".join(t.task_id[:8] for t in tasks[:4])
    more = "…" if len(tasks) > 4 else ""
    return f"【联合复盘】{len(tasks)} 个任务（{shorts}{more}）"


def review_tasks(
    supervisor: Supervisor,
    task_ids: list[str],
    *,
    material: str | None = None,
    distill: bool = True,
    ratchet: bool = False,
    skills_dir: str | None = None,
    work_root: str | Path | None = None,
    use_llm: bool | None = None,
    user_guidance: str | None = None,
    auto_apply: bool = True,
) -> dict[str, Any]:
    """兼容入口：分析；若 distill+auto_apply 则对建议勾选项备份并执行。"""
    del material
    analyzed = analyze_review_tasks(
        supervisor,
        task_ids,
        distill=distill,
        ratchet=ratchet,
        use_llm=use_llm,
        user_guidance=user_guidance,
    )
    if not analyzed.get("ok"):
        return analyzed
    if not distill or not auto_apply:
        return analyzed
    items = list(analyzed.get("items") or [])
    selected = [
        str(it.get("id"))
        for it in items
        if bool(it.get("selected_default", True))
    ]
    if not selected and items:
        selected = [str(it.get("id")) for it in items]
    applied = apply_review_plan(
        supervisor,
        task_ids=list(analyzed.get("task_ids") or task_ids),
        items=items,
        selected_ids=selected,
        skills_dir=skills_dir,
        work_root=work_root,
        dimensions=analyzed.get("dimensions"),
        analysis=str(analyzed.get("analysis") or ""),
        outcome=str(analyzed.get("outcome") or ""),
    )
    merged = dict(analyzed)
    merged.update(applied)
    merged["analysis"] = analyzed.get("analysis")
    merged["dimensions"] = analyzed.get("dimensions")
    merged["items"] = items
    if applied.get("message") and analyzed.get("message"):
        merged["message"] = (
            str(analyzed.get("message") or "").rstrip()
            + "\n\n"
            + str(applied.get("message") or "")
        )
    return merged


def analyze_review_tasks(
    supervisor: Supervisor,
    task_ids: list[str],
    *,
    distill: bool = True,
    ratchet: bool = False,
    use_llm: bool | None = None,
    user_guidance: str | None = None,
) -> dict[str, Any]:
    """只分析：全维结论 + 优化清单；不写库、不改文件。"""
    ids = _normalize_task_ids(task_ids)
    if not ids:
        return {"ok": False, "message": "缺少 task_ids", "task_ids": []}
    if len(ids) > _MAX_JOINT:
        return {
            "ok": False,
            "message": f"一次最多联合 {_MAX_JOINT} 个任务",
            "task_ids": ids,
        }

    tasks: list[TaskRecord] = []
    missing: list[str] = []
    for tid in ids:
        task = supervisor.get_task(tid)
        if task is None:
            missing.append(tid)
        else:
            tasks.append(task)
    if missing:
        return {
            "ok": False,
            "message": f"未知任务: {', '.join(x[:8] for x in missing)}",
            "task_ids": ids,
        }

    joint = len(tasks) > 1
    primary = tasks[0]
    guidance = (user_guidance or "").strip()

    if ratchet:
        return {
            "ok": False,
            "message": "棘轮尚未启用（stub）",
            "detail_fetched": False,
            "ratchet_status": "disabled",
            "task_id": primary.task_id,
            "task_ids": [t.task_id for t in tasks],
            "joint": joint,
            "phase": "failed",
        }

    if not distill:
        summary = build_materials_text(tasks, include_steps=False, user_guidance=guidance)
        return {
            "ok": True,
            "message": "已生成复盘简要素材（未分析、未落盘）",
            "detail_fetched": False,
            "summary": summary[:2000],
            "task_id": primary.task_id,
            "task_ids": [t.task_id for t in tasks],
            "joint": joint,
            "phase": "materials_only",
            "items": [],
            "dimensions": {},
        }

    include_steps = any(_has_step_trace(t) for t in tasks)
    live = use_llm if use_llm is not None else _llm_enabled()
    try:
        plan = _analyze(
            tasks,
            include_steps=include_steps,
            use_llm=live,
            user_guidance=guidance,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"复盘分析失败：{exc}",
            "detail_fetched": include_steps,
            "task_id": primary.task_id,
            "task_ids": [t.task_id for t in tasks],
            "joint": joint,
            "phase": "failed",
        }

    dimensions = _normalize_dimensions(plan.get("dimensions"), tasks=tasks, plan=plan)
    items = _normalize_items(plan.get("items") or _items_from_legacy_plan(plan, tasks))
    analysis = _format_dimensions_analysis(dimensions, fallback=str(plan.get("analysis") or ""))
    outcome = str(plan.get("outcome") or "").strip() or _guess_outcome_multi(tasks)

    message = _format_analysis_message(
        analysis=analysis,
        dimensions=dimensions,
        outcome=outcome,
        items=items,
        detail_fetched=include_steps,
        joint=joint,
        task_ids=[t.task_id for t in tasks],
    )

    for t in tasks:
        supervisor.merge_task_meta(
            t.task_id,
            {
                "last_review_analysis": {
                    "outcome": outcome,
                    "dimensions": dimensions,
                    "item_ids": [it["id"] for it in items],
                    "detail_fetched": include_steps,
                    "joint": joint,
                }
            },
        )

    return {
        "ok": True,
        "message": message,
        "detail_fetched": include_steps,
        "analysis": analysis,
        "dimensions": dimensions,
        "outcome": outcome,
        "items": items,
        "decisions": _kinds_from_items(items),
        "task_id": primary.task_id,
        "task_ids": [t.task_id for t in tasks],
        "joint": joint,
        "phase": "awaiting_apply",
        "skill": None,
        "patches": [],
        "skipped": ["分析阶段不落盘；请勾选优化项后再执行"],
    }


def apply_review_plan(
    supervisor: Supervisor,
    *,
    task_ids: list[str],
    items: list[dict[str, Any]],
    selected_ids: list[str],
    skills_dir: str | None = None,
    work_root: str | Path | None = None,
    releases_dir: str | Path | None = None,
    dimensions: dict[str, Any] | None = None,
    analysis: str = "",
    outcome: str = "",
) -> dict[str, Any]:
    """备份后执行选中优化项。selected 为空则不备份、不改。"""
    ids = _normalize_task_ids(task_ids)
    catalog = _normalize_items(items)
    by_id = {str(it["id"]): it for it in catalog}
    selected = [sid for sid in _normalize_id_list(selected_ids) if sid in by_id]
    joint = len(ids) > 1
    primary = ids[0] if ids else ""

    if not selected:
        msg = "未选中任何优化项，未备份、未落盘。"
        return {
            "ok": True,
            "message": msg,
            "task_id": primary,
            "task_ids": ids,
            "joint": joint,
            "selected_ids": [],
            "backup": {},
            "skill": None,
            "patches": [],
            "skipped": ["无选中项"],
            "decisions": ["none"],
            "phase": "completed",
        }

    chosen = [by_id[sid] for sid in selected]
    patch_paths = _collect_patch_paths(chosen)
    backup = _backup_before_apply(
        supervisor,
        paths=patch_paths,
        work_root=work_root,
        releases_dir=releases_dir,
        message=f"review-backup:{','.join(ids[:3])}",
    )

    root = skills_dir or os.path.join(supervisor.data_dir, "skills")
    store = SkillStore(root)
    skill: dict[str, Any] | None = None
    patches_applied: list[dict[str, Any]] = []
    skipped: list[str] = []
    actions: list[str] = []

    for item in chosen:
        kind = str(item.get("kind") or "")
        if kind not in actions:
            actions.append(kind)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if kind in {"skill", "lesson"}:
            try:
                body = dict(payload)
                if not body.get("id"):
                    body["id"] = _skill_id_from_task_ids(ids, prefix=kind)
                body.setdefault("title", str(item.get("title") or kind))
                body.setdefault("when_to_use", str(item.get("rationale") or "")[:500])
                body.setdefault("steps", str(item.get("rationale") or "见复盘分析"))
                body["source"] = "tasks:" + ",".join(ids)
                tags = body.get("tags") if isinstance(body.get("tags"), list) else []
                tags = [str(t) for t in tags]
                for t in ("review", kind, outcome or ""):
                    if t and t not in tags:
                        tags.append(t)
                if joint and "joint" not in tags:
                    tags.append("joint")
                body["tags"] = tags
                written = store.write(validate_skill_payload(body))
                skill = written
            except SkillValidationError as exc:
                skipped.append(f"#{item.get('id')} Skill 质量门未过：{exc}")
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"#{item.get('id')} Skill 写入失败：{exc}")
        elif kind in _PATCH_KINDS:
            raw_patches = payload.get("patches") or payload.get("code_patches")
            if not raw_patches and (payload.get("path") or payload.get("content") is not None):
                raw_patches = [payload]
            if not isinstance(raw_patches, list) or not raw_patches:
                skipped.append(f"#{item.get('id')} {kind} 缺少 patch 内容")
                continue
            applied, errs = _apply_code_patches(raw_patches, work_root=work_root)
            for a in applied:
                a["item_id"] = item.get("id")
                a["kind"] = kind
            patches_applied.extend(applied)
            skipped.extend(f"#{item.get('id')} {e}" for e in errs)
        else:
            skipped.append(f"#{item.get('id')} 未知 kind={kind}")

    want_skill = bool({"skill", "lesson"} & set(actions))
    want_patch = bool(_PATCH_KINDS & set(actions))
    ok = True
    if want_skill and skill is None and not want_patch:
        ok = False
    if want_patch and not patches_applied and skill is None:
        if any(("禁改" in s or "失败" in s or "缺少" in s) for s in skipped):
            ok = False

    message = _format_apply_message(
        selected_ids=selected,
        skill=skill,
        patches=patches_applied,
        skipped=skipped,
        backup=backup,
        actions=actions,
    )

    review_meta = {
        "outcome": outcome,
        "actions": actions or ["none"],
        "analysis": (analysis or "")[:1500],
        "dimensions": dimensions or {},
        "selected_ids": selected,
        "skill_id": (skill or {}).get("id"),
        "patches": patches_applied,
        "backup": backup,
        "ok": ok,
        "joint": joint,
        "task_ids": ids,
    }
    for tid in ids:
        supervisor.merge_task_meta(tid, {"last_review": review_meta})

    return {
        "ok": ok,
        "message": message,
        "task_id": primary,
        "task_ids": ids,
        "joint": joint,
        "selected_ids": selected,
        "backup": backup,
        "skill": skill,
        "patches": patches_applied,
        "skipped": skipped,
        "decisions": actions or ["none"],
        "outcome": outcome,
        "analysis": analysis,
        "dimensions": dimensions or {},
        "items": catalog,
        "phase": "completed" if ok else "failed",
    }


def build_material_text(task: TaskRecord, *, include_steps: bool) -> str:
    return build_materials_text([task], include_steps=include_steps)


def build_materials_text(
    tasks: list[TaskRecord],
    *,
    include_steps: bool,
    detail_only: set[str] | None = None,
    user_guidance: str = "",
) -> str:
    parts: list[str] = [
        f"任务数: {len(tasks)}",
        "模式: " + ("联合复盘" if len(tasks) > 1 else "单任务复盘"),
        "说明：须覆盖任务规划/执行/调度/工具结果/结果判断五维；"
        "成功与失败都有复盘价值；联合时综合承接链。",
        f"has_any_step_trace: {any(_has_step_trace(t) for t in tasks)}",
    ]
    if (user_guidance or "").strip():
        parts.append("用户附加建议（须优先尊重）：\n" + user_guidance.strip()[:2000])
    any_trace = any(_has_step_trace(t) for t in tasks)
    if include_steps:
        parts.append("已附逐步轨迹（头尾采样+步骤索引；若有）。请据此做全维分析并给出可勾选优化清单。")
    elif any_trace:
        parts.append(
            "本段为指令/简要预览，未附 steps；放行分析时将喂头尾采样轨迹。"
            "请仍可先看 final_thought 与调度计数。"
        )
    else:
        parts.append(
            "当前无逐步轨迹；请仍尽量分维分析，并在调度/执行维注明材料不足。"
        )
    for i, task in enumerate(tasks, start=1):
        meta = task.meta or {}
        block = [
            f"--- task[{i}] ---",
            f"task_id: {task.task_id}",
            f"status: {task.status}",
            f"goal: {task.goal}",
            f"final_thought: {meta.get('final_thought') or ''}",
            f"error: {meta.get('error') or ''}",
            f"ok: {meta.get('ok')}",
            f"replan_count: {meta.get('replan_count', 0)}",
            f"reflect_count: {meta.get('reflect_count', 0)}",
            f"stagnation_count: {meta.get('stagnation_count', 0)}",
            f"has_step_trace: {_has_step_trace(task)}",
        ]
        reflection = meta.get("reflection")
        if reflection:
            block.append(
                "reflection: "
                + json.dumps(reflection, ensure_ascii=False)[:1500]
            )
        want_steps = include_steps and (
            detail_only is None
            or task.task_id in detail_only
            or any(task.task_id.startswith(x) for x in (detail_only or set()))
        )
        if want_steps and _has_step_trace(task):
            steps = meta.get("steps") or []
            block.append("steps_sample:")
            block.append(format_steps_for_review(steps))
        elif include_steps:
            block.append("steps: （无轨迹或未请求该任务详情）")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def format_steps_for_review(
    steps: list[Any] | None,
    *,
    max_chars: int = 12000,
    head_n: int = 4,
    tail_n: int = 6,
) -> str:
    """复盘轨迹采样：步骤索引 + 头尾详情，避免只截前缀丢掉 success 收尾。"""
    if not isinstance(steps, list) or not steps:
        return "[]"
    index_lines = ["steps_index:"]
    for i, step in enumerate(steps):
        plan = (step or {}).get("plan") if isinstance(step, dict) else {}
        if not isinstance(plan, dict):
            plan = {}
        status = str(plan.get("status") or "")
        thought = str(plan.get("thought") or "").replace("\n", " ")[:100]
        kinds: list[str] = []
        for a in plan.get("actions") or []:
            if isinstance(a, dict) and a.get("kind"):
                kinds.append(str(a.get("kind")))
        index_lines.append(
            f"{i}. status={status} actions=[{','.join(kinds[:6])}] thought={thought}"
        )
    index_text = "\n".join(index_lines)[:3500]

    full = json.dumps(steps, ensure_ascii=False, indent=2)
    if len(full) <= max_chars:
        return index_text + "\nsteps_detail:\n" + full

    n = len(steps)
    if n <= head_n + tail_n:
        head, mid, tail = steps, [], []
    else:
        head = steps[:head_n]
        tail = steps[-tail_n:]
        mid = steps[head_n : n - tail_n]
    sample: dict[str, Any] = {
        "note": (
            f"轨迹过长已头尾采样：共 {n} 步，详前 {len(head)} + 后 {len(tail)}；"
            f"中间省略 {len(mid)} 步（见 steps_index）。"
            "结果判断必须核对末步 status/thought 与 final_thought 是否答到 goal。"
        ),
        "head": head,
        "tail": tail,
    }
    detail = json.dumps(sample, ensure_ascii=False, indent=2)
    budget = max(2000, max_chars - len(index_text) - 32)
    if len(detail) > budget:
        detail = detail[:budget] + "\n…(detail truncated)"
    return index_text + "\nsteps_detail:\n" + detail


def _one_line(text: str, limit: int = 100) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _patch_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("patches") or payload.get("code_patches")
    if not raw and (
        payload.get("path")
        or payload.get("content") is not None
        or payload.get("old") is not None
    ):
        raw = [payload]
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def _summarize_item_change(item: dict[str, Any]) -> dict[str, Any]:
    """从 payload 抽出勾选前可读摘要（不回传完整 payload）。"""
    kind = str(item.get("kind") or "")
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    paths: list[str] = []
    preview_lines: list[str] = []
    summary = ""

    if kind in {"skill", "lesson"}:
        sid = str(payload.get("id") or "").strip() or "(未命名)"
        when = str(payload.get("when_to_use") or "").strip()
        steps = str(payload.get("steps") or "").strip()
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        summary = f"将写入知识库 {kind}/{sid}"
        preview_lines.append(summary)
        if when:
            preview_lines.append(f"何时用：{_one_line(when, 200)}")
        if steps:
            step_lines = [ln.strip() for ln in steps.splitlines() if ln.strip()][:5]
            preview_lines.append("步骤：")
            preview_lines.extend(f"  · {_one_line(ln, 140)}" for ln in step_lines)
            if len([ln for ln in steps.splitlines() if ln.strip()]) > 5:
                preview_lines.append("  · …")
        if tags:
            preview_lines.append(
                "标签：" + ",".join(_one_line(str(t), 40) for t in tags[:8])
            )
    elif kind in _PATCH_KINDS:
        entries = _patch_entries(payload)
        for p in entries[:8]:
            path = str(p.get("path") or "").strip() or "(无路径)"
            if path and path not in paths and path != "(无路径)":
                paths.append(path)
            if "old" in p or "new" in p:
                preview_lines.append(f"替换 `{path}`：")
                preview_lines.append(f"  − {_one_line(str(p.get('old') or ''), 120)}")
                preview_lines.append(f"  ＋ {_one_line(str(p.get('new') or ''), 120)}")
            elif "content" in p:
                content = str(p.get("content") or "")
                preview_lines.append(f"整文件写入 `{path}`（约 {len(content)} 字）")
                head = _one_line(content, 140)
                if head:
                    preview_lines.append(f"  开头：{head}")
            else:
                preview_lines.append(f"改 `{path}`（补丁内容未给出，执行时可能跳过）")
        if len(entries) > 8:
            preview_lines.append(f"…另有 {len(entries) - 8} 处补丁未展开")
        if paths:
            shown = "、".join(paths[:4])
            more = f" 等 {len(paths)} 个文件" if len(paths) > 4 else ""
            summary = f"将改文件：{shown}{more}"
        elif entries:
            summary = f"将改 {len(entries)} 处补丁（路径未写清）"
        else:
            summary = "补丁内容缺失，执行时可能跳过"
            preview_lines.append(summary)
    else:
        summary = f"未知类型 {kind or '?'}"
        preview_lines.append(summary)

    impact = str(item.get("impact") or "").strip()
    if not impact:
        if kind in {"skill", "lesson"}:
            impact = (
                f"沉淀可复用{kind}；同类目标命中「何时用」时可被规划引用。"
                "不改运行时代码。"
            )
        elif kind in _PATCH_KINDS:
            layer = {
                "code_patch": "代码",
                "rule_patch": "规则",
                "prompt_patch": "提示词",
            }.get(kind, kind)
            target = "、".join(paths[:3]) if paths else "（路径待核）"
            impact = (
                f"改{layer}层：{target}。"
                "执行前整包备份；验收看同类任务是否不再重复本案机制失败。"
            )

    return {
        "impact": impact[:360],
        "change_summary": summary[:220],
        "preview": "\n".join(preview_lines)[:1800],
        "paths": paths[:12],
    }


def items_for_ui(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """PWA 勾选控件用的轻量清单（含可读摘要，不含完整 payload）。"""
    out: list[dict[str, Any]] = []
    for it in _normalize_items(items):
        summary = _summarize_item_change(it)
        out.append(
            {
                "id": str(it["id"]),
                "kind": str(it.get("kind") or ""),
                "title": str(it.get("title") or "")[:200],
                "rationale": str(it.get("rationale") or "")[:360],
                "impact": summary["impact"],
                "change_summary": summary["change_summary"],
                "preview": summary["preview"],
                "paths": summary["paths"],
                "selected_default": bool(it.get("selected_default", True)),
            }
        )
    return out


def format_optimization_checklist(items: list[dict[str, Any]]) -> str:
    catalog = _normalize_items(items)
    if not catalog:
        return "【优化清单】（空）无建议改动；回复「取消」结束即可。"
    lines = [
        "【优化清单】请勾选后执行（分析阶段尚未改任何东西）：",
        "可用界面勾选，或回复：全部 / 执行 1,3 / 不要 2 / 取消；其它文字视为改写建议并重新分析。",
        "每项含：理由、影响与验收、将改什么（展开详情见界面）。",
        "",
    ]
    for it in catalog:
        mark = "✓建议" if it.get("selected_default", True) else "·可选"
        summary = _summarize_item_change(it)
        lines.append(
            f"{it['id']}. [{it.get('kind')}] {it.get('title')}（{mark}）"
        )
        rationale = str(it.get("rationale") or "").strip()
        if rationale:
            lines.append(f"   理由：{rationale[:360]}")
        impact = str(summary.get("impact") or "").strip()
        if impact:
            lines.append(f"   影响/验收：{impact[:360]}")
        change = str(summary.get("change_summary") or "").strip()
        if change:
            lines.append(f"   将改：{change}")
        preview = str(summary.get("preview") or "").strip()
        if preview:
            p_lines = [pl for pl in preview.splitlines() if pl.strip() and pl.strip() != change]
            for pl in p_lines[:8]:
                lines.append(f"   {pl}")
            if len(p_lines) > 8:
                lines.append("   …（更多见勾选卡「改动详情」）")
    return "\n".join(lines)


def _analyze(
    tasks: list[TaskRecord],
    *,
    include_steps: bool,
    use_llm: bool,
    user_guidance: str = "",
) -> dict[str, Any]:
    if use_llm:
        return _analyze_with_llm(
            tasks,
            include_steps=include_steps,
            user_guidance=user_guidance,
        )
    return _analyze_rule_based(
        tasks, include_steps=include_steps, user_guidance=user_guidance
    )


def _analyze_rule_based(
    tasks: list[TaskRecord],
    *,
    include_steps: bool,
    user_guidance: str = "",
) -> dict[str, Any]:
    """mock：产出五维分析 + 可勾选 items。"""
    del user_guidance
    outcome = _guess_outcome_multi(tasks)
    joint = len(tasks) > 1
    goals = " → ".join((t.goal or "").strip()[:60] for t in tasks)
    short = ",".join(t.task_id[:8] for t in tasks)

    dims: dict[str, str] = {}
    plan_bits: list[str] = []
    exec_bits: list[str] = []
    sched_bits: list[str] = []
    tool_bits: list[str] = []
    judge_bits: list[str] = []

    for t in tasks:
        meta = t.meta or {}
        sid = t.task_id[:8]
        plan_bits.append(f"[{sid}] goal={ (t.goal or '')[:80] } status={t.status}")
        steps = meta.get("steps") if include_steps else None
        if isinstance(steps, list) and steps:
            kinds: list[str] = []
            for step in steps[:8]:
                plan = (step or {}).get("plan") or {}
                for a in plan.get("actions") or []:
                    if isinstance(a, dict) and a.get("kind"):
                        kinds.append(str(a.get("kind")))
                for r in (step or {}).get("results") or []:
                    if isinstance(r, dict):
                        tool_bits.append(
                            f"[{sid}] ok={r.get('ok')} msg={str(r.get('message') or '')[:80]}"
                        )
            exec_bits.append(
                f"[{sid}] steps={len(steps)} actions=[{','.join(kinds[:12])}] "
                f"thought={(meta.get('final_thought') or '')[:80]}"
            )
        else:
            exec_bits.append(
                f"[{sid}] 无逐步轨迹；final_thought={(meta.get('final_thought') or '')[:100]}"
            )
        sched_bits.append(
            f"[{sid}] replan={meta.get('replan_count', 0)} "
            f"reflect={meta.get('reflect_count', 0)} "
            f"stagnation={meta.get('stagnation_count', 0)}"
        )
        judge_bits.append(
            f"[{sid}] ok={meta.get('ok')} error={str(meta.get('error') or '')[:100]} "
            f"thought={str(meta.get('final_thought') or '')[:100]}"
        )

    dims["planning"] = "；".join(plan_bits) or f"目标链：{goals}"
    dims["execution"] = "；".join(exec_bits) or "缺少执行轨迹"
    dims["scheduling"] = "；".join(sched_bits) or "无调度计数"
    dims["tool_results"] = "；".join(tool_bits) or (
        "无逐步工具结果" if not include_steps else "步骤中无 results"
    )
    dims["outcome_judgment"] = "；".join(judge_bits) or f"综合 outcome={outcome}"

    items: list[dict[str, Any]] = []
    try:
        from fawai_avatar.brain.answer_effect import (
            goal_needs_answer_coverage_gate,
            thought_ok_for_answer_coverage,
        )
    except Exception:  # noqa: BLE001
        goal_needs_answer_coverage_gate = None  # type: ignore[assignment]
        thought_ok_for_answer_coverage = None  # type: ignore[assignment]

    answer_misses: list[str] = []
    if goal_needs_answer_coverage_gate and thought_ok_for_answer_coverage:
        for t in tasks:
            thought = str((t.meta or {}).get("final_thought") or "")
            if goal_needs_answer_coverage_gate(t.goal or "") and not thought_ok_for_answer_coverage(
                thought, goal=t.goal or ""
            ):
                answer_misses.append(t.task_id[:8])
                judge_bits.append(
                    f"[{t.task_id[:8]}] 问询类终稿疑似过程元叙述，未覆盖 goal 问点"
                )
        if answer_misses:
            dims["outcome_judgment"] = "；".join(judge_bits) or dims.get(
                "outcome_judgment", ""
            )
            if outcome == "success":
                outcome = "mixed"
            items.append(
                {
                    "id": str(len(items) + 1),
                    "kind": "lesson",
                    "title": "教训：问询终稿须答到目标问点",
                    "rationale": (
                        "结果判断：success 的 thought 禁止只写过程元叙述；"
                        "须含面向用户的可验收事实（状态/数字/是否）。"
                        f"命中短码：{','.join(answer_misses)}"
                    ),
                    "selected_default": True,
                    "payload": {
                        "id": "lesson_answer_must_cover_goal",
                        "title": "问询终稿须答到目标问点",
                        "when_to_use": (
                            "goal 在问状态/进度/是否/多少，或复盘发现终稿只有"
                            "「已获取/已汇总/并结束」时"
                        ),
                        "steps": (
                            "1. 对照 goal 问点列出须交付的事实字段。\n"
                            "2. success 的 thought 直接写事实；禁止过程元叙述收尾。\n"
                            "3. 材料不足则 failed/need_input，说明缺什么。"
                        ),
                        "tags": ["review", "lesson", "answer_coverage", "false_success"],
                    },
                }
            )
            items.append(
                {
                    "id": str(len(items) + 1),
                    "kind": "lesson",
                    "title": "教训：压缩观察须读全文再总结",
                    "rationale": (
                        "问询/盘点类若工具结果已压缩，须先 fs_read observe 落盘再分项作答；"
                        "禁止只凭截断预览交差。"
                        f"命中短码：{','.join(answer_misses)}"
                    ),
                    "selected_default": True,
                    "payload": {
                        "id": "lesson_compressed_dump_must_read",
                        "title": "压缩观察须读全文再总结",
                        "when_to_use": "工具结果已压缩落盘或盘点过薄时",
                        "steps": (
                            "1. fs_read 压缩落盘 path（优先相对路径）。\n"
                            "2. 短结论 + 分项写证据已有维度。\n"
                            "3. 禁止 shell cat 代替 fs_read。"
                        ),
                        "tags": ["review", "lesson", "answer_coverage", "observe"],
                    },
                }
            )

    # 失败文案/反省里出现 shell 绕过 fs → 默认勾选
    shell_bypass_hits: list[str] = []
    for t in tasks:
        meta = t.meta or {}
        blob = " ".join(
            [
                str(meta.get("error") or ""),
                str((meta.get("reflection") or {}).get("thought") or "")
                if isinstance(meta.get("reflection"), dict)
                else "",
                " ".join(
                    str(c)
                    for c in (
                        (meta.get("reflection") or {}).get("constraints") or []
                        if isinstance(meta.get("reflection"), dict)
                        else []
                    )
                ),
            ]
        )
        if any(
            tok in blob
            for tok in (
                "改用 shell",
                "执行 cat",
                "escapes work root",
                "绕过",
            )
        ) and any(tok in blob for tok in ("fs_read", "绝对路径", "沙箱", "cat")):
            shell_bypass_hits.append(t.task_id[:8])
    if shell_bypass_hits:
        items.append(
            {
                "id": str(len(items) + 1),
                "kind": "lesson",
                "title": "教训：禁止 shell 绕过 fs 沙箱",
                "rationale": (
                    "fs 越界后不得教 shell cat 读本地文件；应相对路径 / observe 落盘 / need_input。"
                    f"命中短码：{','.join(shell_bypass_hits)}"
                ),
                "selected_default": True,
                "payload": {
                    "id": "lesson_no_shell_bypass_fs",
                    "title": "禁止 shell 绕过 fs 沙箱",
                    "when_to_use": "fs_read 越界或反省出现 shell cat 绕过时",
                    "steps": (
                        "1. 改 work_root 相对路径或读 data/observe。\n"
                        "2. 仓外文件 need_input/failed。\n"
                        "3. 禁止 shell cat 绕过沙箱。"
                    ),
                    "tags": ["review", "lesson", "reflect", "sandbox"],
                },
            }
        )

    if outcome == "failure" or (
        joint and any(_guess_outcome(t) == "failure" for t in tasks)
    ):
        fail_bits = []
        for t in tasks:
            meta = t.meta or {}
            fail_bits.append(
                f"{t.task_id[:8]}: {meta.get('error') or meta.get('final_thought') or t.status}"
            )
        items.append(
            {
                "id": str(len(items) + 1),
                "kind": "lesson",
                "title": f"教训：{goals[:40] or '失败任务'}",
                "rationale": "结果判断/执行维：存在失败，沉淀教训避免重复无效链",
                "selected_default": True,
                "payload": {
                    "id": _skill_id_from_task_ids(
                        [t.task_id for t in tasks], prefix="lesson"
                    ),
                    "title": f"教训：{goals[:40] or '失败任务'}",
                    "when_to_use": f"当再次遇到类似目标链且曾失败：{goals[:160]}",
                    "steps": (
                        "1. 上次失败点：\n- "
                        + "\n- ".join(fail_bits[:8])
                        + "\n2. 不要重复同一无效操作链；先核对观察/权限/目标是否可结构化完成\n"
                        "3. 若缺能力，应 status=failed 说明边界，禁止假成功"
                    )[:8000],
                    "tags": ["review", "lesson", "failure"]
                    + (["joint"] if joint else []),
                },
            }
        )
    elif all(
        not str((t.meta or {}).get("final_thought") or "").strip()
        and not ((t.meta or {}).get("steps") or [])
        for t in tasks
    ):
        if not items:
            items = []
    elif not answer_misses:
        steps_chunks: list[str] = []
        for t in tasks:
            meta = t.meta or {}
            chunk = _format_steps_for_skill(
                meta.get("steps") if include_steps else None,
                str(meta.get("final_thought") or ""),
            )
            steps_chunks.append(f"[{t.task_id[:8]}] {chunk}")
        items.append(
            {
                "id": str(len(items) + 1),
                "kind": "skill",
                "title": f"经验：{goals[:40] or '成功任务'}",
                "rationale": f"规划/执行维：可复用做法（{short}）",
                "selected_default": True,
                "payload": {
                    "id": _skill_id_from_task_ids(
                        [t.task_id for t in tasks], prefix="skill"
                    ),
                    "title": f"经验：{goals[:40] or '成功任务'}",
                    "when_to_use": f"当目标类似：{goals[:160]}",
                    "steps": "\n".join(steps_chunks)[:8000],
                    "tags": ["review", "skill", "success"]
                    + (["joint"] if joint else []),
                },
            }
        )

    # 统一编号，避免前置 answer lesson 与后续项撞号
    for idx, it in enumerate(items, start=1):
        it["id"] = str(idx)

    analysis = "\n".join(
        f"{_DIMENSION_LABELS[k]}：{dims[k]}" for k in _DIMENSION_KEYS
    )
    return {
        "analysis": analysis,
        "outcome": outcome,
        "dimensions": dims,
        "items": items,
        "actions": _kinds_from_items(items) or ["none"],
        "skill": (items[0].get("payload") if items else None),
        "code_patches": [],
    }


def _analyze_with_llm(
    tasks: list[TaskRecord],
    *,
    include_steps: bool,
    user_guidance: str = "",
) -> dict[str, Any]:
    from fawai_avatar.llm.client import LlmClient

    context = build_materials_text(
        tasks,
        include_steps=include_steps,
        user_guidance=user_guidance,
    )
    mode = "联合复盘" if len(tasks) > 1 else "单任务复盘"
    prompt = f"""你是操作分身的复盘分析官。模式：{mode}。
材料阶段：{"含逐步详情" if include_steps else "无逐步轨迹（仍须尽量分维）"}。
成功与失败都有价值。禁止一律写成成功 Skill。禁止在本阶段声称已改代码或已写库。
若材料含「用户附加建议」，决策须优先尊重。
联合复盘时：把多任务当作同一条承接/重试链综合分析，优先沉淀一条可复用经验或教训；各维须落到任务短码。

【任务分析强制口径】（与 doc/功能/任务复盘/分析规则.md 一致）
- 智能体机制优先：须指出规划/观察/动作/反省/步骤重复/结果判定哪环失效；禁止只复述终态错误句。
- 优化须针对通用任务（可迁移到同类目标）；单任务细节只作证据，不得当唯一优化标题。
- 区分表面失败与机制失败（例：fingerprint / 探索闸门 abort 可能只是止损，根因常是从未 mutate、inject 未收紧工具面、或 alternate/架构门误伤改写）。
- analysis 总述建议含：机制根因（可复用模式名）→ 证据要点 → 通用优化方向。
- 最终答复用户（analysis / dimensions / items 的 title 与 rationale）：须同时保证全面性、准确性、专业性、易读性——覆盖命中机制与有材料的五维；断言可落到证据，不确定则写材料不足；用机制术语写清改哪一层；短段先结论后证据，禁止堆内部字段名当主文。
- 问询/状态类假成功（强制）：若 goal 在问状态/进度/是否/多少/配置落点/硬件配置，而 final_thought 或末步 thought 只写「已获取/已汇总/已找到并结束」等过程元叙述、或硬件盘点只报一两项缺来源路径 → 须把「假成功 / 答复未覆盖目标」或「配置盘点过薄」升为机制根因之一，并至少给一条默认勾选优化（lesson 或 prompt_patch/code_patch：答复覆盖闸门）；禁止只写在 outcome_judgment 末句却清单不跟进。轨迹材料含 steps_index 与头尾采样时，必须核对末步，禁止只根据前缀探索噪声定主因。

必须覆盖以下五维（dimensions 每键一两段，禁止空话；须落到机制而非流水账）：
- planning 任务规划：goal 拆解、能力选型、探索 vs 改写相位是否错误、是否误走 GUI/超清单
- execution 任务执行：是否长期无 mutate；逐步 actions 是否空转式只读；闸门 force_mutate 后是否仍只读
- scheduling 调度过程：replan/reflect/stagnation 是纠偏还是注入错误世界模型/矛盾约束；架构二次失败是否硬掐死写入
- tool_results 工具使用结果：ok 的只读是否被误当成进展；写动作是否缺失；mutate_prefer 工具面是否生效
- outcome_judgment 结果判断：终态文案是否掩盖机制空洞；假成功/边界未明示；问询类是否答到 goal 问点

然后给出可勾选优化清单 items（可空）。每项须让用户**不看代码也能判断是否执行**：
- id: 从 "1" 起的字符串编号
- kind: skill | lesson | code_patch | rule_patch | prompt_patch
- title / rationale（对应哪一维；title 写通用机制，勿绑死单一 goal 文案；文案过「最终答复用户」四性）
- impact: 必填，一两句——改哪一层（规划/观察/闸门/提示词/知识库等）、执行后会怎样、如何验收（类任务可观察指标）
- selected_default: bool（是否建议默认勾选）
- payload: 必须可执行且具体（禁止空壳）；用户勾选前会看到由此生成的路径/步骤/补丁摘要
  - skill/lesson: {{id,title,when_to_use,steps,tags}}（steps 写清可执行步骤，非口号）
  - *_patch: {{path,content}} 或 {{path,old,new}} 或 {{patches:[...]}}（path 与 old/new 或 content 须齐全；old 须能对上仓库现文）

禁改（仅智能体）：versioning/**、runtime/{{releases,keeper,code_watch,reload_gate,port_guard}}.py、workspace/paths.py、已存在的 doc/版本/v*/**。

只输出一个 JSON：
{{
  "analysis": "给人看的总述（全面准确、专业易读：机制根因+证据要点+通用优化方向）",
  "outcome": "success|failure|mixed",
  "dimensions": {{
    "planning": "...",
    "execution": "...",
    "scheduling": "...",
    "tool_results": "...",
    "outcome_judgment": "..."
  }},
  "items": []
}}

{context}
"""
    result = LlmClient().chat([{"role": "user", "content": prompt}], tools=None)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "LLM 调用失败"))
    content = str(result.get("content") or "").strip()
    payload = _parse_json_object(content)
    if payload is None:
        raise RuntimeError("无法解析 LLM JSON")
    return payload


def _backup_before_apply(
    supervisor: Supervisor,
    *,
    paths: list[str],
    work_root: str | Path | None,
    releases_dir: str | Path | None,
    message: str,
) -> dict[str, Any]:
    from fawai_avatar.config import RuntimeConfig
    from fawai_avatar.control.background import BackgroundTools
    from fawai_avatar.runtime.releases import ReleaseStore, releases_from_env
    from fawai_avatar.types import Action

    out: dict[str, Any] = {"release_id": None, "version_id": None, "paths": list(paths)}
    try:
        store = (
            ReleaseStore(releases_dir)
            if releases_dir is not None
            else releases_from_env(supervisor.data_dir)
        )
        out["release_id"] = store.snapshot(message=message, label="review-backup")
    except Exception as exc:  # noqa: BLE001
        out["release_error"] = str(exc)

    if paths:
        try:
            cfg = RuntimeConfig.from_env()
            root = Path(work_root or cfg.work_root)
            data = Path(supervisor.data_dir or cfg.data_dir)
            tools = BackgroundTools(
                work_root=root, data_dir=data, auto_version=False
            )
            r = tools.execute(
                Action(
                    kind="version_snapshot",
                    args={"paths": paths, "message": message},
                )
            )
            if r.ok:
                out["version_id"] = (r.data or {}).get("version_id")
            else:
                out["version_error"] = r.message
        except Exception as exc:  # noqa: BLE001
            out["version_error"] = str(exc)
    return out


def _collect_patch_paths(items: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in items:
        if str(item.get("kind") or "") not in _PATCH_KINDS:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        raw = payload.get("patches") or payload.get("code_patches")
        candidates: list[Any] = []
        if isinstance(raw, list):
            candidates.extend(raw)
        elif payload.get("path"):
            candidates.append(payload)
        for c in candidates:
            if isinstance(c, dict):
                p = str(c.get("path") or "").strip()
                if p and p not in paths:
                    paths.append(p)
    return paths


def _has_step_trace(task: TaskRecord) -> bool:
    steps = (task.meta or {}).get("steps")
    return isinstance(steps, list) and len(steps) > 0


def _apply_code_patches(
    patches: list[Any],
    *,
    work_root: str | Path | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    from fawai_avatar.config import RuntimeConfig
    from fawai_avatar.control.background import BackgroundTools
    from fawai_avatar.types import Action

    cfg = RuntimeConfig.from_env()
    root = work_root or cfg.work_root
    tools = BackgroundTools(work_root=root, data_dir=cfg.data_dir, auto_version=True)
    applied: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in patches[:8]:
        if not isinstance(item, dict):
            errors.append("忽略非对象 patch")
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            errors.append("patch 缺 path")
            continue
        if "content" in item:
            r = tools.execute(
                Action(kind="fs_write", args={"path": path, "content": str(item.get("content"))})
            )
            if r.ok:
                applied.append(
                    {
                        "path": path,
                        "kind": "write",
                        "version_id": (r.data or {}).get("version_id"),
                    }
                )
            else:
                errors.append(f"{path}: {r.message}")
            continue
        if item.get("old") is not None and item.get("new") is not None:
            r = tools.execute(
                Action(
                    kind="fs_replace",
                    args={
                        "path": path,
                        "old": str(item.get("old")),
                        "new": str(item.get("new")),
                    },
                )
            )
            if r.ok:
                applied.append(
                    {
                        "path": path,
                        "kind": "replace",
                        "version_id": (r.data or {}).get("version_id"),
                    }
                )
            else:
                errors.append(f"{path}: {r.message}")
            continue
        errors.append(f"{path}: 需要 content 或 old/new")
    return applied, errors


def _format_analysis_message(
    *,
    analysis: str,
    dimensions: dict[str, str],
    outcome: str,
    items: list[dict[str, Any]],
    detail_fetched: bool,
    joint: bool,
    task_ids: list[str],
) -> str:
    parts: list[str] = []
    if analysis:
        parts.append("【分析】\n" + analysis.strip())
    elif dimensions:
        parts.append(
            "【分析】\n"
            + "\n".join(
                f"{_DIMENSION_LABELS.get(k, k)}：{dimensions.get(k) or '（空）'}"
                for k in _DIMENSION_KEYS
            )
        )
    detail = "已用逐步轨迹" if detail_fetched else "无逐步轨迹（仅 meta 摘要）"
    mode = "联合复盘" if joint else "单任务复盘"
    shorts = ",".join(x[:8] for x in task_ids)
    parts.append(
        f"【结论】{mode}（{len(task_ids)}）[{shorts}]；"
        f"outcome={outcome}；{detail}；本阶段未落盘"
    )
    parts.append(format_optimization_checklist(items))
    return "\n\n".join(parts)


def _format_apply_message(
    *,
    selected_ids: list[str],
    skill: dict[str, Any] | None,
    patches: list[dict[str, Any]],
    skipped: list[str],
    backup: dict[str, Any],
    actions: list[str],
) -> str:
    parts: list[str] = [
        f"【执行】已选 #{','.join(selected_ids) or '无'}；动作={','.join(actions) or 'none'}"
    ]
    rid = backup.get("release_id")
    vid = backup.get("version_id")
    parts.append(
        f"【备份】release={rid or backup.get('release_error') or '无'}；"
        f"version={vid or backup.get('version_error') or ('无路径快照' if not backup.get('paths') else '失败')}"
    )
    if skill:
        parts.append(
            "【已写入】"
            f"{skill.get('id')}｜{skill.get('title')}\n"
            f"何时用：{skill.get('when_to_use')}\n"
            f"步骤摘要：{str(skill.get('steps') or '')[:400]}"
        )
    if patches:
        paths = ", ".join(str(p.get("path")) for p in patches)
        parts.append(f"【已改代码/规则/提示词】{paths}")
    if skipped:
        parts.append("【未执行/跳过】\n- " + "\n- ".join(skipped[:8]))
    return "\n\n".join(parts)


def _format_dimensions_analysis(
    dimensions: dict[str, str], *, fallback: str = ""
) -> str:
    lines = [
        f"{_DIMENSION_LABELS[k]}：{dimensions.get(k) or '（未覆盖）'}"
        for k in _DIMENSION_KEYS
    ]
    body = "\n".join(lines)
    fb = (fallback or "").strip()
    if fb and fb not in body:
        return fb + "\n\n" + body
    return body


def _normalize_dimensions(
    raw: Any, *, tasks: list[TaskRecord], plan: dict[str, Any]
) -> dict[str, str]:
    out: dict[str, str] = {}
    src = raw if isinstance(raw, dict) else {}
    for key in _DIMENSION_KEYS:
        val = str(src.get(key) or "").strip()
        out[key] = val
    if not any(out.values()):
        # 回退：用总述填各维占位，避免空维
        fb = str(plan.get("analysis") or "").strip() or _guess_outcome_multi(tasks)
        for key in _DIMENSION_KEYS:
            out[key] = f"（材料不足，据总述）{fb[:200]}"
    else:
        for key in _DIMENSION_KEYS:
            if not out[key]:
                out[key] = "（本维未写清，需结合轨迹复核）"
    return out


def _normalize_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in _VALID_KINDS:
            continue
        iid = str(item.get("id") or i).strip() or str(i)
        payload = item.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            # 兼容顶层 skill / patches
            if kind in {"skill", "lesson"} and isinstance(item.get("skill"), dict):
                payload = dict(item["skill"])
            for pk in ("path", "content", "old", "new", "patches", "code_patches"):
                if pk in item and pk not in payload:
                    payload[pk] = item[pk]
        out.append(
            {
                "id": iid,
                "kind": kind,
                "title": str(item.get("title") or kind)[:200],
                "rationale": str(item.get("rationale") or "")[:500],
                "impact": str(item.get("impact") or "")[:400],
                "selected_default": bool(item.get("selected_default", True)),
                "payload": payload,
            }
        )
    # 规范编号为 1..n 字符串，保留原 id 若已是数字序列
    renumbered: list[dict[str, Any]] = []
    for i, it in enumerate(out, start=1):
        nid = str(i)
        row = dict(it)
        row["id"] = nid
        renumbered.append(row)
    return renumbered


def _items_from_legacy_plan(
    plan: dict[str, Any], tasks: list[TaskRecord]
) -> list[dict[str, Any]]:
    """兼容旧 actions/skill/code_patches 字段。"""
    actions = plan.get("actions") or []
    if isinstance(actions, str):
        actions = [actions]
    if not isinstance(actions, list):
        actions = []
    kinds = [str(a).strip().lower() for a in actions]
    items: list[dict[str, Any]] = []
    n = 1
    if "lesson" in kinds or "skill" in kinds:
        kind = "lesson" if "lesson" in kinds else "skill"
        skill = plan.get("skill") if isinstance(plan.get("skill"), dict) else {}
        items.append(
            {
                "id": str(n),
                "kind": kind,
                "title": str(skill.get("title") or kind),
                "rationale": "由旧 actions 字段推导",
                "selected_default": True,
                "payload": skill or {},
            }
        )
        n += 1
    if "code_patch" in kinds:
        patches = plan.get("code_patches") or plan.get("patches") or []
        items.append(
            {
                "id": str(n),
                "kind": "code_patch",
                "title": "代码修改",
                "rationale": "由旧 code_patches 推导",
                "selected_default": True,
                "payload": {"patches": patches if isinstance(patches, list) else []},
            }
        )
    if not items and not kinds:
        return []
    if not items and "none" in kinds:
        return []
    del tasks
    return items


def _kinds_from_items(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for it in items:
        k = str(it.get("kind") or "")
        if k in _VALID_KINDS and k not in out:
            out.append(k)
    return out or ["none"]


def _normalize_task_ids(task_ids: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in task_ids or []:
        tid = str(item or "").strip()
        if tid and tid not in out:
            out.append(tid)
    return out


def _normalize_id_list(ids: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in ids or []:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _parse_id_list(raw: str, all_ids: list[str]) -> list[str]:
    tokens = _ID_TOKEN_RE.findall(raw or "")
    wanted: list[str] = []
    for tok in tokens:
        if tok in all_ids and tok not in wanted:
            wanted.append(tok)
    return wanted


def _guess_outcome(task: TaskRecord) -> str:
    meta = task.meta or {}
    if task.status == "failed" or meta.get("ok") is False:
        return "failure"
    if task.status == "completed" or meta.get("ok") is True:
        return "success"
    return "mixed"


def _guess_outcome_multi(tasks: list[TaskRecord]) -> str:
    outs = {_guess_outcome(t) for t in tasks}
    if outs == {"success"}:
        return "success"
    if outs == {"failure"}:
        return "failure"
    if "failure" in outs and "success" in outs:
        return "mixed"
    if "failure" in outs:
        return "failure"
    if "success" in outs:
        return "success"
    return "mixed"


def _format_steps_for_skill(steps: Any, thought: str) -> str:
    lines: list[str] = []
    if isinstance(steps, list) and steps:
        for i, step in enumerate(steps[:12], start=1):
            plan = (step or {}).get("plan") or {}
            actions = plan.get("actions") or []
            kinds = ",".join(
                str(a.get("kind") or "") for a in actions if isinstance(a, dict)
            )
            lines.append(
                f"{i}. status={plan.get('status')} actions=[{kinds}] "
                f"thought={(plan.get('thought') or '')[:120]}"
            )
    if thought:
        lines.append(f"结论：{thought[:400]}")
    if not lines:
        lines.append("（无逐步轨迹）核对目标后选用结构化工具完成，失败则明示边界")
    return "\n".join(lines)[:8000]


def _skill_id_from_task_ids(task_ids: list[str], *, prefix: str = "review") -> str:
    pref = re.sub(r"[^a-z0-9]", "", (prefix or "review").lower())[:12] or "review"
    if len(task_ids) == 1:
        short = re.sub(r"[^a-z0-9]", "", (task_ids[0] or "").lower())[:10] or "task"
        return f"{pref}_{short}"
    first = re.sub(r"[^a-z0-9]", "", (task_ids[0] or "").lower())[:6] or "t"
    return f"{pref}_j{len(task_ids)}_{first}"


def _llm_enabled() -> bool:
    if os.environ.get("FAWAI_BRAIN_MOCK", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.environ.get("FAWAI_REVIEW_LIVE", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    return True


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
