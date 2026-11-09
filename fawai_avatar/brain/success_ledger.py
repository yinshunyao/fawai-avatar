# term:成功价值台账  tool:success_ledger_upsert|success_ledger_search|success_ledger_get  doc:doc/功能/成功价值台账/说明.md
"""本任务成功步骤价值台账：落盘、检索；三维过门后实写 lore。"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from fawai_avatar.brain.ledger_gate import (
    GateNotify,
    decide_gate_from_scores,
    gate_prompt_suffix,
    ledger_gate_threshold_pct,
    parse_gate_json,
)

LEDGER_FILENAME = "success_ledger.jsonl"
_GATE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="success_ledger_gate")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

_ENTRY_FIELDS = (
    "acceptance",
    "goal_slice",
    "commands",
    "config_refs",
    "code_refs",
    "targets",
    "evidence",
    "side_effects",
    "source_refs",
    "tool_id",
    "avoid",
    "constraints",
    "artifacts",
)


def _task_lock(task_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(task_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[task_id] = lock
        return lock


def ledger_path(data_dir: str | Path, task_id: str, *, create: bool = True) -> Path:
    from fawai_avatar.workspace.py_session import session_root

    root = session_root(data_dir, task_id, create=create)
    return root / LEDGER_FILENAME


def _new_id() -> str:
    return f"sl_{uuid.uuid4().hex[:16]}"


def _as_list(val: Any) -> list[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, tuple):
        return list(val)
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return [val]


def normalize_entry(
    raw: dict[str, Any],
    *,
    task_id: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(existing or {})
    eid = str(raw.get("id") or base.get("id") or "").strip() or _new_id()
    out: dict[str, Any] = {
        "id": eid,
        "task_id": str(task_id),
        "created_at": float(base.get("created_at") or time.time()),
        "updated_at": time.time(),
    }
    if raw.get("step") is not None or base.get("step") is not None:
        try:
            out["step"] = int(raw.get("step", base.get("step")))
        except (TypeError, ValueError):
            pass
    for key in _ENTRY_FIELDS:
        if key in raw:
            val = raw[key]
        elif key in base:
            val = base[key]
        else:
            continue
        if key in {
            "commands",
            "config_refs",
            "code_refs",
            "targets",
            "side_effects",
            "source_refs",
            "constraints",
            "artifacts",
        }:
            out[key] = _as_list(val)
        elif key in {
            "acceptance",
            "goal_slice",
            "evidence",
            "tool_id",
            "avoid",
        }:
            from fawai_avatar.brain.ledger_title import as_text_field

            # 布尔 True/False 不得当验收文案（否则 lore 标题会变成 "True"）
            out[key] = as_text_field(val)
        else:
            out[key] = val
    # 保留门控 / lore 状态（除非调用方显式覆盖）
    if "gate" in raw and isinstance(raw.get("gate"), dict):
        out["gate"] = dict(raw["gate"])
    elif isinstance(base.get("gate"), dict):
        out["gate"] = dict(base["gate"])
    else:
        out["gate"] = {"status": "pending"}
    if "lore_id" in raw:
        out["lore_id"] = raw.get("lore_id")
    elif "lore_id" in base:
        out["lore_id"] = base.get("lore_id")
    if "promoted_at" in raw:
        out["promoted_at"] = raw.get("promoted_at")
    elif "promoted_at" in base:
        out["promoted_at"] = base.get("promoted_at")
    return out


def _read_all(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("id"):
            rows.append(obj)
    return rows


def _write_all(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    if body:
        body += "\n"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def list_entries(data_dir: str | Path, task_id: str) -> list[dict[str, Any]]:
    path = ledger_path(data_dir, task_id, create=False)
    with _task_lock(task_id):
        return _read_all(path)


def get_entry(data_dir: str | Path, task_id: str, entry_id: str) -> dict[str, Any] | None:
    eid = (entry_id or "").strip()
    if not eid:
        return None
    for row in list_entries(data_dir, task_id):
        if str(row.get("id")) == eid:
            return row
    return None


def upsert_entry(
    data_dir: str | Path,
    task_id: str,
    payload: dict[str, Any],
    *,
    schedule_gate: bool = True,
    notify: GateNotify | None = None,
) -> dict[str, Any]:
    tid = (task_id or "").strip()
    if not tid:
        raise ValueError("task_id required")
    path = ledger_path(data_dir, tid, create=True)
    with _task_lock(tid):
        rows = _read_all(path)
        want = str(payload.get("id") or "").strip()
        existing = None
        idx = -1
        if want:
            for i, row in enumerate(rows):
                if str(row.get("id")) == want:
                    existing = row
                    idx = i
                    break
        entry = normalize_entry(payload, task_id=tid, existing=existing)
        # 新写入或内容变更时重新排队门控（已晋升的不强制重评，除非显式 evaluate）
        force_eval = payload.get("evaluate") is True or bool(payload.get("force_evaluate"))
        skip_eval = payload.get("evaluate") is False
        already = str((entry.get("gate") or {}).get("status") or "") == "passed" and entry.get(
            "lore_id"
        )
        if force_eval or (not already and not skip_eval):
            entry["gate"] = {"status": "pending"}
            entry.pop("lore_id", None)
            entry.pop("promoted_at", None)
        elif skip_eval and not already:
            entry["gate"] = {"status": "skipped"}
        if idx >= 0:
            rows[idx] = entry
        else:
            rows.append(entry)
        _write_all(path, rows)
    if schedule_gate and str((entry.get("gate") or {}).get("status") or "") == "pending":
        schedule_gate_eval(data_dir, tid, str(entry["id"]), notify=notify)
    return entry


def _tokenize(query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    parts = re.split(r"[\s/\\|,+，、；;]+", q)
    tokens = [p for p in parts if len(p) >= 2]
    if not tokens and q:
        tokens = [q]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:16]


def _entry_blob(entry: dict[str, Any]) -> str:
    return json.dumps(entry, ensure_ascii=False).lower()


def search_entries(
    data_dir: str | Path,
    task_id: str,
    query: str,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 8), 30))
    tokens = _tokenize(query)
    rows = list_entries(data_dir, task_id)
    if not tokens:
        hits = rows[-limit:]
        hits.reverse()
        return {
            "ok": True,
            "query": query,
            "hits": [_hit_view(r) for r in hits],
            "count": len(hits),
            "source": "ledger",
        }
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        blob = _entry_blob(row)
        score = sum(1 for t in tokens if t in blob)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda x: (-x[0], -float(x[1].get("updated_at") or 0)))
    hits = [_hit_view(r) for _, r in scored[:limit]]
    return {
        "ok": True,
        "query": query,
        "hits": hits,
        "count": len(hits),
        "source": "ledger",
    }


def _hit_view(row: dict[str, Any]) -> dict[str, Any]:
    from fawai_avatar.brain.ledger_title import summarize_success_lore_title

    gate = row.get("gate") if isinstance(row.get("gate"), dict) else {}
    summary = summarize_success_lore_title(row, eid=str(row.get("id") or ""))
    return {
        "id": row.get("id"),
        "summary": summary,
        "acceptance": row.get("acceptance") or row.get("goal_slice"),
        "tool_id": row.get("tool_id"),
        "gate_status": gate.get("status"),
        "lore_id": row.get("lore_id"),
        "step": row.get("step"),
        "updated_at": row.get("updated_at"),
    }


def _redact(text: str) -> str:
    try:
        from fawai_avatar.channel.redact import redact_secrets

        return redact_secrets(text)
    except Exception:
        return text


def entry_to_lore_note(entry: dict[str, Any]) -> dict[str, Any]:
    """过门后写入 lore 的笔记载荷（脱敏）。"""
    from fawai_avatar.brain.ledger_title import (
        as_text_field,
        summarize_success_lore_title,
    )

    eid = str(entry.get("id") or _new_id())
    title = summarize_success_lore_title(entry, eid=eid)[:120]
    accept = as_text_field(entry.get("acceptance")) or as_text_field(
        entry.get("goal_slice")
    )
    lines = [
        f"# {title}",
        "",
        f"- source_task: `{entry.get('task_id')}`",
        f"- ledger_id: `{eid}`",
        f"- tool_id: `{entry.get('tool_id') or ''}`",
        "",
        "## acceptance",
        accept,
        "",
        "## commands",
        json.dumps(entry.get("commands") or [], ensure_ascii=False, indent=2),
        "",
        "## config_refs",
        json.dumps(entry.get("config_refs") or [], ensure_ascii=False, indent=2),
        "",
        "## code_refs",
        json.dumps(entry.get("code_refs") or [], ensure_ascii=False, indent=2),
        "",
        "## targets",
        json.dumps(entry.get("targets") or [], ensure_ascii=False, indent=2),
        "",
        "## evidence",
        as_text_field(entry.get("evidence")),
        "",
        "## side_effects",
        json.dumps(entry.get("side_effects") or [], ensure_ascii=False, indent=2),
        "",
        "## source_refs",
        json.dumps(entry.get("source_refs") or [], ensure_ascii=False, indent=2),
        "",
        "## avoid",
        as_text_field(entry.get("avoid")),
        "",
        "## constraints",
        json.dumps(entry.get("constraints") or [], ensure_ascii=False, indent=2),
        "",
        "## artifacts",
        json.dumps(entry.get("artifacts") or [], ensure_ascii=False, indent=2),
        "",
        "## gate",
        json.dumps(entry.get("gate") or {}, ensure_ascii=False, indent=2),
    ]
    body = _redact("\n".join(lines))
    tags = ["success_ledger", "imitate"]
    tid = str(entry.get("tool_id") or "").strip()
    if tid:
        tags.append(tid[:40])
    return {
        "id": f"success_{eid}",
        "title": _redact(title),
        "body": body,
        "tags": tags,
        "source": f"success_ledger:{entry.get('task_id')}:{eid}",
        "enabled": True,
    }


def write_lore_note(note: dict[str, Any], *, data_dir: str | Path | None = None) -> dict[str, Any]:
    from fawai_avatar.config import resolve_lore_dir
    from fawai_lore import open_lore

    root = resolve_lore_dir()
    lore = open_lore(root)
    saved = lore.notes.upsert(dict(note))
    try:
        from fawai_avatar.lore_embed import index_note_text

        index_note_text(saved.id, f"{saved.title}\n{saved.body}")
    except Exception:
        pass
    try:
        from fawai_avatar.lore_api import sync_lore_fts_entry

        sync_lore_fts_entry(
            kind="note",
            id=saved.id,
            title=saved.title,
            body=saved.body,
            tags=saved.tags,
            related_keys=saved.related_keys,
            source=saved.source,
            enabled=saved.enabled,
        )
    except Exception:
        pass
    return saved.to_dict()


def evaluate_gate_with_llm(
    entry: dict[str, Any],
    *,
    on_delta: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """大模型评三维分数；与记忆配置阈值比较后决定是否晋升。"""
    from fawai_avatar.llm.client import LlmClient

    thr = ledger_gate_threshold_pct()
    card = {
        k: entry.get(k)
        for k in (
            "id",
            "acceptance",
            "goal_slice",
            "commands",
            "config_refs",
            "code_refs",
            "targets",
            "evidence",
            "side_effects",
            "source_refs",
            "tool_id",
            "avoid",
            "constraints",
            "artifacts",
        )
        if entry.get(k) not in (None, "", [])
    }
    prompt = (
        gate_prompt_suffix(threshold_pct=thr, polarity="imitate")
        + f"条目 JSON：\n{json.dumps(card, ensure_ascii=False)}"
    )
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
            "status": "failed",
            "pass": False,
            "error": str(result.get("error") or "llm failed"),
            "elapsed_ms": elapsed_ms,
            "usage": usage or None,
            "model": model,
            "threshold_pct": thr,
        }
    parsed = parse_gate_json(str(result.get("content") or ""))
    if not parsed:
        return {
            "status": "failed",
            "pass": False,
            "error": "无法解析门控 JSON",
            "raw": str(result.get("content") or "")[:500],
            "elapsed_ms": elapsed_ms,
            "usage": usage or None,
            "model": model,
            "threshold_pct": thr,
        }
    decided = decide_gate_from_scores(parsed, threshold_pct=thr)
    gate = {
        **decided,
        "elapsed_ms": elapsed_ms,
        "usage": usage or None,
        "model": model,
    }
    return gate


def apply_gate_result(
    data_dir: str | Path,
    task_id: str,
    entry_id: str,
    gate: dict[str, Any],
) -> dict[str, Any]:
    """写入门控结果；pass 则实写 lore。"""
    tid = (task_id or "").strip()
    eid = (entry_id or "").strip()
    path = ledger_path(data_dir, tid, create=True)
    with _task_lock(tid):
        rows = _read_all(path)
        idx = next((i for i, r in enumerate(rows) if str(r.get("id")) == eid), -1)
        if idx < 0:
            return {"ok": False, "error": "entry not found"}
        entry = dict(rows[idx])
        entry["gate"] = dict(gate)
        entry["updated_at"] = time.time()
        lore_note = None
        if gate.get("pass"):
            note_payload = entry_to_lore_note(entry)
            try:
                lore_note = write_lore_note(note_payload, data_dir=data_dir)
                entry["lore_id"] = lore_note.get("id")
                entry["promoted_at"] = time.time()
                entry["gate"]["status"] = "passed"
            except Exception as exc:
                entry["gate"]["status"] = "failed"
                entry["gate"]["pass"] = False
                entry["gate"]["error"] = f"lore write failed: {exc}"
        rows[idx] = entry
        _write_all(path, rows)
    return {"ok": True, "entry": entry, "lore": lore_note}


def _notify_progress(
    notify: GateNotify | None,
    text: str,
    *,
    status: str,
    entry_id: str,
    **extra: Any,
) -> None:
    if notify is None or notify.on_progress is None:
        return
    phase = (notify.phase if notify else None) or "success_ledger_gate"
    meta: dict[str, Any] = {
        "phase": phase,
        "status": status,
        "entry_id": entry_id,
        "ledger_id": entry_id,
    }
    if notify.step is not None:
        meta["step"] = int(notify.step)
    if notify.run_id:
        meta["run_id"] = notify.run_id
    for k, v in extra.items():
        if v is not None:
            meta[k] = v
    try:
        notify.on_progress(text, meta)
    except Exception:
        pass


def _notify_usage(notify: GateNotify | None, gate: dict[str, Any]) -> None:
    if notify is None or notify.on_usage is None:
        return
    usage = gate.get("usage") if isinstance(gate.get("usage"), dict) else None
    try:
        elapsed = int(gate.get("elapsed_ms") or 0)
    except (TypeError, ValueError):
        elapsed = 0
    try:
        notify.on_usage(usage, elapsed)
    except Exception:
        pass


def _gate_finish_text(gate: dict[str, Any], applied: dict[str, Any]) -> str:
    status = str(gate.get("status") or "")
    rationale = str(gate.get("rationale") or gate.get("error") or "").strip()
    lore = None
    entry = applied.get("entry") if isinstance(applied, dict) else None
    if isinstance(entry, dict):
        lore = entry.get("lore_id")
    if status == "passed" and lore:
        head = f"成功价值台账：已晋升 lore（{lore}）"
    elif status == "passed":
        head = "成功价值台账：过门（lore 写入待确认）"
    elif status == "rejected":
        head = "成功价值台账：未过门，仅留本任务"
    else:
        head = "成功价值台账：评估失败"
    if rationale:
        return f"{head} — {rationale[:160]}"
    return head


def _gate_worker(
    data_dir: str,
    task_id: str,
    entry_id: str,
    notify: GateNotify | None,
) -> None:
    try:
        entry = get_entry(data_dir, task_id, entry_id)
        if not entry:
            return
        accept = str(entry.get("acceptance") or entry.get("goal_slice") or entry_id)[:80]
        _notify_progress(
            notify,
            f"成功价值台账：三维评估中… {accept}",
            status="running",
            entry_id=entry_id,
        )
        live = None
        on_delta = None
        if notify is not None and notify.on_progress is not None:
            from fawai_avatar.runtime.llm_live import LlmLiveProgress

            def _emit(text: str, *, phase: str, step: int = 0, **extra: Any) -> None:
                meta = {"phase": phase, "status": "running", "entry_id": entry_id}
                if step:
                    meta["step"] = step
                if notify.run_id:
                    meta["run_id"] = notify.run_id
                meta.update(extra)
                try:
                    notify.on_progress(text, meta)
                except Exception:
                    pass

            live = LlmLiveProgress(
                _emit,
                phase="success_ledger_gate",
                step=int(notify.step or 0),
                entry_id=entry_id,
            )
            on_delta = live.on_delta
        gate = evaluate_gate_with_llm(entry, on_delta=on_delta)
        if live is not None:
            try:
                live.flush()
            except Exception:
                pass
        applied = apply_gate_result(data_dir, task_id, entry_id, gate)
        _notify_usage(notify, gate)
        finish_extra: dict[str, Any] = {
            "gate_status": gate.get("status"),
            "gate_pass": bool(gate.get("pass")),
            "elapsed_ms": gate.get("elapsed_ms"),
            "model": gate.get("model"),
        }
        usage = gate.get("usage")
        if isinstance(usage, dict) and usage:
            finish_extra["tokens"] = usage
        entry_after = applied.get("entry") if isinstance(applied, dict) else None
        if isinstance(entry_after, dict) and entry_after.get("lore_id"):
            finish_extra["lore_id"] = entry_after.get("lore_id")
        _notify_progress(
            notify,
            _gate_finish_text(gate, applied),
            status="done",
            entry_id=entry_id,
            **finish_extra,
        )
    except Exception:
        try:
            applied = apply_gate_result(
                data_dir,
                task_id,
                entry_id,
                {"status": "failed", "pass": False, "error": "gate worker exception"},
            )
            _notify_progress(
                notify,
                "成功价值台账：评估失败",
                status="done",
                entry_id=entry_id,
                gate_status="failed",
                gate_pass=False,
            )
        except Exception:
            pass


def schedule_gate_eval(
    data_dir: str | Path,
    task_id: str,
    entry_id: str,
    *,
    notify: GateNotify | None = None,
) -> None:
    """与主任务并发：后台线程跑门控，不阻塞规划闭环。"""
    # 立刻提示，避免排队空隙无进度
    _notify_progress(
        notify,
        "成功价值台账：已排队三维评估…",
        status="queued",
        entry_id=entry_id,
    )
    _GATE_POOL.submit(
        _gate_worker, str(data_dir), str(task_id), str(entry_id), notify
    )


def apply_gate_sync_for_tests(
    data_dir: str | Path,
    task_id: str,
    entry_id: str,
    *,
    gate: dict[str, Any] | None = None,
    notify: GateNotify | None = None,
) -> dict[str, Any]:
    """测试用：可注入 gate，或同步跑 LLM；可选走旁路进度/用量回调。"""
    if gate is None:
        entry = get_entry(data_dir, task_id, entry_id)
        if not entry:
            return {"ok": False, "error": "entry not found"}
        _notify_progress(
            notify,
            "成功价值台账：三维评估中…",
            status="running",
            entry_id=entry_id,
        )
        gate = evaluate_gate_with_llm(entry)
    applied = apply_gate_result(data_dir, task_id, entry_id, gate)
    _notify_usage(notify, gate)
    finish_extra: dict[str, Any] = {
        "gate_status": gate.get("status"),
        "gate_pass": bool(gate.get("pass")),
        "elapsed_ms": gate.get("elapsed_ms"),
        "model": gate.get("model"),
    }
    usage = gate.get("usage")
    if isinstance(usage, dict) and usage:
        finish_extra["tokens"] = usage
    entry_after = applied.get("entry") if isinstance(applied, dict) else None
    if isinstance(entry_after, dict) and entry_after.get("lore_id"):
        finish_extra["lore_id"] = entry_after.get("lore_id")
    _notify_progress(
        notify,
        _gate_finish_text(gate, applied),
        status="done",
        entry_id=entry_id,
        **finish_extra,
    )
    return applied
