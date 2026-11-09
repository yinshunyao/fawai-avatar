# term:脚本铲子生命周期  tool:shovel_search  doc:doc/功能/脚本铲子生命周期/说明.md
# term:成败经验与检索极性  tool:shovel_search  doc:doc/功能/成败经验与检索极性/说明.md
"""检索 fawai_shovels 文首 meta（复用 scaffold/catalog 解析）。"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.shovel_outcome import enrich_shovel_hit


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


def _read_code(abs_path: str | Path, *, max_chars: int = 0) -> str:
    path = Path(abs_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if max_chars and max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def get_shovel(
    shovel_id: str,
    *,
    work_root: str | Path | None = None,
    shovels_root: str | Path | None = None,
    data_dir: str | Path | None = None,
    include_code: bool = True,
) -> dict[str, Any] | None:
    sid = str(shovel_id or "").strip().lower().replace("-", "_")
    if not sid:
        return None
    for row in list_shovels(work_root, shovels_root=shovels_root):
        rid = str(row.get("id") or "").strip().lower().replace("-", "_")
        if rid != sid:
            continue
        out = enrich_shovel_hit(row, data_dir)
        if include_code:
            out["code"] = _read_code(str(row.get("abs_path") or ""))
        return out
    return None


def _patch_header_scores(
    path: Path,
    *,
    completeness: str | int | None = None,
    generality: str | int | None = None,
    efficiency: str | int | None = None,
) -> dict[str, str]:
    """改写文首设计三维分；返回更新后的 meta。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    begin = "# --- shovel ---"
    end = "# --- end-shovel ---"
    start_i = None
    end_i = None
    for i, line in enumerate(lines[:120]):
        if line.strip() == begin:
            start_i = i
            break
    if start_i is None:
        raise ShovelOpsError(f"shovel header missing: {path}")
    for j in range(start_i + 1, min(len(lines), start_i + 80)):
        if lines[j].strip() == end:
            end_i = j
            break
    if end_i is None:
        raise ShovelOpsError(f"shovel header end missing: {path}")

    updates: dict[str, str] = {}
    for key, raw in (
        ("completeness", completeness),
        ("generality", generality),
        ("efficiency", efficiency),
    ):
        if raw is None or str(raw).strip() == "":
            continue
        try:
            n = int(str(raw).strip())
        except ValueError as exc:
            raise ShovelOpsError(f"{key} must be 1–5") from exc
        if n < 1 or n > 5:
            raise ShovelOpsError(f"{key} must be 1–5")
        updates[key] = str(n)
    if not updates:
        meta = parse_header_from_text("".join(lines))
        return meta or {}

    seen: set[str] = set()
    for i in range(start_i + 1, end_i):
        s = lines[i].strip()
        if not s.startswith("#"):
            continue
        body = s[1:].strip()
        if ":" not in body:
            continue
        key, _, _ = body.partition(":")
        key = key.strip().lower()
        if key in updates:
            # 保留原缩进与注释风格
            prefix = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            nl = "\n" if lines[i].endswith("\n") else ""
            lines[i] = f"{prefix}# {key}: {updates[key]}{nl}"
            seen.add(key)
    missing = [k for k in updates if k not in seen]
    if missing:
        # 插在 end 行前
        insert_at = end_i
        chunk = [f"# {k}: {updates[k]}\n" for k in missing]
        lines[insert_at:insert_at] = chunk
    path.write_text("".join(lines), encoding="utf-8")
    meta = parse_header_from_text("".join(lines))
    return meta or {}


def parse_header_from_text(text: str) -> dict[str, str] | None:
    """轻量解析文首（与 catalog.parse_header 字段口径一致）。"""
    begin = "# --- shovel ---"
    end = "# --- end-shovel ---"
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines[:80]):
        if line.strip() == begin:
            start = i
            break
    if start is None:
        return None
    meta: dict[str, str] = {}
    for line in lines[start + 1 : start + 80]:
        s = line.strip()
        if s == end:
            return meta
        if not s.startswith("#"):
            return meta if meta else None
        body = s[1:].strip()
        if not body or body.startswith("---") or ":" not in body:
            continue
        key, _, val = body.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key:
            meta[key] = val
    return meta if meta else None


def update_shovel_scores(
    shovel_id: str,
    *,
    completeness: str | int | None = None,
    generality: str | int | None = None,
    efficiency: str | int | None = None,
    work_root: str | Path | None = None,
    shovels_root: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    row = get_shovel(
        shovel_id,
        work_root=work_root,
        shovels_root=shovels_root,
        data_dir=data_dir,
        include_code=False,
    )
    if row is None:
        raise ShovelOpsError(f"shovel not found: {shovel_id}")
    abs_path = Path(str(row.get("abs_path") or ""))
    if not abs_path.is_file():
        raise ShovelOpsError(f"shovel file missing: {abs_path}")
    meta = _patch_header_scores(
        abs_path,
        completeness=completeness,
        generality=generality,
        efficiency=efficiency,
    )
    refreshed = get_shovel(
        shovel_id,
        work_root=work_root,
        shovels_root=shovels_root,
        data_dir=data_dir,
        include_code=True,
    )
    if refreshed is None:
        raise ShovelOpsError(f"shovel vanished after patch: {shovel_id}")
    for k in ("completeness", "generality", "efficiency"):
        if k in meta:
            refreshed[k] = meta[k]
    return refreshed


def search_shovels(
    query: str,
    *,
    work_root: str | Path | None = None,
    shovels_root: str | Path | None = None,
    data_dir: str | Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    q = (query or "").strip().lower()
    limit = max(1, min(int(limit or 20), 50))
    all_rows = list_shovels(work_root, shovels_root=shovels_root)
    if not q:
        scored_rows = list(all_rows[:limit])
    else:
        tokens = [t for t in re.split(r"[\s,/|]+", q) if t]
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in all_rows:
            code_preview = _read_code(str(row.get("abs_path") or ""), max_chars=4000)
            outcome = enrich_shovel_hit(row, data_dir).get("outcome") or {}
            blob = " ".join(
                [
                    str(row.get(k) or "")
                    for k in (
                        "id",
                        "title",
                        "when_to_use",
                        "path",
                        "tags",
                        "invoke",
                        "runtime",
                        "completeness",
                        "generality",
                        "efficiency",
                    )
                ]
                + [
                    str(outcome.get("rate") or ""),
                    str(outcome.get("last_fail_one_liner") or ""),
                    code_preview,
                ]
            ).lower()
            score = 0
            for t in tokens:
                if t in blob:
                    score += 2 if t in (row.get("id") or "").lower() else 1
            if score:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], x[1].get("id") or ""))
        scored_rows = [r for _, r in scored[:limit]]
    hits = [enrich_shovel_hit(r, data_dir) for r in scored_rows]
    root = (
        Path(shovels_root).expanduser().resolve()
        if shovels_root
        else resolve_shovels_root(work_root)
    )
    poor = [
        h
        for h in hits
        if str((h.get("outcome") or {}).get("rate") or "") == "poor"
    ]
    mixed = [
        h
        for h in hits
        if str((h.get("outcome") or {}).get("rate") or "") == "mixed"
    ]
    return {
        "query": query,
        "shovels_root": str(root),
        "total_indexed": len(all_rows),
        "hits": hits,
        "count": len(hits),
        "poor_ids": [str(h.get("id") or "") for h in poor if h.get("id")],
        "mixed_ids": [str(h.get("id") or "") for h in mixed if h.get("id")],
        "hint": (
            "outcome.rate 为运行成败维（good/mixed/poor/unknown），"
            "与文首 completeness/generality/efficiency 设计三维分不同；"
            "poor/近失败勿无视当前 goal 原样照抄 invoke"
        ),
    }
