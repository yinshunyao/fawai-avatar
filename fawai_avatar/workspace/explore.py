# term:探仓  tool:explore_search  doc:doc/功能/编码闭环/说明.md
"""只读并行探仓：进程内线程池跑多路 grep + 符号索引借用。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.code_index import CodeIndex
from fawai_avatar.workspace.fs import WorkspaceFs
from fawai_avatar.workspace.paths import normalize_work_root


def explore_search(
    work_root: str | Path,
    *,
    queries: list[str] | None = None,
    path: str = ".",
    glob: str = "*",
    symbols_query: str = "",
    max_workers: int = 4,
    head_limit: int = 30,
    code_index: CodeIndex | None = None,
) -> dict[str, Any]:
    root = normalize_work_root(work_root)
    fs = WorkspaceFs(root)
    code = code_index or CodeIndex(root)
    qlist = [str(q).strip() for q in (queries or []) if str(q).strip()]
    extra_sym = (symbols_query or "").strip()
    if not qlist and not extra_sym:
        raise ValueError("explore_search requires queries or symbols_query")

    # 懒建索引，供并行 symbols 借用
    try:
        code.ensure_index(path)
    except Exception:  # noqa: BLE001
        pass

    jobs: list[tuple[str, str]] = []
    for q in qlist:
        jobs.append(("grep", q))
        jobs.append(("symbols", q))
    if extra_sym and extra_sym not in qlist:
        jobs.append(("symbols", extra_sym))

    results: list[dict[str, Any]] = []

    def _run(kind: str, payload: str) -> dict[str, Any]:
        if kind == "grep":
            hits = fs.grep(
                payload,
                path=path,
                glob=glob,
                head_limit=head_limit,
                context_before=0,
                context_after=0,
            )
            return {"kind": "grep", "query": payload, "hits": hits, "count": len(hits)}
        hits = code.symbols(path, query=payload, head_limit=head_limit)
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
                    {"kind": kind, "query": payload, "ok": False, "error": str(exc), "hits": [], "count": 0}
                )
    return {"ok": True, "path": path, "results": results, "workers": workers}
