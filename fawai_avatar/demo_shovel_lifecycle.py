# term:脚本铲子生命周期  doc:doc/功能/脚本铲子生命周期/说明.md
"""脚本铲子生命周期冒烟：shovel_search + 软闸判定（无真实 LLM）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.shovel_lifecycle import (
    PROMOTE_HINT_MARKER,
    SEARCH_GATE_MARKER,
    action_invents_script,
    format_promote_hint_message,
    format_search_gate_message,
    hard_reject_invent_without_search,
    history_has_shovel_search_ok,
    should_inject_promote_hint,
    should_inject_search_gate,
)
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action
from fawai_avatar.workspace.shovel_ops import resolve_shovels_root, search_shovels


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    # 本仓 fawai_shovels
    root = resolve_shovels_root(Path(__file__).resolve().parents[1])
    _ok("resolve shovels root", (root / "scaffold" / "catalog.py").is_file(), str(root))

    data = search_shovels("rsync", work_root=root.parent, limit=10)
    _ok("search rsync hits", int(data.get("count") or 0) >= 1, str(data.get("count")))
    ids = [h.get("id") for h in data.get("hits") or []]
    _ok(
        "rsync shovel id present",
        any(i and "rsync" in str(i) for i in ids) or any("rsync" in str(h.get("path")) for h in data.get("hits") or []),
        str(ids[:5]),
    )

    tmp = Path(tempfile.mkdtemp(prefix="fawai-shovel-demo-"))
    data_dir = tmp / "data"
    data_dir.mkdir()
    os.environ["FAWAI_WORK_ROOT"] = str(tmp)
    os.environ["FAWAI_DATA_DIR"] = str(data_dir)
    os.environ["FAWAI_SHOVELS_ROOT"] = str(root)

    tools = BackgroundTools(work_root=tmp, data_dir=data_dir, auto_version=False, task_id="t1")
    res = tools.execute(Action(kind="shovel_search", args={"query": "ssh", "limit": 8}))
    _ok("shovel_search tool ok", res.ok, res.message)
    _ok("shovel_search has hits", int((res.data or {}).get("count") or 0) >= 1, res.message)

    invent = Action(kind="py_write", args={"path": "x.py", "content": "print(1)\n"})
    _ok("py_write invents", action_invents_script(invent))
    _ok(
        "existing shovel shell not invent",
        not action_invents_script(
            Action(kind="shell", args={"cmd": "bash fawai_shovels/shell/rsync-ssh-deploy.sh"})
        ),
    )

    empty_hist: list[dict] = []
    _ok(
        "search gate on invent without search",
        should_inject_search_gate(history=empty_hist, actions=[invent], goal="写个脚本"),
    )
    _ok("gate message marker", SEARCH_GATE_MARKER in format_search_gate_message())

    searched = [
        {
            "role": "user",
            "content": "执行结果（工具结果观察）：\n- shovel_search → ok: shovel_search 2 hits\n",
        }
    ]
    _ok("history detects shovel_search ok", history_has_shovel_search_ok(searched))
    _ok(
        "no gate after search",
        not should_inject_search_gate(history=searched, actions=[invent], goal="写个脚本"),
    )

    os.environ["FAWAI_SHOVEL_SEARCH_HARD"] = "1"
    hard = hard_reject_invent_without_search(history=empty_hist, actions=[invent])
    _ok("hard reject when enabled", bool(hard and "HARD" in (hard or "")), hard or "")
    os.environ["FAWAI_SHOVEL_SEARCH_HARD"] = "0"
    _ok(
        "hard off by default path",
        hard_reject_invent_without_search(history=empty_hist, actions=[invent]) is None,
    )

    py_hist = [
        {
            "role": "user",
            "content": "执行结果（工具结果观察）：\n- py_write path='x.py' → ok: wrote\n",
        }
    ]
    _ok(
        "promote hint after temp script",
        should_inject_promote_hint(history=py_hist, status="success"),
    )
    _ok("promote marker", PROMOTE_HINT_MARKER in format_promote_hint_message())
    with_hint = py_hist + [{"role": "user", "content": format_promote_hint_message()}]
    _ok(
        "promote only once",
        not should_inject_promote_hint(history=with_hint, status="success"),
    )

    print("demo_shovel_lifecycle: all passed")


if __name__ == "__main__":
    main()
