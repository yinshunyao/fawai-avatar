# term:脚本铲子生命周期  doc:doc/功能/脚本铲子生命周期/说明.md
# term:成败经验与检索极性  doc:doc/功能/成败经验与检索极性/说明.md
"""HTTP helpers: shovel list / get / scores+outcome edit (PWA 技能工具·工具表)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.workspace.shovel_ops import (
    ShovelOpsError,
    get_shovel,
    list_shovels,
    resolve_shovels_root,
    update_shovel_scores,
)
from fawai_avatar.workspace.shovel_outcome import enrich_shovel_hit, set_outcome


def _ctx() -> tuple[str, str, Path]:
    cfg = RuntimeConfig.from_env()
    data_dir = str(Path(cfg.data_dir).expanduser().resolve())
    work_root = str(Path(cfg.work_root).expanduser().resolve())
    root = resolve_shovels_root(work_root)
    return data_dir, work_root, root


def shovels_list() -> dict[str, Any]:
    data_dir, work_root, root = _ctx()
    rows = list_shovels(work_root, shovels_root=root)
    items = [enrich_shovel_hit(r, data_dir) for r in rows]
    # 列表不带全文；附短预览便于前端本地搜代码
    for item, raw in zip(items, rows):
        try:
            text = Path(str(raw.get("abs_path") or "")).read_text(encoding="utf-8")
        except OSError:
            text = ""
        item["code_preview"] = text[:4000]
        item.pop("abs_path", None)
    return {
        "ok": True,
        "shovels": items,
        "count": len(items),
        "root": str(root),
    }


def shovels_get(shovel_id: str) -> dict[str, Any]:
    data_dir, work_root, root = _ctx()
    row = get_shovel(
        shovel_id,
        work_root=work_root,
        shovels_root=root,
        data_dir=data_dir,
        include_code=True,
    )
    if row is None:
        return {"ok": False, "error": f"shovel not found: {shovel_id}"}
    row = dict(row)
    row.pop("abs_path", None)
    return {"ok": True, "shovel": row, "root": str(root)}


def shovels_update(shovel_id: str, body: dict[str, Any] | None) -> dict[str, Any]:
    """手工改设计三维分与/或运行成败维。"""
    payload = dict(body or {})
    sid = str(shovel_id or payload.get("id") or "").strip()
    if not sid:
        return {"ok": False, "error": "shovel id required"}
    data_dir, work_root, root = _ctx()
    try:
        scores_touched = any(
            k in payload for k in ("completeness", "generality", "efficiency")
        )
        if scores_touched:
            update_shovel_scores(
                sid,
                completeness=payload.get("completeness"),
                generality=payload.get("generality"),
                efficiency=payload.get("efficiency"),
                work_root=work_root,
                shovels_root=root,
                data_dir=data_dir,
            )
        outcome_body = payload.get("outcome")
        if isinstance(outcome_body, dict) or any(
            k in payload for k in ("calls_ok", "calls_fail", "last_fail_one_liner")
        ):
            src = outcome_body if isinstance(outcome_body, dict) else payload
            calls_ok = src.get("calls_ok")
            calls_fail = src.get("calls_fail")
            fail_line = src.get("last_fail_one_liner")
            set_outcome(
                data_dir,
                sid,
                calls_ok=None if calls_ok is None else int(calls_ok),
                calls_fail=None if calls_fail is None else int(calls_fail),
                last_fail_one_liner=(
                    None if fail_line is None else str(fail_line)
                ),
            )
    except (ShovelOpsError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    row = get_shovel(
        sid,
        work_root=work_root,
        shovels_root=root,
        data_dir=data_dir,
        include_code=True,
    )
    if row is None:
        return {"ok": False, "error": f"shovel not found: {sid}"}
    row = dict(row)
    row.pop("abs_path", None)
    return {"ok": True, "shovel": row, "root": str(root)}
