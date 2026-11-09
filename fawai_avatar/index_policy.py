# term:个人知识库  doc:doc/功能/个人知识库/说明.md
"""按区 / 数据业务划分的索引方式策略（多选）；对智能体可见，由 LLM 选通道。"""
from __future__ import annotations

import json
from typing import Any

# 索引方式（多选）
METHOD_FTS = "fts"  # 正文 / 笔记 FTS5
METHOD_NAME = "name"  # 文件名索引
METHOD_VECTOR = "vector"  # sqlite-vec（笔记）

ALL_METHODS: tuple[str, ...] = (METHOD_FTS, METHOD_NAME, METHOD_VECTOR)

METHOD_META: dict[str, dict[str, str]] = {
    METHOD_FTS: {
        "label": "FTS5 全文",
        "label_en": "FTS5 full-text",
        "tool": "fts_search / lore_search(note|content)",
    },
    METHOD_NAME: {
        "label": "文件名索引",
        "label_en": "Filename index",
        "tool": "name_index_search / lore_search(name)",
    },
    METHOD_VECTOR: {
        "label": "向量检索",
        "label_en": "Vector search",
        "tool": "lore_search(vector)",
    },
}

# 数据盘业务面（与分析文档对齐）
BIZ_INDEX_CACHE = "index_cache"
BIZ_LORE = "lore"
BIZ_TASKS = "tasks_sessions"
BIZ_OBSERVE = "observe"
BIZ_CONFIG = "config"
BIZ_LOGS = "system_logs"
BIZ_OTHER = "other"

# 各业务独立多选索引方式。索引缓存/配置库禁止再扫；其余可配。
# engine=none 表示勾选后仅记意向（尚无检索引擎）；tasks_api/logs_api/lore 勾选后可检索。
DATA_BUSINESSES: tuple[dict[str, Any], ...] = (
    {
        "id": BIZ_INDEX_CACHE,
        "label": "索引缓存",
        "label_en": "Index cache",
        "paths": "memory_index/ · code_index/",
        "allowed": (),
        "locked": True,
        "engine": "self",
        "hint": "",
    },
    {
        "id": BIZ_LORE,
        "label": "知识库存盘",
        "label_en": "Lore store",
        "paths": "lore/",
        "allowed": (METHOD_FTS, METHOD_VECTOR),
        "locked": False,
        "engine": "lore",
        "hint": "",
    },
    {
        "id": BIZ_TASKS,
        "label": "任务会话",
        "label_en": "Tasks & sessions",
        "paths": "tasks.sqlite · sessions/ · session_*",
        "allowed": (METHOD_FTS, METHOD_NAME),
        "locked": False,
        "engine": "tasks_api",
        "hint": "",
    },
    {
        "id": BIZ_OBSERVE,
        "label": "观察落盘",
        "label_en": "Observe dumps",
        "paths": "observe/",
        "allowed": (METHOD_FTS, METHOD_NAME),
        "locked": False,
        "engine": "none",
        "hint": "",
    },
    {
        "id": BIZ_CONFIG,
        "label": "配置库",
        "label_en": "Settings DBs",
        "paths": "memory_settings.sqlite · llm_settings.sqlite",
        "allowed": (),
        "locked": True,
        "engine": "settings_api",
        "hint": "",
    },
    {
        "id": BIZ_LOGS,
        "label": "系统日志",
        "label_en": "System logs",
        "paths": "system_logs.sqlite",
        "allowed": (METHOD_FTS,),
        "locked": False,
        "engine": "logs_api",
        "hint": "",
    },
    {
        "id": BIZ_OTHER,
        "label": "其它运行态",
        "label_en": "Other runtime",
        "paths": "versions/ · runtime* · skills/ · analysis/ · frpc.*",
        "allowed": (METHOD_FTS, METHOD_NAME),
        "locked": False,
        "engine": "none",
        "hint": "",
    },
)

_BIZ_BY_ID = {b["id"]: b for b in DATA_BUSINESSES}

# 工作区 / 可读区可选方式（文件扫盘）
ZONE_FILE_METHODS: tuple[str, ...] = (METHOD_FTS, METHOD_NAME)


def default_policy() -> dict[str, Any]:
    """开箱：工作区开 FTS+文件名；可读区不开；知识库开 FTS+向量；任务会话开 FTS（走任务 API）。"""
    businesses: dict[str, Any] = {}
    for b in DATA_BUSINESSES:
        methods: list[str] = []
        if b["id"] == BIZ_LORE:
            methods = [METHOD_FTS, METHOD_VECTOR]
        elif b["id"] == BIZ_TASKS:
            methods = [METHOD_FTS]
        businesses[b["id"]] = {"methods": methods}
    return {
        "version": 1,
        "work": {"methods": [METHOD_FTS, METHOD_NAME]},
        "read": {"methods": []},
        "data": {"businesses": businesses},
    }


def _norm_methods(raw: Any, *, allowed: tuple[str, ...]) -> list[str]:
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        m = str(item or "").strip().lower()
        if m in {"content", "content_fts", "fulltext", "fts5"}:
            m = METHOD_FTS
        if m in {"filename", "names", "name_index"}:
            m = METHOD_NAME
        if m in {"vec", "embedding"}:
            m = METHOD_VECTOR
        if m not in allowed or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def parse_policy(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """解析并规范化策略；坏 JSON 回默认。"""
    base = default_policy()
    if raw is None or raw == "":
        return base
    data: Any
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return base
    if not isinstance(data, dict):
        return base

    work_m = _norm_methods(
        (data.get("work") or {}).get("methods") if isinstance(data.get("work"), dict) else None,
        allowed=ZONE_FILE_METHODS,
    )
    read_m = _norm_methods(
        (data.get("read") or {}).get("methods") if isinstance(data.get("read"), dict) else None,
        allowed=ZONE_FILE_METHODS,
    )
    if "work" not in data and "read" not in data and "data" not in data:
        return base

    businesses: dict[str, Any] = {}
    raw_data = data.get("data") if isinstance(data.get("data"), dict) else {}
    raw_biz = raw_data.get("businesses") if isinstance(raw_data.get("businesses"), dict) else {}
    for b in DATA_BUSINESSES:
        bid = b["id"]
        allowed = tuple(b.get("allowed") or ())
        entry = raw_biz.get(bid) if isinstance(raw_biz.get(bid), dict) else {}
        if b.get("locked"):
            methods = []
        else:
            methods = _norm_methods(entry.get("methods"), allowed=allowed)
            # 存量策略缺该业务键时，用默认（勿把「未写入」当成永久全关）
            if "methods" not in entry and not methods:
                methods = list((base.get("data") or {}).get("businesses", {}).get(bid, {}).get("methods") or [])
        businesses[bid] = {"methods": methods}

    return {
        "version": 1,
        "work": {"methods": work_m if "work" in data else base["work"]["methods"]},
        "read": {"methods": read_m if "read" in data else base["read"]["methods"]},
        "data": {"businesses": businesses},
    }


def policy_to_json(policy: dict[str, Any]) -> str:
    return json.dumps(parse_policy(policy), ensure_ascii=False, separators=(",", ":"))


def migrate_from_legacy_flags(
    *,
    fts5: bool,
    fts_dir: str,
    name_on: bool,
    name_dir: str,
    vector_on: bool,
) -> dict[str, Any]:
    """把旧全局开关 + 目录哨兵迁成按区策略。"""
    from fawai_avatar.memory_settings import is_follow_read_roots, is_follow_work_root

    pol = default_policy()
    work_m: list[str] = []
    read_m: list[str] = []

    def _place(enabled: bool, raw_dir: str, method: str) -> None:
        if not enabled:
            return
        if is_follow_read_roots(raw_dir):
            if method not in read_m:
                read_m.append(method)
        else:
            # 空 / work_root / 其它路径：算工作区（其它路径仍用 work 根解析语义）
            if method not in work_m:
                work_m.append(method)

    _place(fts5, fts_dir, METHOD_FTS)
    _place(name_on, name_dir, METHOD_NAME)

    # 旧默认两者常同开 work_root
    if fts5 and not work_m and not read_m:
        work_m.append(METHOD_FTS)
    if name_on and METHOD_NAME not in work_m and METHOD_NAME not in read_m:
        if is_follow_read_roots(name_dir):
            read_m.append(METHOD_NAME)
        else:
            work_m.append(METHOD_NAME)

    pol["work"]["methods"] = [m for m in ZONE_FILE_METHODS if m in work_m]
    pol["read"]["methods"] = [m for m in ZONE_FILE_METHODS if m in read_m]

    lore_m: list[str] = []
    if fts5:
        lore_m.append(METHOD_FTS)
    if vector_on:
        lore_m.append(METHOD_VECTOR)
    if not lore_m:
        lore_m = [METHOD_FTS, METHOD_VECTOR]
    pol["data"]["businesses"][BIZ_LORE]["methods"] = lore_m
    return pol


def zone_has(policy: dict[str, Any], zone: str, method: str) -> bool:
    z = policy.get(zone) if isinstance(policy.get(zone), dict) else {}
    methods = z.get("methods") if isinstance(z, dict) else []
    return method in (methods or [])


def biz_has(policy: dict[str, Any], biz_id: str, method: str) -> bool:
    data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    businesses = data.get("businesses") if isinstance(data, dict) else {}
    entry = businesses.get(biz_id) if isinstance(businesses, dict) else {}
    methods = entry.get("methods") if isinstance(entry, dict) else []
    return method in (methods or [])


def workspace_fts_enabled(policy: dict[str, Any]) -> bool:
    return zone_has(policy, "work", METHOD_FTS) or zone_has(policy, "read", METHOD_FTS)


def workspace_name_enabled(policy: dict[str, Any]) -> bool:
    return zone_has(policy, "work", METHOD_NAME) or zone_has(policy, "read", METHOD_NAME)


def lore_fts_enabled(policy: dict[str, Any]) -> bool:
    return biz_has(policy, BIZ_LORE, METHOD_FTS)


def lore_vector_enabled(policy: dict[str, Any]) -> bool:
    return biz_has(policy, BIZ_LORE, METHOD_VECTOR)


# lore_search.scopes 与旁路现扫工具的菜单（效率 5=最快 … 1=最慢/现扫）
SEARCH_SCOPE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "id": "work_name",
        "label": "工作区/可读区·文件名",
        "capability": "路径与文件名；不知落点时的第一刀",
        "efficiency": 5,
        "tool": "lore_search(scopes=[work_name]) 或 name_index_search",
        "channel": "file_name",
    },
    {
        "id": "tasks",
        "label": "任务会话",
        "capability": "任务 goal/title/id；找「上次任务/会话」",
        "efficiency": 5,
        "tool": "lore_search(scopes=[tasks])",
        "channel": "task",
    },
    {
        "id": "lore",
        "label": "知识库笔记",
        "capability": "笔记正文（FTS±向量）；账号/偏好/长期笔记",
        "efficiency": 4,
        "tool": "lore_search(scopes=[lore])",
        "channel": "note",
    },
    {
        "id": "work_content",
        "label": "工作区/可读区·正文 FTS",
        "capability": "已索引文件正文词法；比现扫快、弱于正则",
        "efficiency": 3,
        "tool": "lore_search(scopes=[work_content]) 或 fts_search",
        "channel": "file_content",
    },
    {
        "id": "logs",
        "label": "系统日志",
        "capability": "近期 action/message/task_id 子串",
        "efficiency": 3,
        "tool": "lore_search(scopes=[logs])",
        "channel": "log",
    },
    {
        "id": "fs_grep",
        "label": "仓内现扫 grep",
        "capability": "正则/现扫工作区树；大树慢；path 须收窄",
        "efficiency": 1,
        "tool": "fs_grep / explore_search",
        "channel": None,
        "external": True,
    },
)

_SCOPE_BY_ID = {s["id"]: s for s in SEARCH_SCOPE_DEFS}


def scope_enabled(policy: dict[str, Any], scope_id: str) -> bool:
    """该范围在当前策略下是否可搜（外部工具恒视为可用）。"""
    if scope_id == "work_name":
        return workspace_name_enabled(policy)
    if scope_id == "work_content":
        return workspace_fts_enabled(policy)
    if scope_id == "lore":
        return lore_fts_enabled(policy) or lore_vector_enabled(policy)
    if scope_id == "tasks":
        return biz_has(policy, BIZ_TASKS, METHOD_FTS) or biz_has(
            policy, BIZ_TASKS, METHOD_NAME
        )
    if scope_id == "logs":
        return biz_has(policy, BIZ_LOGS, METHOD_FTS)
    if scope_id == "fs_grep":
        return True
    return False


def normalize_scopes(raw: Any) -> list[str] | None:
    """解析 scopes；None = 未指定（由 resolve_search_scopes 扩成当前已开通道）。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return None
    out: list[str] = []
    seen: set[str] = set()
    aliases = {
        "name": "work_name",
        "filename": "work_name",
        "file_name": "work_name",
        "content": "work_content",
        "fts": "work_content",
        "file_content": "work_content",
        "note": "lore",
        "notes": "lore",
        "task": "tasks",
        "tasks_sessions": "tasks",
        "session": "tasks",
        "sessions": "tasks",
        "log": "logs",
        "system_logs": "logs",
        "workspace": "work_content",
        "work": "work_name",
    }
    for item in raw:
        sid = str(item or "").strip().lower()
        sid = aliases.get(sid, sid)
        if sid not in _SCOPE_BY_ID or sid in seen:
            continue
        if _SCOPE_BY_ID[sid].get("external"):
            continue  # lore_search 不执行现扫
        seen.add(sid)
        out.append(sid)
    return out


def enabled_search_scopes(policy: dict[str, Any]) -> list[str]:
    """记忆配置当前已开、且 lore_search 可执行的范围（不含 fs_grep 现扫）。"""
    out: list[str] = []
    for s in SEARCH_SCOPE_DEFS:
        if s.get("external"):
            continue
        sid = str(s["id"])
        if scope_enabled(policy, sid):
            out.append(sid)
    return out


def resolve_search_scopes(
    raw: Any,
    policy: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """
    请求 scopes ∩ 记忆配置开关。

    返回 (实际执行的 scopes, 因开关关闭而跳过的 scopes)。
    raw 未指定（None）→ 只跑当前已开通道，不碰已关库里的残留索引。
    """
    requested = normalize_scopes(raw)
    enabled = enabled_search_scopes(policy)
    enabled_set = set(enabled)
    if requested is None:
        return list(enabled), []
    run: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for sid in requested:
        if sid in seen:
            continue
        seen.add(sid)
        if sid in enabled_set:
            run.append(sid)
        else:
            skipped.append(sid)
    return run, skipped


def search_menu_for_agent(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """能力+效率菜单，供 LLM 选范围。"""
    rows: list[dict[str, Any]] = []
    for s in SEARCH_SCOPE_DEFS:
        on = scope_enabled(policy, s["id"])
        rows.append(
            {
                "id": s["id"],
                "label": s["label"],
                "capability": s["capability"],
                "efficiency": int(s["efficiency"]),
                "tool": s["tool"],
                "enabled": on,
                "external": bool(s.get("external")),
            }
        )
    rows.sort(key=lambda r: (-int(r["efficiency"]), r["id"]))
    return rows


def resolve_zone_scan_roots(
    policy: dict[str, Any],
    *,
    method: str,
    work_root: str,
    read_roots: str,
) -> str:
    """合并开启了某方式的区路径（冒号分隔）。"""
    from fawai_avatar.memory_settings import resolve_work_root_path

    parts: list[str] = []
    if zone_has(policy, "work", method):
        parts.append(resolve_work_root_path(work_root))
    if zone_has(policy, "read", method):
        rr = (read_roots or "").strip()
        if rr:
            parts.append(rr)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return ":".join(out)


def catalog_for_agent(
    policy: dict[str, Any],
    *,
    work_root: str,
    read_roots: str,
    data_dir: str,
) -> dict[str, Any]:
    """给智能体 / API 的可读索引目录。"""
    zones = [
        {
            "id": "work",
            "label": "工作区",
            "path": work_root,
            "methods": list((policy.get("work") or {}).get("methods") or []),
            "scan_fts": zone_has(policy, "work", METHOD_FTS),
            "scan_name": zone_has(policy, "work", METHOD_NAME),
            "tools": _tools_for_methods((policy.get("work") or {}).get("methods") or []),
        },
        {
            "id": "read",
            "label": "可读区",
            "path": read_roots,
            "methods": list((policy.get("read") or {}).get("methods") or []),
            "scan_fts": zone_has(policy, "read", METHOD_FTS),
            "scan_name": zone_has(policy, "read", METHOD_NAME),
            "tools": _tools_for_methods((policy.get("read") or {}).get("methods") or []),
        },
        {
            "id": "data",
            "label": "数据区",
            "path": data_dir,
            "businesses": [],
        },
    ]
    biz_list: list[dict[str, Any]] = []
    data = policy.get("data") if isinstance(policy.get("data"), dict) else {}
    businesses = data.get("businesses") if isinstance(data, dict) else {}
    for meta in DATA_BUSINESSES:
        bid = meta["id"]
        entry = businesses.get(bid) if isinstance(businesses, dict) else {}
        methods = list((entry or {}).get("methods") or []) if isinstance(entry, dict) else []
        tools = _tools_for_biz(bid, methods, meta)
        eng = str(meta.get("engine") or "")
        searchable = bool(methods) and eng in {"lore", "tasks_api", "logs_api"}
        biz_list.append(
            {
                "id": bid,
                "label": meta["label"],
                "paths": meta["paths"],
                "methods": methods,
                "locked": bool(meta.get("locked")),
                "engine": eng,
                "searchable": searchable,
                "hint": meta.get("hint"),
                "tools": tools,
            }
        )
    zones[2]["businesses"] = biz_list
    menu = search_menu_for_agent(policy)
    return {
        "ok": True,
        "version": int(policy.get("version") or 1),
        "zones": zones,
        "search_menu": menu,
        "tool_map": {
            "name_index_search": "工作区/可读区文件名（methods 含 name）",
            "fts_search": "工作区/可读区正文 FTS（methods 含 fts）",
            "lore_search": "按 scopes 检索；缺省=已开通道混合；应传 scopes",
            "lore_get": "按笔记 id / 路径读取",
            "fs_grep": "仓内现扫（效率1；path 须收窄）",
            "tasks": "scopes 含 tasks",
            "logs": "scopes 含 logs",
        },
        "search_guidance": (
            "先读 search_menu（efficiency 5最快→1最慢）。"
            "优先窄 scopes + 高效项；空命中再扩范围或降效率；"
            "可一段并行多个 lore_search（不同 scopes）或串行先快后慢；"
            "大树现扫用 fs_grep，勿默认全开混合；"
            "engine=none 仅意向；禁止再扫索引缓存/配置库。"
        ),
    }


def _tools_for_methods(methods: list[str] | Any) -> list[str]:
    out: list[str] = []
    for m in methods or []:
        meta = METHOD_META.get(str(m))
        if meta and meta.get("tool"):
            out.append(meta["tool"])
    return out


def _tools_for_biz(biz_id: str, methods: list[str], meta: dict[str, Any]) -> list[str]:
    if not methods and biz_id not in {BIZ_INDEX_CACHE, BIZ_CONFIG}:
        return []
    if biz_id == BIZ_TASKS:
        return ["lore_search(task)", "GET /api/tasks?q="] if methods else []
    if biz_id == BIZ_LOGS:
        return ["lore_search(log)", "GET /api/system-logs"] if methods else []
    if biz_id == BIZ_CONFIG:
        return ["settings API"]
    if biz_id == BIZ_INDEX_CACHE:
        return ["name_index_search / fts_search（查缓存库，勿再扫盘建库）"]
    if meta.get("engine") == "none":
        return []  # 意向未落地：无可用检索工具
    if biz_id == BIZ_LORE:
        return _tools_for_methods(methods)
    return _tools_for_methods(methods)


def ui_schema() -> dict[str, Any]:
    """前端渲染用：区方法 + 业务元数据。"""
    return {
        "zone_methods": [
            {"id": m, "label": METHOD_META[m]["label"], "label_en": METHOD_META[m]["label_en"]}
            for m in ZONE_FILE_METHODS
        ],
        "businesses": [
            {
                "id": b["id"],
                "label": b["label"],
                "label_en": b["label_en"],
                "paths": b["paths"],
                "allowed": list(b.get("allowed") or ()),
                "locked": bool(b.get("locked")),
                "hint": b.get("hint") or "",
                "method_options": [
                    {
                        "id": m,
                        "label": METHOD_META[m]["label"],
                        "label_en": METHOD_META[m]["label_en"],
                    }
                    for m in (b.get("allowed") or ())
                ],
            }
            for b in DATA_BUSINESSES
        ],
    }


def prompt_block(
    policy: dict[str, Any],
    *,
    work_root: str,
    read_roots: str,
    data_dir: str,
    max_chars: int = 1600,
    compact: bool = False,
) -> str:
    cat = catalog_for_agent(
        policy, work_root=work_root, read_roots=read_roots, data_dir=data_dir
    )
    lines = ["【索引配置】（检索前必读；LLM 选 scopes / 工具；效率 5→1）"]
    menu = cat.get("search_menu") or []
    if menu:
        lines.append("检索菜单（优先高 efficiency；enabled=策略已开）：")
        for row in menu:
            if compact and not row.get("enabled") and not row.get("external"):
                continue
            flag = "开" if row.get("enabled") else ("外" if row.get("external") else "关")
            if compact:
                lines.append(
                    f"- e{row.get('efficiency')} {row.get('id')} [{flag}] {row.get('label')}"
                )
            else:
                lines.append(
                    f"- e{row.get('efficiency')} `{row.get('id')}` [{flag}] "
                    f"{row.get('label')}｜{row.get('capability')}｜{row.get('tool')}"
                )
    if not compact:
        for z in cat["zones"]:
            if z["id"] != "data":
                methods = ",".join(z.get("methods") or []) or "无"
                lines.append(f"- 区 {z['label']} `{z.get('path')}` methods=[{methods}]")
                continue
            lines.append(f"- 数据区 `{z.get('path')}`：")
            for b in z.get("businesses") or []:
                methods = ",".join(b.get("methods") or []) or (
                    "锁定" if b.get("locked") else "关"
                )
                flag = "searchable" if b.get("searchable") else "intent-only"
                lines.append(
                    f"  · {b['label']} [{methods}] {flag}"
                )
    lines.append(f"指引：{cat.get('search_guidance')}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated)"
    return text
