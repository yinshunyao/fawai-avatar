# term:自我进化  tool:skill_search|skill_write|skill_get  doc:doc/功能/自我进化/说明.md
# term:Skill 管理  doc:doc/功能/Skill管理/说明.md
# term:成败经验与检索极性  tool:skill_search  doc:doc/功能/成败经验与检索极性/说明.md
"""Skill / 教训文件库（E1）：规则质量门 + 检索摘要 + 启停。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_AVOID_TAGS = frozenset({"lesson", "failure", "avoid", "anti_pattern", "antipattern"})
_IMITATE_TAGS = frozenset({"success", "imitate", "skill"})


def skill_polarity(skill: dict[str, Any] | None) -> str:
    """imitate=可参考；avoid=勿当正例照抄。"""
    if not isinstance(skill, dict):
        return "imitate"
    tags = {
        str(t).strip().lower()
        for t in (skill.get("tags") or [])
        if str(t).strip()
    }
    sid = str(skill.get("id") or "").strip().lower()
    if tags & _AVOID_TAGS:
        return "avoid"
    if sid.startswith("lesson_") or sid.startswith("lesson-"):
        return "avoid"
    if tags & _IMITATE_TAGS:
        return "imitate"
    return "imitate"


def annotate_skill_hit(skill: dict[str, Any]) -> dict[str, Any]:
    out = dict(skill)
    out["polarity"] = skill_polarity(skill)
    # 便于检索展示：不改盘上字段
    tags = [str(t) for t in (skill.get("tags") or [])]
    outcome = ""
    for t in tags:
        tl = t.lower()
        if tl in {"success", "failure", "mixed"}:
            outcome = tl
            break
    if not outcome and out["polarity"] == "avoid":
        outcome = "failure"
    elif not outcome:
        outcome = "success"
    out["outcome"] = outcome
    return out


def partition_skill_hits(hits: list[dict[str, Any]]) -> dict[str, Any]:
    imitate: list[dict[str, Any]] = []
    avoid: list[dict[str, Any]] = []
    for raw in hits:
        item = annotate_skill_hit(raw)
        if item.get("polarity") == "avoid":
            avoid.append(item)
        else:
            imitate.append(item)
    return {
        "imitate": imitate,
        "avoid": avoid,
        "skills": imitate + avoid,
        "count": len(imitate) + len(avoid),
        "hint": "imitate=可参考成功路径；avoid=已知失败/教训，禁止当正例照抄 steps",
    }


class SkillValidationError(ValueError):
    """Skill 未过质量门。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _as_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "enabled"}:
        return True
    if s in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


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
    enabled = _as_bool(raw.get("enabled"), default=True)
    return {
        "id": skill_id,
        "title": title,
        "when_to_use": when,
        "steps": steps_text,
        "tags": tag_list,
        "source": source,
        "created_at": created,
        "enabled": enabled,
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


def _normalize_loaded(data: dict[str, Any]) -> dict[str, Any]:
    """读盘后补默认 enabled；不强制重跑完整校验（容忍历史文件）。"""
    out = dict(data)
    if "enabled" not in out:
        out["enabled"] = True
    else:
        out["enabled"] = _as_bool(out.get("enabled"), default=True)
    return out


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
        had_enabled = isinstance(payload, dict) and "enabled" in payload
        skill = validate_skill_payload(payload)
        # 覆盖写时若未显式带 enabled，保留原值
        existing = self.get(skill["id"], include_disabled=True)
        if existing is not None and not had_enabled:
            skill["enabled"] = _as_bool(existing.get("enabled"), default=True)
        path = self._path_for(skill["id"])
        path.write_text(
            json.dumps(skill, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return skill

    def get(
        self, skill_id: str, *, include_disabled: bool = False
    ) -> dict[str, Any] | None:
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
        if not isinstance(data, dict):
            return None
        skill = _normalize_loaded(data)
        if not include_disabled and not skill.get("enabled", True):
            return None
        return skill

    def list_all(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("id"):
                skill = _normalize_loaded(data)
                if include_disabled or skill.get("enabled", True):
                    out.append(skill)
        return out

    def delete(self, skill_id: str) -> bool:
        sid = str(skill_id or "").strip().lower()
        if not sid:
            return False
        path = self._path_for(sid)
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def set_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any] | None:
        skill = self.get(skill_id, include_disabled=True)
        if skill is None:
            return None
        skill["enabled"] = bool(enabled)
        path = self._path_for(str(skill["id"]))
        path.write_text(
            json.dumps(skill, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return skill

    def search(
        self, query: str, *, limit: int = 10, include_disabled: bool = False
    ) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        limit = max(1, min(int(limit), 50))
        scored: list[tuple[int, dict[str, Any]]] = []
        for skill in self.list_all(include_disabled=include_disabled):
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
        return [annotate_skill_hit(s) for _, s in scored[:limit]]

    def search_polarized(
        self, query: str, *, limit: int = 10, include_disabled: bool = False
    ) -> dict[str, Any]:
        hits = self.search(query, limit=limit, include_disabled=include_disabled)
        out = partition_skill_hits(hits)
        out["query"] = query
        return out


def skills_prompt_block(skills_dir: str | Path | None = None) -> str:
    """供大脑系统提示注入的 Skill 摘要（仅 enabled；imitate/avoid 分栏）。"""
    if skills_dir is None:
        from fawai_avatar.config import RuntimeConfig

        skills_dir = Path(RuntimeConfig.from_env().data_dir) / "skills"
    store = SkillStore(skills_dir)
    items = [annotate_skill_hit(s) for s in store.list_all(include_disabled=False)]
    if not items:
        return "当前 Skill 库为空；可用 skill_write 沉淀可复用步骤，skill_search 检索。"
    imitate = [s for s in items if s.get("polarity") != "avoid"]
    avoid = [s for s in items if s.get("polarity") == "avoid"]
    lines = [
        "可复用 Skill / 教训（skill_search 分栏；avoid 禁止当正例照抄）：",
        "【imitate·可参考】",
    ]
    if imitate:
        for skill in imitate[:12]:
            sid = skill.get("id")
            title = skill.get("title")
            when = skill.get("when_to_use")
            src = str(skill.get("source") or "").strip()
            suffix = f" · source={src}" if src else ""
            lines.append(f"- {sid}: {title} — {when}{suffix}")
        if len(imitate) > 12:
            lines.append(f"…imitate 共 {len(imitate)} 条，用 skill_search 缩小范围")
    else:
        lines.append("- （无）")
    lines.append("【avoid·勿照抄】")
    if avoid:
        for skill in avoid[:12]:
            sid = skill.get("id")
            title = skill.get("title")
            when = skill.get("when_to_use")
            src = str(skill.get("source") or "").strip()
            suffix = f" · source={src}" if src else ""
            lines.append(f"- {sid}: {title} — {when}{suffix}")
        if len(avoid) > 12:
            lines.append(f"…avoid 共 {len(avoid)} 条，用 skill_search 缩小范围")
    else:
        lines.append("- （无）")
    return "\n".join(lines)
