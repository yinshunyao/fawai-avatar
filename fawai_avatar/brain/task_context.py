# term:任务内上下文查询  tool:task_context_search/task_context_get  doc:doc/功能/任务内上下文查询/说明.md
"""同任务历史词法检索与回灌（有损规划窗的 Recall）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_PATH_RE = re.compile(
    r"(?:path=|paths?:\s*|path':\s*|path\":\s*)['\"]?([^\s'\",\]]+)",
    re.IGNORECASE,
)
_DUMP_PATH_RE = re.compile(
    r"((?:[^\s，,;；\"']*?/)?(?:observe/)?tool_[0-9A-Za-z._-]+\.(?:txt|json|jsonl))",
    re.IGNORECASE,
)

_DEFAULT_SEARCH_LIMIT = 8
_DEFAULT_GET_CHARS = 6000
_MAX_GET_CHARS = 20000
_SUMMARY_CHARS = 220


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def search_limit_default() -> int:
    return max(1, min(_env_int("FAWAI_TASK_CONTEXT_SEARCH_LIMIT", _DEFAULT_SEARCH_LIMIT), 30))


def get_chars_default() -> int:
    return max(
        500,
        min(_env_int("FAWAI_TASK_CONTEXT_GET_CHARS", _DEFAULT_GET_CHARS), _MAX_GET_CHARS),
    )


def _msg_text(msg: dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or msg.get("text") or "")


def _infer_kind(role: str, text: str) -> str:
    if "【已完成步骤摘要】" in text or "（这是更早步骤的摘要" in text:
        return "compact"
    if "执行结果" in text or "工具结果" in text:
        return "tool"
    if role == "assistant":
        return "assistant"
    if role == "user":
        return "user"
    return role or "unknown"


def _extract_paths(text: str) -> list[str]:
    out: list[str] = []
    for m in _PATH_RE.finditer(text or ""):
        p = (m.group(1) or "").strip().rstrip("',\"")
        if p and p not in out:
            out.append(p)
    return out[:12]


def _extract_observe_dumps(text: str) -> list[str]:
    out: list[str] = []
    for m in _DUMP_PATH_RE.finditer(text or ""):
        p = (m.group(1) or "").strip().rstrip("',\"")
        if not p:
            continue
        if "observe/" not in p and not p.startswith("tool_"):
            continue
        if p not in out:
            out.append(p)
    return out[:8]


def _tokenize_query(query: str) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    parts = re.split(r"[\s/\\|,+，、；;]+", q)
    tokens = [p for p in parts if len(p) >= 2]
    if not tokens and q:
        tokens = [q]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:12]


def normalize_history_messages(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for i, raw in enumerate(history or []):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip() or "user"
        text = _msg_text(raw)
        if not text.strip():
            continue
        msg_id = str(raw.get("msg_id") or raw.get("id") or f"m{i}").strip() or f"m{i}"
        kind = str(raw.get("kind") or "").strip() or _infer_kind(role, text)
        records.append(
            {
                "msg_id": msg_id,
                "index": i,
                "role": role,
                "kind": kind,
                "text": text,
                "paths": _extract_paths(text),
                "observe_dumps": _extract_observe_dumps(text),
            }
        )
    return records


def _looks_like_empty_prior_claim(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    markers = (
        "本任务历史无记录",
        "历史无记录",
        "没有获取到任务",
        "无历史",
    )
    return any(m in t for m in markers) and len(t) < 400


def _conclusion_text(meta: dict[str, Any] | None) -> str:
    """优先 prior_final_thought（追问重开前快照），否则 final_thought。"""
    m = meta or {}
    for key in ("prior_final_thought", "final_thought"):
        text = str(m.get(key) or "").strip()
        if text and not _looks_like_empty_prior_claim(text):
            return text
    return ""


def _history_has_substance(history: list[dict[str, Any]] | None) -> bool:
    """是否已有非追问/非追加的实质正文（结论、工具结果等）。"""
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        text = _msg_text(msg)
        if not text.strip():
            continue
        if text.startswith("【追问】") or text.startswith("【用户追加】") or text.startswith(
            "【澄清答复】"
        ):
            continue
        return True
    return False


def _followup_only_messages(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        text = _msg_text(msg)
        if text.startswith("【追问】") or text.startswith("【用户追加】") or text.startswith(
            "【澄清答复】"
        ):
            out.append(dict(msg))
    return out


def prior_context_messages(
    meta: dict[str, Any] | None,
    *,
    data_dir: str | Path | None = None,
    task_id: str | None = None,
    max_session: int = 12,
    ignore_checkpoint: bool = False,
) -> list[dict[str, Any]]:
    """终态追问 / 无检查点时：从结论、旧 steps、会话信封拼出可搜上下文。

    ignore_checkpoint=True：即便已有 run_checkpoint，仍拼 prior（给「live 过薄」的
    task_context 搜索并集用，避免只搜追问句）。
    """
    from fawai_avatar.runtime.checkpoint import history_from_step_dicts, load_checkpoint

    out: list[dict[str, Any]] = []
    m = meta if isinstance(meta, dict) else {}
    # 已有检查点 history 时不重复塞 prior（调用方自己用 checkpoint）
    if not ignore_checkpoint:
        cp = load_checkpoint(m)
        if isinstance(cp, dict) and isinstance(cp.get("history"), list) and cp.get("history"):
            return out

    conclusion = _conclusion_text(m)
    if conclusion:
        out.append(
            {
                "role": "assistant",
                "content": f"【此前任务结论】\n{conclusion[:3000]}",
            }
        )

    # 仅当有 prior_steps 快照时用步骤合成；当前 steps 常是追问重开后的空转步，不要盖住会话结论
    prior_steps = m.get("prior_steps")
    if isinstance(prior_steps, list) and prior_steps:
        synthesized = history_from_step_dicts(prior_steps)
        for msg in synthesized[-16:]:
            if isinstance(msg, dict) and _msg_text(msg).strip():
                out.append(dict(msg))

    tid = (task_id or "").strip()
    if tid and data_dir is not None:
        out.extend(
            _session_envelopes_as_messages(
                task_id=tid, data_dir=data_dir, limit=max_session
            )
        )
    return out


def _session_envelopes_as_messages(
    *,
    task_id: str,
    data_dir: str | Path,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """从 session_history.jsonl 抽本任务结论文本（检查点清掉后的兜底）。

    优先保留 kind=text 的助手结论文（常在会话前段）；进度只作补充，避免末尾空转进度挤掉早期结论。
    """
    path = Path(data_dir) / "session_history.jsonl"
    if not path.is_file():
        return []
    tid = (task_id or "").strip()
    texts: list[dict[str, Any]] = []
    progresses: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if str(obj.get("task_id") or "") != tid:
                    continue
                kind = str(obj.get("kind") or "")
                text = str(obj.get("text") or "").strip()
                if not text:
                    continue
                if kind == "text" and len(text) >= 40:
                    if _looks_like_empty_prior_claim(text):
                        continue
                    texts.append(
                        {
                            "role": "assistant",
                            "content": f"【会话记录】\n{text[:2500]}",
                        }
                    )
                elif kind == "progress" and (
                    ("规划完成（success）" in text)
                    or ("Epoch" in text)
                    or ("训练进度" in text)
                ):
                    progresses.append(
                        {
                            "role": "user",
                            "content": f"【执行进度】\n{text[:1200]}",
                        }
                    )
    except OSError:
        return []
    lim = max(1, int(limit))
    # 结论文优先；进度最多占一半名额
    text_n = min(len(texts), max(2, lim // 2 + 1))
    prog_n = min(len(progresses), max(0, lim - text_n))
    out = texts[-text_n:] + progresses[-prog_n:]
    return out


def load_history_from_task_meta(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从 run_checkpoint / steps 取本任务 history（可能已被 clip）。"""
    from fawai_avatar.runtime.checkpoint import (
        history_from_step_dicts,
        load_checkpoint,
    )

    cp = load_checkpoint(meta)
    if isinstance(cp, dict):
        hist = cp.get("history")
        if isinstance(hist, list) and hist:
            return [m for m in hist if isinstance(m, dict)]
    steps = (meta or {}).get("steps")
    if isinstance(steps, list) and steps:
        return history_from_step_dicts(steps)
    return []


def load_task_history(
    *,
    task_id: str,
    data_dir: str | Path | None = None,
    history_override: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """返回 (messages, source)。

    优先 live override；若 live 过薄（只剩追问/追加），与检查点/prior **并集**，
    避免 task_context_search 只扫【追问】句得到 0 hits。
    """
    from fawai_avatar.runtime.checkpoint import load_checkpoint

    tid = (task_id or "").strip()
    root = ""
    meta: dict[str, Any] = {}
    task = None
    if tid:
        try:
            from fawai_avatar.config import RuntimeConfig
            from fawai_avatar.task.store import TaskStore

            root = str(data_dir) if data_dir is not None else RuntimeConfig.from_env().data_dir
            store = TaskStore(root)
            task = store.get_task(tid)
            if task is not None:
                raw_meta = getattr(task, "meta", None) or {}
                meta = raw_meta if isinstance(raw_meta, dict) else {}
        except Exception:
            task = None
            meta = {}

    if history_override is not None:
        live = [m for m in history_override if isinstance(m, dict)]
        if _history_has_substance(live):
            return live, "live"
        disk = load_history_from_task_meta(meta) if meta else []
        if disk:
            return disk + _followup_only_messages(live), "checkpoint+live"
        prior = prior_context_messages(
            meta,
            data_dir=root or data_dir,
            task_id=tid,
            ignore_checkpoint=True,
        )
        if prior:
            return prior + live, "prior+live"
        return live, "live"

    if not tid:
        return [], "none"
    if task is None:
        return [], "none"
    msgs = load_history_from_task_meta(meta)
    if msgs:
        src = "checkpoint" if load_checkpoint(meta) else "steps"
        return msgs, src
    prior = prior_context_messages(meta, data_dir=root, task_id=tid)
    if prior:
        return prior, "prior"
    return [], "empty"


def merge_prior_into_history(
    history: list[dict[str, Any]] | None,
    *,
    meta: dict[str, Any] | None,
    data_dir: str | Path | None,
    task_id: str,
) -> list[dict[str, Any]]:
    """无检查点新开跑时：把此前结论/步骤/会话记录垫在追问之前。"""
    base = list(history or [])
    # 已有非追问内容则不重复垫（检查点续跑）
    has_substance = False
    for msg in base:
        text = _msg_text(msg)
        if text.startswith("【追问】") or text.startswith("【用户追加】") or text.startswith(
            "【澄清答复】"
        ):
            continue
        if text.strip():
            has_substance = True
            break
    if has_substance:
        return base
    prior = prior_context_messages(meta, data_dir=data_dir, task_id=task_id)
    if not prior:
        return base
    return prior + base


def _score_record(rec: dict[str, Any], tokens: list[str], *, total: int) -> float:
    text = str(rec.get("text") or "").lower()
    blob = text
    blob += " " + " ".join(str(p).lower() for p in (rec.get("paths") or []))
    blob += " " + " ".join(str(p).lower() for p in (rec.get("observe_dumps") or []))
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in blob)
    if hits == 0:
        return 0.0
    idx = int(rec.get("index") or 0)
    recency = (idx + 1) / max(1, total)
    kind = str(rec.get("kind") or "")
    # 工具结果/落盘略抬分，避免短追问句仅靠「端口」一词压过含证据的旧步
    kind_boost = 0.35 if kind == "tool" else (0.1 if kind == "assistant" else 0.0)
    dump_boost = 0.2 if rec.get("observe_dumps") else 0.0
    length_boost = min(0.25, len(text) / 4000.0)
    return float(hits) + 0.12 * recency + kind_boost + dump_boost + length_boost


def _hit_summary(text: str, tokens: list[str]) -> str:
    body = (text or "").replace("\n", " ").strip()
    if not body:
        return ""
    low = body.lower()
    pos = -1
    for t in tokens:
        p = low.find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return body[:_SUMMARY_CHARS] + ("…" if len(body) > _SUMMARY_CHARS else "")
    start = max(0, pos - 40)
    end = min(len(body), pos + _SUMMARY_CHARS - 40)
    chunk = body[start:end]
    if start > 0:
        chunk = "…" + chunk
    if end < len(body):
        chunk = chunk + "…"
    return chunk


def task_context_search(
    *,
    query: str,
    task_id: str,
    data_dir: str | Path | None = None,
    history_override: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    tid = (task_id or "").strip()
    q = (query or "").strip()
    lim = max(1, min(int(limit or search_limit_default()), 30))
    if not tid:
        return {"ok": False, "error": "task_id required", "hits": [], "count": 0}
    if not q:
        return {
            "ok": True,
            "task_id": tid,
            "query": q,
            "hits": [],
            "count": 0,
            "source": "none",
            "engine": "lexical",
        }
    msgs, source = load_task_history(
        task_id=tid, data_dir=data_dir, history_override=history_override
    )
    records = normalize_history_messages(msgs)
    kind_f = (kind or "").strip().lower()
    if kind_f:
        records = [r for r in records if str(r.get("kind") or "") == kind_f]
    tokens = _tokenize_query(q)
    total = len(records)
    scored: list[tuple[float, dict[str, Any]]] = []
    for rec in records:
        s = _score_record(rec, tokens, total=total)
        if s <= 0:
            continue
        scored.append((s, rec))
    scored.sort(key=lambda x: (-x[0], -int(x[1].get("index") or 0)))
    hits: list[dict[str, Any]] = []
    for score, rec in scored[:lim]:
        hits.append(
            {
                "msg_id": rec["msg_id"],
                "index": rec["index"],
                "role": rec["role"],
                "kind": rec["kind"],
                "score": round(float(score), 3),
                "summary": _hit_summary(str(rec["text"]), tokens),
                "paths": list(rec.get("paths") or [])[:6],
                "observe_dumps": list(rec.get("observe_dumps") or [])[:4],
            }
        )
    return {
        "ok": True,
        "task_id": tid,
        "query": q,
        "hits": hits,
        "count": len(hits),
        "scanned": total,
        "source": source,
        "engine": "lexical",
    }


def _clip_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    body = text or ""
    if len(body) <= max_chars:
        return body, False
    return body[: max_chars - 1] + "…", True


def _read_observe_dump(
    path: str,
    *,
    data_dir: str | Path | None,
    max_chars: int,
) -> dict[str, Any]:
    raw = (path or "").strip()
    if not raw:
        return {"ok": False, "error": "empty observe_path"}
    candidates: list[Path] = []
    p = Path(raw).expanduser()
    candidates.append(p)
    if data_dir is not None:
        root = Path(data_dir)
        if not p.is_absolute():
            candidates.append(root / raw)
            if raw.startswith("observe/"):
                candidates.append(root / raw)
            else:
                candidates.append(root / "observe" / Path(raw).name)
        # 仅允许 data_dir 下 observe
        allowed = (root / "observe").resolve()
        for c in list(candidates):
            try:
                resolved = c.resolve()
            except OSError:
                continue
            if allowed in resolved.parents or resolved == allowed or resolved.parent == allowed:
                if resolved.is_file():
                    text = resolved.read_text(encoding="utf-8", errors="replace")
                    clipped, truncated = _clip_text(text, max_chars=max_chars)
                    return {
                        "ok": True,
                        "kind": "observe_dump",
                        "observe_path": str(resolved),
                        "text": clipped,
                        "truncated": truncated,
                        "chars": len(text),
                    }
        return {
            "ok": False,
            "error": "observe_path not found under data_dir/observe",
            "observe_path": raw,
        }
    if p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace")
        clipped, truncated = _clip_text(text, max_chars=max_chars)
        return {
            "ok": True,
            "kind": "observe_dump",
            "observe_path": str(p.resolve()),
            "text": clipped,
            "truncated": truncated,
            "chars": len(text),
        }
    return {"ok": False, "error": "observe_path not found", "observe_path": raw}


def task_context_get(
    *,
    task_id: str,
    data_dir: str | Path | None = None,
    history_override: list[dict[str, Any]] | None = None,
    msg_id: str | None = None,
    index: int | None = None,
    observe_path: str | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    cap = max(500, min(int(max_chars or get_chars_default()), _MAX_GET_CHARS))
    obs = (observe_path or "").strip()
    if obs:
        data = _read_observe_dump(obs, data_dir=data_dir, max_chars=cap)
        data["task_id"] = tid
        return data

    msgs, source = load_task_history(
        task_id=tid, data_dir=data_dir, history_override=history_override
    )
    records = normalize_history_messages(msgs)
    target: dict[str, Any] | None = None
    mid = (msg_id or "").strip()
    if mid:
        for rec in records:
            if rec["msg_id"] == mid:
                target = rec
                break
    elif index is not None:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"ok": False, "error": "index must be int", "task_id": tid}
        for rec in records:
            if int(rec["index"]) == idx:
                target = rec
                break
    else:
        return {
            "ok": False,
            "error": "require msg_id or index or observe_path",
            "task_id": tid,
        }
    if target is None:
        return {
            "ok": False,
            "error": "message not found",
            "task_id": tid,
            "msg_id": mid or None,
            "index": index,
            "source": source,
        }
    text, truncated = _clip_text(str(target["text"]), max_chars=cap)
    return {
        "ok": True,
        "task_id": tid,
        "source": source,
        "kind": "message",
        "msg_id": target["msg_id"],
        "index": target["index"],
        "role": target["role"],
        "msg_kind": target["kind"],
        "text": text,
        "truncated": truncated,
        "chars": len(str(target["text"])),
        "paths": list(target.get("paths") or [])[:8],
        "observe_dumps": list(target.get("observe_dumps") or [])[:6],
        "note": (
            "若正文仍不够：可对 observe_dumps 再 task_context_get(observe_path=…)，"
            "或对 paths 用 fs_read 定向再读。"
        ),
    }


def assert_same_task(
    *,
    runtime_task_id: str | None,
    args_task_id: str | None,
) -> str | None:
    """跨任务默认拒绝；返回错误文案或 None。"""
    runtime = (runtime_task_id or "").strip()
    asked = (args_task_id or "").strip()
    if not asked:
        return None
    if not runtime:
        return None
    if asked != runtime:
        return (
            f"task_context_* 默认仅当前任务；拒绝查其它 task_id={asked!r} "
            f"(current={runtime!r})"
        )
    return None
