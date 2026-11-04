# term:脚本铲子生命周期  tool:shovel_search  doc:doc/功能/脚本铲子生命周期/说明.md
"""检索 fawai_shovels 文首 meta（复用 scaffold/catalog 解析）。"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any


class ShovelOpsError(ValueError):
    """Shovels root / catalog missing."""


def resolve_shovels_root(work_root: str | Path | None = None) -> Path:
    for key in ("FAWAI_SHOVELS_ROOT", "HIPP_TOOLS_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            p = Path(raw).expanduser().resolve()
            if p.is_dir():
                return p
            raise ShovelOpsError(f"{key} is not a directory: {raw}")
    candidates: list[Path] = []
    if work_root:
        root = Path(work_root).expanduser().resolve()
        candidates.append(root / "fawai_shovels")
        candidates.append(root)
    # 本包所在仓根：fawai_avatar/../fawai_shovels
    pkg = Path(__file__).resolve().parents[2]
    candidates.append(pkg / "fawai_shovels")
    candidates.append(pkg)
    for c in candidates:
        if (c / "scaffold" / "catalog.py").is_file():
            return c.resolve()
        if c.name == "fawai_shovels" and c.is_dir():
            return c.resolve()
    raise ShovelOpsError(
        "fawai_shovels not found; set FAWAI_SHOVELS_ROOT or run under repo with fawai_shovels/"
    )


def _load_catalog(shovels_root: Path) -> Any:
    path = shovels_root / "scaffold" / "catalog.py"
    if not path.is_file():
        raise ShovelOpsError(f"catalog missing: {path}")
    spec = importlib.util.spec_from_file_location(
        "fawai_shovel_catalog_dyn", path
    )
    if spec is None or spec.loader is None:
        raise ShovelOpsError(f"cannot load catalog: {path}")
    mod = importlib.util.module_from_spec(spec)
    # catalog.py 用 Path(__file__).parents[1] 作 ROOT；加载后与磁盘一致
    spec.loader.exec_module(mod)
    return mod


def list_shovels(
    work_root: str | Path | None = None,
    *,
    shovels_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = (
        Path(shovels_root).expanduser().resolve()
        if shovels_root
        else resolve_shovels_root(work_root)
    )
    mod = _load_catalog(root)
    # catalog 模块 ROOT 固定为文件旁；若 env 指向别处，直接扫该根
    scan_root = root
    rows: list[dict[str, Any]] = []
    scan_dirs = getattr(mod, "SCAN_DIRS", ("shell", "python", "media", "scaffold"))
    skip = getattr(mod, "SKIP_NAMES", {"TEMPLATE.py", "TEMPLATE.sh"})
    parse_header = mod.parse_header
    for name in scan_dirs:
        d = scan_root / name
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            if p.name in skip or p.name.startswith("."):
                continue
            if p.suffix not in {".py", ".sh"}:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = parse_header(text)
            if not meta:
                continue
            rel = p.relative_to(scan_root).as_posix()
            rows.append(
                {
                    "id": meta.get("id", ""),
                    "title": meta.get("title", ""),
                    "when_to_use": meta.get("when_to_use", ""),
                    "path": rel,
                    "abs_path": str(p),
                    "runtime": meta.get("runtime", ""),
                    "kind": meta.get("kind", ""),
                    "platforms": meta.get("platforms", ""),
                    "tags": meta.get("tags", ""),
                    "invoke": meta.get("invoke", ""),
                    "completeness": meta.get("completeness", ""),
                    "generality": meta.get("generality", ""),
                    "efficiency": meta.get("efficiency", ""),
                    "privacy": meta.get("privacy", ""),
                }
            )
    return rows


def search_shovels(
    query: str,
    *,
    work_root: str | Path | None = None,
    shovels_root: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    q = (query or "").strip().lower()
    limit = max(1, min(int(limit or 20), 50))
    all_rows = list_shovels(work_root, shovels_root=shovels_root)
    if not q:
        hits = all_rows[:limit]
    else:
        tokens = [t for t in re.split(r"[\s,/|]+", q) if t]
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in all_rows:
            blob = " ".join(
                str(row.get(k) or "")
                for k in (
                    "id",
                    "title",
                    "when_to_use",
                    "path",
                    "tags",
                    "invoke",
                    "runtime",
                )
            ).lower()
            score = 0
            for t in tokens:
                if t in blob:
                    score += 2 if t in (row.get("id") or "").lower() else 1
            if score:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], x[1].get("id") or ""))
        hits = [r for _, r in scored[:limit]]
    root = (
        Path(shovels_root).expanduser().resolve()
        if shovels_root
        else resolve_shovels_root(work_root)
    )
    return {
        "query": query,
        "shovels_root": str(root),
        "total_indexed": len(all_rows),
        "hits": hits,
        "count": len(hits),
    }
