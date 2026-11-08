# term:个人知识库  doc:doc/功能/个人知识库/说明.md
"""知识检索：工作区文件名/正文 FTS + 笔记 FTS/向量；不再以抽 KV key 当主搜。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# 各通道本地分归一到约 0～1 后，再加通道基分，避免正文 bm25 小数被文件名 LIKE 挤出榜
_CHANNEL_BASE = {
    "file_content": 2.2,
    "file_name": 2.0,
    "note": 1.6,
    "task": 1.8,
    "log": 1.4,
}


def _biz_methods_on(biz_id: str, *methods: str) -> bool:
    try:
        from fawai_avatar.index_policy import biz_has
        from fawai_avatar.memory_settings import get_memory_store

        pol = get_memory_store().index_policy()
        return any(biz_has(pol, biz_id, m) for m in methods)
    except Exception:
        return False


def _tasks_search_enabled() -> bool:
    from fawai_avatar.index_policy import BIZ_TASKS, METHOD_FTS, METHOD_NAME

    return _biz_methods_on(BIZ_TASKS, METHOD_FTS, METHOD_NAME)


def _logs_search_enabled() -> bool:
    from fawai_avatar.index_policy import BIZ_LOGS, METHOD_FTS

    return _biz_methods_on(BIZ_LOGS, METHOD_FTS)


def _task_hits(query: str, *, limit: int) -> list[dict[str, Any]]:
    if not _tasks_search_enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    try:
        from fawai_avatar.task.store import TaskStore
        from fawai_avatar.memory_settings import get_memory_store

        store = TaskStore(get_memory_store().data_dir)
        rows = store.list_tasks(q=q, limit=limit, archived=False)
    except Exception as exc:
        return [{"_channel_error": "tasks", "error": str(exc)}]
    out: list[dict[str, Any]] = []
    for rec in rows:
        title = str((rec.meta or {}).get("title") or "")
        goal = str(rec.goal or "")
        preview = (goal or title).replace("\n", " ")[:160]
        out.append(
            {
                "kind": "task",
                "id": rec.task_id,
                "score": _CHANNEL_BASE["task"] + 0.5,
                "summary": f"{title or rec.task_id} — {preview}",
                "source": "tasks_api",
                "raw": {
                    "task_id": rec.task_id,
                    "status": rec.status,
                    "title": title,
                    "goal": goal[:400],
                },
            }
        )
    return out


def _log_hits(query: str, *, limit: int) -> list[dict[str, Any]]:
    if not _logs_search_enabled():
        return []
    q = (query or "").strip().lower()
    if not q:
        return []
    try:
        from fawai_avatar.channel.system_log import SystemLogStore
        from fawai_avatar.memory_settings import get_memory_store

        store = SystemLogStore(get_memory_store().data_dir)
        rows = store.list_logs(limit=max(limit * 20, 80))
    except Exception as exc:
        return [{"_channel_error": "logs", "error": str(exc)}]
    out: list[dict[str, Any]] = []
    for entry in rows:
        blob = " ".join(
            [
                str(getattr(entry, "action", "") or ""),
                str(getattr(entry, "message", "") or ""),
                str(getattr(entry, "task_id", "") or ""),
                str(getattr(entry, "category", "") or ""),
            ]
        ).lower()
        if q not in blob:
            continue
        msg = str(getattr(entry, "message", "") or "").replace("\n", " ")[:160]
        out.append(
            {
                "kind": "log",
                "id": str(getattr(entry, "log_id", "") or ""),
                "score": _CHANNEL_BASE["log"] + 0.3,
                "summary": f"{getattr(entry, 'action', '')} — {msg}",
                "source": "logs_api",
                "raw": {
                    "log_id": getattr(entry, "log_id", None),
                    "action": getattr(entry, "action", None),
                    "task_id": getattr(entry, "task_id", None),
                    "message": msg,
                },
            }
        )
        if len(out) >= limit:
            break
    return out


def _index_catalog_brief() -> dict[str, Any]:
    try:
        from fawai_avatar.memory_settings import get_memory_store

        return get_memory_store().index_policy_payload().get("catalog") or {}
    except Exception:
        return {}


def _note_hits(query: str, *, limit: int) -> list[dict[str, Any]]:
    from fawai_avatar.config import lore_note_fts_enabled, lore_vector_enabled, resolve_lore_dir
    from fawai_avatar.lore_api import ensure_lore_fts
    from fawai_lore import open_lore
    from fawai_lore.fts import LoreFtsIndex, fts5_available

    q = (query or "").strip()
    if not q:
        return []
    root = resolve_lore_dir()
    lore = open_lore(root)
    out: list[dict[str, Any]] = []
    if lore_note_fts_enabled(default=True) and fts5_available():
        try:
            ensure_lore_fts()
        except Exception:
            pass
        fts_path = Path(root) / "fts.sqlite"
        if fts_path.is_file():
            try:
                with LoreFtsIndex(root) as idx:
                    for row in idx.search(q, limit=limit, kind="note"):
                        note = lore.notes.get(row["id"])
                        if note is None or not note.enabled:
                            continue
                        preview = (note.body or "").replace("\n", " ")[:160]
                        local = float(row.get("score") or 0)
                        out.append(
                            {
                                "kind": "note",
                                "id": note.id,
                                "score": _CHANNEL_BASE["note"] + local,
                                "summary": f"{note.title} — {preview}",
                                "source": "lore_fts",
                                "raw": note.to_dict(),
                            }
                        )
            except Exception:
                pass

    if lore_vector_enabled() and len(out) < limit:
        try:
            from fawai_avatar.lore_embed import make_embed_fn
            from fawai_lore.vector import VectorIndex, sqlite_vec_available

            if sqlite_vec_available():
                embed_fn = make_embed_fn()
                idx = VectorIndex(root)
                try:
                    emb = embed_fn(q)
                    for note_id, dist in idx.search(emb, limit=limit):
                        note = lore.notes.get(note_id)
                        if note is None or not note.enabled:
                            continue
                        preview = (note.body or "").replace("\n", " ")[:160]
                        local = 1.0 / (1.0 + float(dist))
                        out.append(
                            {
                                "kind": "note",
                                "id": note.id,
                                "score": _CHANNEL_BASE["note"] + local * 0.5,
                                "summary": f"{note.title} — {preview}",
                                "source": "vector",
                                "raw": note.to_dict(),
                            }
                        )
                finally:
                    idx.close()
        except Exception:
            pass
    return out


def _name_hits(query: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        from fawai_avatar.memory_index import search_names

        data = search_names(query, limit=limit)
    except Exception as exc:
        return [{"_channel_error": "name_index", "error": str(exc)}]
    if not data.get("ok"):
        return [
            {
                "_channel_error": "name_index",
                "error": str(data.get("error") or "name_index failed"),
            }
        ]
    out: list[dict[str, Any]] = []
    for h in data.get("hits") or []:
        path = h.get("path")
        if not path:
            continue
        raw_score = float(h.get("score") or 0)
        # name_like 用 1～2；name_fts 已是 0～1
        local = raw_score / 2.0 if raw_score > 1.0 else raw_score
        out.append(
            {
                "kind": "file_name",
                "id": path,
                "score": _CHANNEL_BASE["file_name"] + local,
                "summary": f"{h.get('name') or Path(path).name} · {path}",
                "source": h.get("source") or "name_index",
                "raw": h,
            }
        )
    return out


def _content_hits(query: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        from fawai_avatar.memory_index import search_content

        data = search_content(query, limit=limit)
    except Exception as exc:
        return [{"_channel_error": "content_fts", "error": str(exc)}]
    if not data.get("ok"):
        return [
            {
                "_channel_error": "content_fts",
                "error": str(data.get("error") or "content_fts failed"),
            }
        ]
    out: list[dict[str, Any]] = []
    for h in data.get("hits") or []:
        path = h.get("path")
        if not path:
            continue
        snip = (h.get("snippet") or "").strip()
        local = float(h.get("score") or 0)
        out.append(
            {
                "kind": "file_content",
                "id": path,
                "score": _CHANNEL_BASE["file_content"] + local,
                "summary": f"{Path(path).name}: {snip}" if snip else str(path),
                "source": "content_fts",
                "raw": h,
            }
        )
    return out


def _merge_fair(
    groups: dict[str, list[dict[str, Any]]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    分通道保底，再按分数补齐。

    避免「文件名 LIKE 高分占满 limit → 正文 FTS 一条都看不到」。
    """
    limit = max(1, int(limit))
    nonempty = {k: list(v) for k, v in groups.items() if v}
    for hits in nonempty.values():
        hits.sort(key=lambda h: (-float(h.get("score") or 0), str(h.get("id"))))

    counts = {k: len(v) for k, v in nonempty.items()}
    if not nonempty:
        return [], counts

    reserve = max(1, limit // len(nonempty))
    # 通道少、limit 大时略放宽保底
    if limit >= 12:
        reserve = max(reserve, 3)
    elif limit >= 6:
        reserve = max(reserve, 2)

    picked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _take(h: dict[str, Any]) -> bool:
        sig = (str(h.get("kind")), str(h.get("id")))
        if sig in seen:
            return False
        seen.add(sig)
        picked.append(h)
        return True

    for hits in nonempty.values():
        n = 0
        for h in hits:
            if n >= reserve:
                break
            if len(picked) >= limit:
                break
            if _take(h):
                n += 1

    if len(picked) < limit:
        rest: list[dict[str, Any]] = []
        for hits in nonempty.values():
            rest.extend(hits)
        rest.sort(key=lambda h: (-float(h.get("score") or 0), str(h.get("kind")), str(h.get("id"))))
        for h in rest:
            if len(picked) >= limit:
                break
            _take(h)

    return picked, counts


def _want_scope(scopes: list[str] | None, scope_id: str) -> bool:
    """scopes=None → 兼容全开；否则仅跑列出的范围。"""
    if scopes is None:
        return True
    return scope_id in scopes


def knowledge_search(
    *,
    query: str,
    limit: int = 10,
    scopes: list[str] | Any | None = None,
) -> dict[str, Any]:
    """
    lore_search 真源。LLM 应传 scopes 选定范围；缺省仍混合已开通道（兼容）。
    附带 index_catalog / search_menu 供感知能力与效率。
    """
    from fawai_avatar.index_policy import normalize_scopes

    q = str(query or "").strip()
    limit = max(1, min(int(limit or 10), 50))
    catalog = _index_catalog_brief()
    scope_list = normalize_scopes(scopes)
    if not q:
        return {
            "ok": True,
            "query": q,
            "hits": [],
            "count": 0,
            "engine": "hybrid",
            "scopes": scope_list,
            "channels": ["content_fts", "name_index", "lore_fts", "vector", "tasks", "logs"],
            "channel_counts": {},
            "index_catalog": catalog,
            "search_menu": (catalog or {}).get("search_menu") or [],
        }

    # 每通道多取一些，供保底合并
    per = max(limit, min(40, limit * 2))
    raw_name = _name_hits(q, limit=per) if _want_scope(scope_list, "work_name") else []
    raw_content = (
        _content_hits(q, limit=per) if _want_scope(scope_list, "work_content") else []
    )
    raw_notes = _note_hits(q, limit=per) if _want_scope(scope_list, "lore") else []
    raw_tasks = _task_hits(q, limit=per) if _want_scope(scope_list, "tasks") else []
    raw_logs = _log_hits(q, limit=per) if _want_scope(scope_list, "logs") else []

    channel_errors: dict[str, str] = {}
    name_hits: list[dict[str, Any]] = []
    content_hits: list[dict[str, Any]] = []
    task_hits: list[dict[str, Any]] = []
    log_hits: list[dict[str, Any]] = []
    for row in raw_name:
        if row.get("_channel_error"):
            channel_errors[str(row["_channel_error"])] = str(row.get("error") or "")
        else:
            name_hits.append(row)
    for row in raw_content:
        if row.get("_channel_error"):
            channel_errors[str(row["_channel_error"])] = str(row.get("error") or "")
        else:
            content_hits.append(row)
    for row in raw_tasks:
        if row.get("_channel_error"):
            channel_errors[str(row["_channel_error"])] = str(row.get("error") or "")
        else:
            task_hits.append(row)
    for row in raw_logs:
        if row.get("_channel_error"):
            channel_errors[str(row["_channel_error"])] = str(row.get("error") or "")
        else:
            log_hits.append(row)

    hits, channel_counts = _merge_fair(
        {
            "file_content": content_hits,
            "file_name": name_hits,
            "note": raw_notes,
            "task": task_hits,
            "log": log_hits,
        },
        limit=limit,
    )
    ran = []
    if _want_scope(scope_list, "work_content"):
        ran.append("content_fts")
    if _want_scope(scope_list, "work_name"):
        ran.append("name_index")
    if _want_scope(scope_list, "lore"):
        ran.extend(["lore_fts", "vector"])
    if _want_scope(scope_list, "tasks"):
        ran.append("tasks")
    if _want_scope(scope_list, "logs"):
        ran.append("logs")
    out: dict[str, Any] = {
        "ok": True,
        "query": q,
        "hits": hits,
        "count": len(hits),
        "engine": "hybrid",
        "scopes": scope_list,
        "channels": ran,
        "channel_counts": {
            "file_content": channel_counts.get("file_content", 0),
            "file_name": channel_counts.get("file_name", 0),
            "note": channel_counts.get("note", 0),
            "task": channel_counts.get("task", 0),
            "log": channel_counts.get("log", 0),
        },
        "index_catalog": catalog,
        "search_menu": (catalog or {}).get("search_menu") or [],
    }
    if channel_errors:
        out["channel_errors"] = channel_errors
    # 仅文件名范围空命中：常见是自然语言整句过窄；提示扩 scope / 拆词
    if (
        not hits
        and scope_list == ["work_name"]
        and not channel_errors
    ):
        out["hint"] = (
            "文件名未命中：可改 scopes=[work_content] 或 [lore]/[tasks]，"
            "或拆成短词再搜（如 4090 / 云服务）；ssh 口令类正文多在配置 md，不在文件名。"
        )
    return out


def knowledge_get(*, key: str) -> dict[str, Any]:
    """
    lore_get 真源：笔记 id → 文件路径（索引/磁盘）→ 不以 KV key 为主。
    """
    kid = str(key or "").strip()
    if not kid:
        return {"ok": False, "error": "key required"}

    from fawai_avatar.config import resolve_lore_dir
    from fawai_lore import open_lore

    lore = open_lore(resolve_lore_dir())
    note = lore.notes.get(kid)
    if note is not None:
        return {"ok": True, "kind": "note", "item": note.to_dict(), "engine": "lore_note"}

    path = Path(kid).expanduser()
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            head = "\n".join(lines[:80])
            return {
                "ok": True,
                "kind": "file",
                "item": {
                    "path": str(path.resolve()),
                    "preview": head,
                    "line_count": len(lines),
                    "truncated": len(lines) > 80,
                },
                "engine": "filesystem",
            }
    except OSError:
        pass

    try:
        from fawai_avatar.memory_index import search_names

        named = search_names(kid, limit=5)
        if named.get("ok"):
            for h in named.get("hits") or []:
                p = Path(str(h.get("path") or ""))
                if p.is_file() and (
                    p.name.lower() == kid.lower()
                    or str(p) == kid
                    or kid.lower() in p.name.lower()
                ):
                    try:
                        text = p.read_text(encoding="utf-8", errors="replace")
                        lines = text.splitlines()
                        return {
                            "ok": True,
                            "kind": "file",
                            "item": {
                                "path": str(p),
                                "preview": "\n".join(lines[:80]),
                                "line_count": len(lines),
                                "truncated": len(lines) > 80,
                                "via": "name_index",
                            },
                            "engine": "name_index",
                        }
                    except OSError:
                        continue
    except Exception:
        pass

    try:
        from fawai_avatar.memory_index import search_content

        content = search_content(kid, limit=3)
        if content.get("ok") and content.get("hits"):
            h0 = content["hits"][0]
            return {
                "ok": True,
                "kind": "file_content",
                "item": h0,
                "engine": "content_fts",
            }
    except Exception:
        pass

    return {"ok": False, "error": f"not found: {kid}"}


def knowledge_prompt_block(goal: str = "", *, limit: int = 8, max_chars: int = 1800) -> str:
    """规划注入：混合检索摘要；未命中不灌随机 KV。"""
    data = knowledge_search(query=goal or "", limit=limit)
    hits = data.get("hits") or []
    if not hits:
        return ""
    lines = ["【知识检索】（按索引配置已开通道；非旁路 KV）"]
    for h in hits:
        kind = str(h.get("kind") or "")
        tag = {
            "note": "笔记",
            "file_name": "文件名",
            "file_content": "正文",
            "task": "任务",
            "log": "日志",
        }.get(kind, kind)
        src = h.get("source") or ""
        lines.append(f"- [{tag}/{src}] {h.get('summary')}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated)"
    return text
