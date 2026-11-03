# term:自我进化  tool:skill_search|skill_write|skill_get  doc:doc/功能/自我进化/说明.md
"""Skill / 教训文件库（E1）：规则质量门 + 检索摘要。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class SkillValidationError(ValueError):
    """Skill 未过质量门。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_skill_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """规则质量门：缺字段/过长 → 抛错。返回规范化 dict。"""
    skill_id = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.match(skill_id):
        raise SkillValidationError(
            "skill id must match [a-z0-9][a-z0-9_-]{1,63}"
        )
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > 120:
        raise SkillValidationError("skill title required, max 120 chars")
    when = str(raw.get("when_to_use") or "").strip()
    if not when or len(when) > 500:
        raise SkillValidationError("skill when_to_use required, max 500 chars")
    steps = raw.get("steps")
    if isinstance(steps, list):
        steps_text = "\n".join(str(x).strip() for x in steps if str(x).strip())
    else:
        steps_text = str(steps or "").strip()
    if not steps_text or len(steps_text) > 8000:
        raise SkillValidationError("skill steps required, max 8000 chars")
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise SkillValidationError("skill tags must be a list")
    tag_list = [str(t).strip() for t in tags if str(t).strip()][:20]
    source = str(raw.get("source") or "").strip()[:200]
    created = str(raw.get("created_at") or "").strip() or _utc_now()
    return {
        "id": skill_id,
        "title": title,
        "when_to_use": when,
        "steps": steps_text,
        "tags": tag_list,
        "source": source,
        "created_at": created,
    }


def builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


def ensure_builtin_skills(skills_dir: str | Path) -> list[str]:
    """把内置 lesson/skill 拷到 data/skills（已存在则不覆盖用户改稿）。"""
    dest_root = Path(skills_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    src_root = builtin_skills_dir()
    if not src_root.is_dir():
        return []
    installed: list[str] = []
    for src in sorted(src_root.glob("*.json")):
        dest = dest_root / src.name
        if dest.is_file():
            continue
        try:
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            installed.append(src.stem)
        except OSError:
            continue
    return installed


class SkillStore:
    def __init__(self, skills_dir: str | Path) -> None:
        self._root = Path(skills_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        ensure_builtin_skills(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, skill_id: str) -> Path:
        return self._root / f"{skill_id}.json"

    def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        skill = validate_skill_payload(payload)
        path = self._path_for(skill["id"])
        path.write_text(
            json.dumps(skill, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return skill

    def get(self, skill_id: str) -> dict[str, Any] | None:
        sid = str(skill_id or "").strip().lower()
        if not sid:
            return None
        path = self._path_for(sid)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def list_all(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        return out

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        limit = max(1, min(int(limit), 50))
        scored: list[tuple[int, dict[str, Any]]] = []
        for skill in self.list_all():
            blob = " ".join(
                [
                    str(skill.get("id") or ""),
                    str(skill.get("title") or ""),
                    str(skill.get("when_to_use") or ""),
                    str(skill.get("steps") or ""),
                    " ".join(str(t) for t in (skill.get("tags") or [])),
                ]
            ).lower()
            if not q:
                scored.append((0, skill))
                continue
            score = 0
            for token in q.split():
                if token and token in blob:
                    score += 1
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: (-x[0], str(x[1].get("id") or "")))
        return [s for _, s in scored[:limit]]


def skills_prompt_block(skills_dir: str | Path | None = None) -> str:
    """供大脑系统提示注入的 Skill 摘要。"""
    if skills_dir is None:
        from fawai_avatar.config import RuntimeConfig

        skills_dir = Path(RuntimeConfig.from_env().data_dir) / "skills"
    store = SkillStore(skills_dir)
    items = store.list_all()
    if not items:
        return "当前 Skill 库为空；可用 skill_write 沉淀可复用步骤，skill_search 检索。"
    lines = ["可复用 Skill（规划时可 skill_search / skill_get 取全文）："]
    for skill in items[:20]:
        sid = skill.get("id")
        title = skill.get("title")
        when = skill.get("when_to_use")
        lines.append(f"- {sid}: {title} — {when}")
    if len(items) > 20:
        lines.append(f"…共 {len(items)} 条，用 skill_search 缩小范围")
    return "\n".join(lines)
