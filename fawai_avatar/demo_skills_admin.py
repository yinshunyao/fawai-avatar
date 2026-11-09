# term:Skill 管理  doc:doc/功能/Skill管理/说明.md
"""Skill 管理冒烟：CRUD / 启停 / 上传 / 规划注入过滤。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fawai_avatar.skills.skill_api import (
    skills_delete,
    skills_enable,
    skills_get,
    skills_list,
    skills_upload,
    skills_upsert,
)
from fawai_avatar.skills.store import SkillStore, skills_prompt_block


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "skills"
        store = SkillStore(root)
        # builtin may copy; write a custom skill
        skill = store.write(
            {
                "id": "demo_skill_admin",
                "title": "Demo skill",
                "when_to_use": "unit test only",
                "steps": "1. write\n2. search",
                "tags": ["demo"],
                "source": "demo_skills_admin",
            }
        )
        assert skill["enabled"] is True
        assert store.get("demo_skill_admin") is not None
        store.set_enabled("demo_skill_admin", False)
        assert store.get("demo_skill_admin") is None
        assert store.get("demo_skill_admin", include_disabled=True) is not None
        hits = store.search("demo", include_disabled=False)
        assert all(h.get("id") != "demo_skill_admin" for h in hits)
        block = skills_prompt_block(root)
        assert "demo_skill_admin" not in block
        store.set_enabled("demo_skill_admin", True)
        assert store.delete("demo_skill_admin") is True
        assert store.get("demo_skill_admin", include_disabled=True) is None

    # API helpers against real data_dir skills — use isolated via monkeypatch env
    import os

    with tempfile.TemporaryDirectory() as tmp2:
        os.environ["FAWAI_DATA_DIR"] = tmp2
        # clear RuntimeConfig cache if any — from_env reads env each time
        up = skills_upload(
            {
                "id": "api_skill_x",
                "title": "API skill",
                "when_to_use": "api test",
                "steps": "step one",
            }
        )
        assert up.get("ok"), up
        listed = skills_list()
        assert any(s["id"] == "api_skill_x" for s in listed.get("skills") or [])
        got = skills_get("api_skill_x")
        assert got.get("ok")
        skills_enable("api_skill_x", enabled=False)
        got2 = skills_get("api_skill_x")
        assert got2["skill"]["enabled"] is False
        skills_upsert(
            "api_skill_x",
            {
                "id": "api_skill_x",
                "title": "API skill 2",
                "when_to_use": "api test",
                "steps": "step two",
                "enabled": True,
            },
        )
        assert skills_delete("api_skill_x").get("ok")
        print("demo_skills_admin: OK", json.dumps({"tmp": tmp2}))


if __name__ == "__main__":
    main()
