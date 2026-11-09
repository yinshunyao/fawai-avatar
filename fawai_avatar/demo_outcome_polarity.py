# term:成败经验与检索极性  doc:doc/功能/成败经验与检索极性/说明.md
"""成败维 + Skill 极性 + 差评软闸冒烟（无真实 LLM）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.shovel_lifecycle import (
    OUTCOME_GATE_MARKER,
    format_outcome_gate_message,
    should_inject_outcome_gate,
)
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.skills.store import (
    SkillStore,
    skill_polarity,
    skills_prompt_block,
)
from fawai_avatar.types import Action
from fawai_avatar.workspace.shovel_ops import resolve_shovels_root, search_shovels
from fawai_avatar.workspace.shovel_outcome import (
    extract_shovel_ids_from_cmd,
    get_outcome,
    record_outcome,
)


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="fawai-outcome-demo-"))
    data_dir = tmp / "data"
    skills_dir = data_dir / "skills"
    data_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)

    store = SkillStore(skills_dir)
    store.write(
        {
            "id": "skill_good_path",
            "title": "成功路径",
            "when_to_use": "需要可复用成功步骤时",
            "steps": "1. do good\n2. verify",
            "tags": ["success", "review"],
            "source": "tasks:abc",
        }
    )
    store.write(
        {
            "id": "lesson_bad_copy",
            "title": "失败教训",
            "when_to_use": "避免盲目照抄失败做法",
            "steps": "1. do not copy blindly",
            "tags": ["failure", "lesson", "review"],
            "source": "tasks:def",
        }
    )
    polarized = store.search_polarized("失败 成功", limit=10)
    _ok("skill polarize imitate", any(s["id"] == "skill_good_path" for s in polarized["imitate"]))
    _ok("skill polarize avoid", any(s["id"] == "lesson_bad_copy" for s in polarized["avoid"]))
    _ok("lesson polarity avoid", skill_polarity(store.get("lesson_bad_copy")) == "avoid")
    block = skills_prompt_block(skills_dir)
    _ok("prompt has avoid section", "【avoid·勿照抄】" in block and "lesson_bad_copy" in block)
    _ok("prompt has imitate section", "【imitate·可参考】" in block and "skill_good_path" in block)

    root = resolve_shovels_root(Path(__file__).resolve().parents[1])
    os.environ["FAWAI_SHOVELS_ROOT"] = str(root)
    os.environ["FAWAI_DATA_DIR"] = str(data_dir)
    os.environ["FAWAI_WORK_ROOT"] = str(tmp)
    os.environ["FAWAI_SHOVEL_OUTCOME_GATE"] = "1"

    ids = extract_shovel_ids_from_cmd(
        "bash fawai_shovels/shell/rsync-ssh-deploy.sh --help"
    )
    _ok("extract shovel id", "rsync-ssh-deploy" in ids, str(ids))

    for _ in range(3):
        record_outcome(data_dir, "rsync-ssh-deploy", ok=False, message="FAIL: auth")
    oc = get_outcome(data_dir, "rsync_ssh_deploy")
    _ok("rate poor after fails", oc.get("rate") == "poor", str(oc))
    _ok(
        "hyphen and underscore same key",
        get_outcome(data_dir, "rsync-ssh-deploy").get("rate") == "poor",
    )

    data = search_shovels("rsync", work_root=root.parent, data_dir=data_dir, limit=10)
    hit = next(
        (
            h
            for h in (data.get("hits") or [])
            if str(h.get("id") or "") == "rsync_ssh_deploy"
            or "rsync-ssh-deploy" in str(h.get("path") or "")
        ),
        None,
    )
    _ok("search hit present", hit is not None)
    _ok(
        "search hit has outcome",
        isinstance((hit or {}).get("outcome"), dict)
        and (hit or {}).get("outcome", {}).get("rate") == "poor",
        str((hit or {}).get("outcome")),
    )
    _ok(
        "poor_ids listed",
        "rsync_ssh_deploy" in (data.get("poor_ids") or []),
        str(data.get("poor_ids")),
    )

    tools = BackgroundTools(work_root=tmp, data_dir=data_dir, auto_version=False, task_id="t1")
    skill_res = tools.execute(Action(kind="skill_search", args={"query": "失败"}))
    _ok("skill_search tool ok", skill_res.ok, skill_res.message)
    _ok("skill_search message polarity", "avoid=" in (skill_res.message or ""))
    shovel_res = tools.execute(Action(kind="shovel_search", args={"query": "rsync", "limit": 8}))
    _ok("shovel_search tool ok", shovel_res.ok, shovel_res.message)
    _ok("shovel_search message poor", "poor=" in (shovel_res.message or ""), shovel_res.message)

    poor_shell = Action(
        kind="shell",
        args={"cmd": "bash fawai_shovels/shell/rsync-ssh-deploy.sh"},
    )
    _ok(
        "outcome gate triggers",
        should_inject_outcome_gate(
            history=[],
            actions=[poor_shell],
            data_dir=data_dir,
            goal="部署到测试机",
        ),
    )
    msg = format_outcome_gate_message(
        actions=[poor_shell], data_dir=data_dir, goal="部署到测试机"
    )
    _ok("outcome gate marker", OUTCOME_GATE_MARKER in msg)
    _ok(
        "gate once",
        not should_inject_outcome_gate(
            history=[{"role": "user", "content": msg}],
            actions=[poor_shell],
            data_dir=data_dir,
            goal="部署到测试机",
        ),
    )

    print("demo_outcome_polarity: all passed")


if __name__ == "__main__":
    main()
