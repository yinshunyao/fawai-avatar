# term:自我进化  doc:doc/功能/自我进化/说明.md
"""C4 验收：E1 Skill 往返；E2 智能体禁改最少核/归档；可新建评测；默认 mock。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.capabilities import mock_plan_for_goal, validate_plan
from fawai_avatar.brain.planner import Brain
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.control.executor import Controller
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.skills.store import SkillStore, SkillValidationError, skills_prompt_block
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action
from fawai_avatar.workspace.fs import WorkspaceFs
from fawai_avatar.workspace.paths import WorkspacePathError


def _fail(name: str, detail: str) -> None:
    raise AssertionError(f"{name}: {detail}")


def _case_e1_skill(work: Path, data: Path) -> None:
    print("== E1 skill write/search ==")
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    os.environ["FAWAI_WORK_ROOT"] = str(work)
    os.environ["FAWAI_DATA_DIR"] = str(data)

    tools = BackgroundTools(work_root=work, data_dir=data, auto_version=False)
    bad = tools.execute(
        Action(
            kind="skill_write",
            args={"id": "bad_skill", "title": "", "when_to_use": "a", "steps": "b"},
        )
    )
    if bad.ok:
        _fail("E1-gate", "empty title should fail quality gate")

    goal = "写入 Skill id=c4_hello 沉淀可复用步骤"
    plan = mock_plan_for_goal(goal)
    assert plan.actions and plan.actions[0].kind == "skill_write", plan.to_dict()
    assert validate_plan(goal, plan) is None

    supervisor = Supervisor(str(data / "tasks_e1"))
    task_id = supervisor.create_task(goal)
    loop = WorkerLoop(
        supervisor,
        brain=Brain(),
        controller=Controller(dry_run=False, supervisor=supervisor),
        max_steps=4,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data, auto_version=False
    )
    out = loop.run(task_id)
    assert out.get("ok"), out

    store = SkillStore(data / "skills")
    skill = store.get("c4_hello")
    assert skill is not None, "skill c4_hello missing"
    hits = store.search("hello")
    assert any(h.get("id") == "c4_hello" for h in hits), hits
    block = skills_prompt_block(data / "skills")
    assert "c4_hello" in block
    print("E1 ok", json.dumps({"skill": skill.get("id"), "hits": len(hits)}, ensure_ascii=False))


def _case_e2_forbid_and_allow(work: Path, data: Path) -> None:
    print("== E2 forbid recovery core + archive; allow notes & new eval ==")
    # 预制归档与最少核假树（智能体禁改；人工/Cursor 仍可直接写盘）
    archive = work / "doc" / "版本" / "v0.1.0" / "评测结果.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("OLD ARCHIVE\n", encoding="utf-8")
    core_files = [
        work / "fawai_avatar" / "versioning" / "store.py",
        work / "fawai_avatar" / "runtime" / "releases.py",
        work / "fawai_avatar" / "runtime" / "keeper.py",
        work / "fawai_avatar" / "runtime" / "code_watch.py",
        work / "fawai_avatar" / "runtime" / "reload_gate.py",
        work / "fawai_avatar" / "runtime" / "port_guard.py",
        work / "fawai_avatar" / "workspace" / "paths.py",
    ]
    for path in core_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# do not touch\n", encoding="utf-8")

    fs = WorkspaceFs(work)
    try:
        fs.write("doc/版本/v0.1.0/评测结果.md", "HACKED\n")
        _fail("E2-archive", "existing archive should be rejected")
    except WorkspacePathError as exc:
        print("reject archive", str(exc))

    forbid_rels = [
        "fawai_avatar/versioning/store.py",
        "fawai_avatar/runtime/releases.py",
        "fawai_avatar/runtime/keeper.py",
        "fawai_avatar/runtime/code_watch.py",
        "fawai_avatar/runtime/reload_gate.py",
        "fawai_avatar/runtime/port_guard.py",
        "fawai_avatar/workspace/paths.py",
    ]
    for rel in forbid_rels:
        try:
            fs.write(rel, "HACKED\n")
            _fail(f"E2-core:{rel}", "recovery/keeper core should be rejected")
        except WorkspacePathError as exc:
            print("reject core", rel, str(exc))

    assert archive.read_text(encoding="utf-8") == "OLD ARCHIVE\n"
    for path in core_files:
        assert path.read_text(encoding="utf-8") == "# do not touch\n"

    # 允许非核路径（如 planner）与 notes
    allow_py = work / "fawai_avatar" / "brain" / "planner.py"
    allow_py.parent.mkdir(parents=True, exist_ok=True)
    allow_py.write_text("# ok to evolve\n", encoding="utf-8")
    fs.write("fawai_avatar/brain/planner.py", "# evolved\n")
    assert allow_py.read_text(encoding="utf-8") == "# evolved\n"

    tools = BackgroundTools(work_root=work, data_dir=data, auto_version=True)
    r = tools.execute(
        Action(kind="fs_write", args={"path": "notes/e2_ok.txt", "content": "ok\n"})
    )
    assert r.ok, r.to_dict()
    assert (work / "notes" / "e2_ok.txt").read_text(encoding="utf-8") == "ok\n"

    # 新建评测目录允许；旧归档不变
    r2 = tools.execute(
        Action(
            kind="fs_write",
            args={
                "path": "doc/版本/v0.1.2/评测结果.md",
                "content": "# v0.1.2\nNEW\n",
            },
        )
    )
    assert r2.ok, r2.to_dict()
    assert (work / "doc" / "版本" / "v0.1.2" / "评测结果.md").is_file()
    assert archive.read_text(encoding="utf-8") == "OLD ARCHIVE\n"
    print("E2 ok")


def _case_e2_via_background_reject(work: Path, data: Path) -> None:
    print("== E2 background tools surface forbid ==")
    tools = BackgroundTools(work_root=work, data_dir=data, auto_version=False)
    # ensure archive exists from previous case or recreate
    archive = work / "doc" / "版本" / "v0.1.0" / "评测结果.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        archive.write_text("OLD\n", encoding="utf-8")
    r = tools.execute(
        Action(
            kind="fs_write",
            args={"path": "doc/版本/v0.1.0/评测结果.md", "content": "nope"},
        )
    )
    assert not r.ok, r.to_dict()
    assert "evolution forbid" in (r.message or "")
    print("E2 tools reject ok")


def main(*, live: bool) -> None:
    if live:
        os.environ.pop("FAWAI_BRAIN_MOCK", None)
    else:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"

    with tempfile.TemporaryDirectory(prefix="fawai-c4-") as tmp:
        root = Path(tmp)
        work = root / "work"
        data = root / "data"
        work.mkdir()
        data.mkdir()
        _case_e1_skill(work, data)
        _case_e2_forbid_and_allow(work, data)
        _case_e2_via_background_reject(work, data)

        # 脏 Skill 质量门
        try:
            SkillStore(data / "skills2").write({"id": "bad id", "title": "t", "when_to_use": "w", "steps": "s"})
            _fail("gate-id", "invalid id accepted")
        except SkillValidationError:
            pass

    print("C4 acceptance E1/E2 ok")


if __name__ == "__main__":
    LIVE = os.environ.get("FAWAI_C4_LIVE", "").strip() in ("1", "true", "yes")
    main(live=LIVE)
