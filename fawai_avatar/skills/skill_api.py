# term:Skill 管理  doc:doc/功能/Skill管理/说明.md
"""HTTP helpers for Skill CRUD / upload (used by lan_gateway)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.skills.store import (
    SkillStore,
    SkillValidationError,
    validate_skill_payload,
)


def _store() -> SkillStore:
    root = Path(RuntimeConfig.from_env().data_dir) / "skills"
    return SkillStore(root)


def skills_list() -> dict[str, Any]:
    store = _store()
    items = store.list_all(include_disabled=True)
    return {"ok": True, "skills": items, "count": len(items), "root": str(store.root)}


def skills_get(skill_id: str) -> dict[str, Any]:
    store = _store()
    skill = store.get(skill_id, include_disabled=True)
    if skill is None:
        return {"ok": False, "error": f"skill not found: {skill_id}"}
    return {"ok": True, "skill": skill}


def skills_upsert(skill_id: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body or {})
    sid = str(skill_id or payload.get("id") or "").strip().lower()
    if not sid:
        return {"ok": False, "error": "skill id required"}
    payload["id"] = sid
    try:
        skill = _store().write(payload)
    except SkillValidationError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "skill": skill}


def skills_enable(skill_id: str, *, enabled: bool) -> dict[str, Any]:
    skill = _store().set_enabled(skill_id, enabled)
    if skill is None:
        return {"ok": False, "error": f"skill not found: {skill_id}"}
    return {"ok": True, "skill": skill}


def skills_delete(skill_id: str) -> dict[str, Any]:
    ok = _store().delete(skill_id)
    if not ok:
        return {"ok": False, "error": f"skill not found: {skill_id}"}
    return {"ok": True, "deleted": skill_id}


def skills_upload(raw: Any) -> dict[str, Any]:
    """Accept one skill dict, a list, or JSON text."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"invalid json: {exc}"}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"invalid json: {exc}"}
    items: list[Any]
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return {"ok": False, "error": "upload body must be object or array"}
    written: list[dict[str, Any]] = []
    errors: list[str] = []
    store = _store()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"[{i}] not an object")
            continue
        try:
            validate_skill_payload(item)
            written.append(store.write(item))
        except SkillValidationError as exc:
            errors.append(f"[{i}] {exc}")
    return {
        "ok": len(errors) == 0 and len(written) > 0,
        "written": written,
        "errors": errors,
        "count": len(written),
    }
