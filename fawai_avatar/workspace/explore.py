# term:探仓  tool:explore_search  doc:doc/功能/编码闭环/说明.md
"""只读并行探仓：进程内线程池跑多路 grep；符号索引默认关闭（可 opt-in）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.code_index import CodeIndex
from fawai_avatar.workspace.fs import WorkspaceFs, search_root_too_broad
from fawai_avatar.workspace.paths import normalize_work_root, resolve_for_read

_EXPLORE_QUERY_ALIASES = ("query", "pattern", "q")
# 默认收窄到常见源码/配置，避免大仓数据目录拖慢 rg
_DEFAULT_SOURCE_GLOB = (
    "*.{py,ts,tsx,js,jsx,vue,go,rs,java,kt,c,h,cc,cpp,hpp,"
    "html,htm,css,"
    "md,yml,yaml,json,toml,sh,txt}"
)


def coerce_explore_search_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """归一 explore_search 参数：queries 列表 + symbols_query + include_symbols。

    兼容模型常见误写：queries 为字符串；单数字段 query / pattern / q；
    以及上述字段为列表。执行侧与规划 apply_capability_defaults 共用。
    """
    src = dict(args or {})
    qlist: list[str] = []

    def _push(raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, list):
            for item in raw:
                s = str(item).strip()
                if s and s not in qlist:
                    qlist.append(s)
            return
        s = str(raw).strip()
        if s and s not in qlist:
            qlist.append(s)

    _push(src.get("queries"))
    for key in _EXPLORE_QUERY_ALIASES:
        _push(src.get(key))

    symbols_query = str(src.get("symbols_query") or "").strip()
    include_raw = src.get("include_symbols")
    if include_raw is None:
        include_symbols = False
    elif isinstance(include_raw, str):
        include_symbols = include_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        include_symbols = bool(include_raw)

    out = dict(src)
    out["queries"] = qlist
    out["symbols_query"] = symbols_query
    out["include_symbols"] = include_symbols
    for key in _EXPLORE_QUERY_ALIASES:
        out.pop(key, None)
    return out


def effective_explore_glob(glob: str | None) -> str:
    """`*` / 空 → 源码默认 glob；显式其它值原样保留。"""
    g = (glob or "").strip() or "*"
    if g in {"*", "**", "**/*"}:
        return _DEFAULT_SOURCE_GLOB
    return g


def explore_search(
    work_root: str | Path,
    *,
    queries: list[str] | None = None,
    path: str = ".",
    glob: str = "*",
    symbols_query: str = "",
    include_symbols: bool = False,
    max_workers: int = 4,
    head_limit: int = 20,
    context_before: int = 0,
    context_after: int = 0,
    code_index: CodeIndex | None = None,
    allow_broad: bool = False,
) -> dict[str, Any]:
    """并行探仓。

    默认只跑 grep（不再每词自动 symbols / ensure_index）。
    设 include_symbols=True 时每词额外 symbols；symbols_query 单独走符号通道。
    """
    root = normalize_work_root(work_root)
    fs = WorkspaceFs(root)
    code = code_index or CodeIndex(root)
    qlist = [str(q).strip() for q in (queries or []) if str(q).strip()]
    extra_sym = (symbols_query or "").strip()
    want_symbols = bool(include_symbols) or bool(extra_sym)
    if not qlist and not extra_sym:
        raise ValueError(
            "explore_search requires queries or symbols_query "
            "(also accepts query/pattern/q; queries may be a string)"
        )
    before = max(0, min(int(context_before), 5))
    after = max(0, min(int(context_after), 5))
    limit = max(1, min(int(head_limit), 100))
    glob_eff = effective_explore_glob(glob)
    # 有正文 grep 任务时，整 work_root / 浅根与 fs_grep 同闸
    if qlist:
        base = resolve_for_read(root, path or ".", read_roots=fs.read_roots)
        broad = search_root_too_broad(
            base,
            work_root=root,
            kind="explore_search",
            pattern=qlist[0],
            allow_broad=allow_broad,
        )
        if broad:
            raise ValueError(broad)

    symbols_on = True
    try:
        from fawai_avatar.config import code_index_enabled

        symbols_on = bool(code_index_enabled(default=True))
    except Exception:  # noqa: BLE001
        symbols_on = True

    # 仅在需要符号通道且记忆配置开启时建索引（默认跳过，避免大树 AST 拖慢）
    if want_symbols and symbols_on:
        try:
            code.ensure_index(path)
        except Exception:  # noqa: BLE001
            pass

    jobs: list[tuple[str, str]] = []
    for q in qlist:
        jobs.append(("grep", q))
        if include_symbols and symbols_on:
            jobs.append(("symbols", q))
    if symbols_on and extra_sym and (not include_symbols or extra_sym not in qlist):
        jobs.append(("symbols", extra_sym))

    results: list[dict[str, Any]] = []
    if want_symbols and not symbols_on:
        results.append(
            {
                "kind": "symbols",
                "query": extra_sym or (qlist[0] if qlist else ""),
                "hits": [],
                "count": 0,
                "disabled": True,
                "message": "code index disabled (FAWAI_CODE_INDEX=0)",
            }
        )

    def _run(kind: str, payload: str) -> dict[str, Any]:
        if kind == "grep":
            hits = fs.grep(
                payload,
                path=path,
                glob=glob_eff,
                head_limit=limit,
                context_before=before,
                context_after=after,
                allow_broad=allow_broad,
            )
            return {"kind": "grep", "query": payload, "hits": hits, "count": len(hits)}
        hits = code.symbols(path, query=payload, head_limit=limit)
        return {"kind": "symbols", "query": payload, "hits": hits, "count": len(hits)}

    workers = max(1, min(max_workers, len(jobs) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_run, kind, payload): (kind, payload) for kind, payload in jobs}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                kind, payload = futs[fut]
                results.append(
                    {
                        "kind": kind,
                        "query": payload,
                        "ok": False,
                        "error": str(exc),
                        "hits": [],
                        "count": 0,
                    }
                )
    return {
        "ok": True,
        "path": path,
        "glob": glob_eff,
        "include_symbols": bool(include_symbols),
        "results": results,
        "workers": workers,
    }
