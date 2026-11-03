# term:编码闭环  doc:doc/功能/编码闭环/说明.md
"""P0/P1 编码闭环冒烟：patch / grep 上下文 / check / confirm / explore / git。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="fawai-coding-"))
    data = root / "data"
    data.mkdir()
    sample = root / "notes" / "coding_sample.py"
    sample.parent.mkdir(parents=True)
    sample.write_text(
        "def answer() -> int:\n    return 1\n\ndef hello() -> str:\n    return \"hello\"\n",
        encoding="utf-8",
    )
    # 大文件：禁止整写
    big = root / "notes" / "big.py"
    big.write_text("\n".join(f"# line {i}" for i in range(100)), encoding="utf-8")

    os.environ["FAWAI_WORK_ROOT"] = str(root)
    os.environ["FAWAI_DATA_DIR"] = str(data)
    os.environ["FAWAI_AUTO_VERSION"] = "0"
    os.environ["FAWAI_DIFF_MODE"] = "auto"

    tools = BackgroundTools(work_root=root, data_dir=data, auto_version=False, diff_mode="auto")

    # P0-4 grep context
    grep = tools.execute(
        Action(
            kind="fs_grep",
            args={"pattern": "return 1", "path": "notes", "context_before": 1, "context_after": 1},
        )
    )
    _ok("fs_grep context", grep.ok and bool(grep.data.get("hits")))

    # P0-1 patch
    patch = tools.execute(
        Action(
            kind="fs_patch",
            args={"path": "notes/coding_sample.py", "old": "return 1", "new": "return 2"},
            write_resources=["notes/coding_sample.py"],
        )
    )
    _ok("fs_patch", patch.ok and "diff" in (patch.data or {}), patch.message)
    text = sample.read_text(encoding="utf-8")
    _ok("fs_patch applied", "return 2" in text and "return 1" not in text)

    # stringified hunks + quality gate
    sample.write_text(
        "def answer() -> int:\n    return 2\n\ndef hello() -> str:\n    return \"hello\"\n",
        encoding="utf-8",
    )
    patch_str = tools.execute(
        Action(
            kind="fs_patch",
            args={
                "path": "notes/coding_sample.py",
                "hunks": '[{"old":"return 2","new":"return 20"}]',
            },
            write_resources=["notes/coding_sample.py"],
        )
    )
    _ok(
        "fs_patch string hunks",
        patch_str.ok and "return 20" in sample.read_text(encoding="utf-8"),
        patch_str.message,
    )
    bad_ellip = tools.execute(
        Action(
            kind="fs_patch",
            args={
                "path": "notes/coding_sample.py",
                "old": "return 20…",
                "new": "return 21",
            },
            write_resources=["notes/coding_sample.py"],
        )
    )
    _ok(
        "fs_patch reject ellipsis",
        (not bad_ellip.ok) and "…" in bad_ellip.message,
        bad_ellip.message,
    )

    # 弱写入：仅新增未引用 CSS 变量 → data.weak_write
    ui = root / "notes" / "ui.html"
    ui.write_text(
        ":root {\n  --chat-max: 720px;\n}\n.msg { padding: 8px; }\n",
        encoding="utf-8",
    )
    weak = tools.execute(
        Action(
            kind="fs_patch",
            args={
                "path": "notes/ui.html",
                "old": "      --chat-max: 720px;\n    }",
                "new": (
                    "      --chat-max: 720px;\n"
                    "      --msg-gap: 12px;\n"
                    "      --bubble-radius: 18px;\n"
                    "    }"
                ),
            },
            write_resources=["notes/ui.html"],
        )
    )
    # 缩进可能不匹配；用与文件一致的片段
    if not weak.ok:
        weak = tools.execute(
            Action(
                kind="fs_patch",
                args={
                    "path": "notes/ui.html",
                    "old": "  --chat-max: 720px;\n}",
                    "new": (
                        "  --chat-max: 720px;\n"
                        "  --msg-gap: 12px;\n"
                        "  --bubble-radius: 18px;\n"
                        "}"
                    ),
                },
                write_resources=["notes/ui.html"],
            )
        )
    _ok(
        "fs_patch weak_write flag",
        weak.ok and bool((weak.data or {}).get("weak_write")),
        str(weak.data),
    )
    missing = tools.execute(
        Action(
            kind="fs_patch",
            args={"path": "notes/no_such_app.js", "old": "a", "new": "b"},
            write_resources=["notes/no_such_app.js"],
        )
    )
    _ok(
        "fs_patch missing path",
        (not missing.ok) and "target missing" in missing.message,
        missing.message,
    )

    # refuse full write on big file
    refuse = tools.execute(
        Action(
            kind="fs_write",
            args={"path": "notes/big.py", "content": "x = 1\n"},
            write_resources=["notes/big.py"],
        )
    )
    _ok("refuse full write", (not refuse.ok) and "force_full_write" in refuse.message)

    # P0-2 code_check
    check = tools.execute(Action(kind="code_check", args={"path": "notes/coding_sample.py"}))
    _ok("code_check", check.ok, check.message)

    # bad syntax
    sample.write_text("def broken(\n", encoding="utf-8")
    bad = tools.execute(Action(kind="code_check", args={"path": "notes/coding_sample.py"}))
    _ok("code_check fails on syntax", not bad.ok)
    sample.write_text("def answer() -> int:\n    return 2\n", encoding="utf-8")

    # P0-5 confirm mode
    tools_c = BackgroundTools(
        work_root=root, data_dir=data, auto_version=False, diff_mode="confirm", task_id="taskdemo"
    )
    pend = tools_c.execute(
        Action(
            kind="fs_patch",
            args={
                "path": "notes/coding_sample.py",
                "old": "return 2",
                "new": "return 3",
                "task_id": "taskdemo",
            },
            write_resources=["notes/coding_sample.py"],
        )
    )
    _ok("pending patch", pend.ok and pend.data.get("pending"), pend.message)
    _ok("not applied yet", "return 2" in sample.read_text(encoding="utf-8"))
    conf = tools_c.execute(Action(kind="diff_confirm", args={"task_id": "taskdemo"}))
    _ok("diff_confirm", conf.ok and "return 3" in sample.read_text(encoding="utf-8"), conf.message)

    # P1 explore
    ex = tools.execute(
        Action(kind="explore_search", args={"queries": ["return"], "path": "notes", "head_limit": 10})
    )
    _ok("explore_search", ex.ok and ex.data.get("results"))

    # P1 git (init temp repo)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    st = tools.execute(Action(kind="git_status", args={}))
    _ok("git_status", st.ok, st.message)
    sample.write_text("def answer() -> int:\n    return 4\n", encoding="utf-8")
    gd = tools.execute(Action(kind="git_diff", args={"path": "notes/coding_sample.py"}))
    _ok("git_diff", gd.ok and "return 4" in str(gd.data.get("diff") or ""), gd.message)
    gc = tools.execute(
        Action(
            kind="git_commit",
            args={"message": "bump answer", "paths": ["notes/coding_sample.py"]},
            write_resources=["notes/coding_sample.py"],
        )
    )
    _ok("git_commit", gc.ok, gc.message)

    # read_many
    rm = tools.execute(
        Action(kind="fs_read_many", args={"paths": ["notes/coding_sample.py", "notes/big.py"], "limit": 5})
    )
    _ok("fs_read_many", rm.ok and rm.data.get("count") == 2)

    # mock coding goal via WorkerLoop
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    from fawai_avatar.runtime.loop import run_worker_once

    sample.write_text('def hello() -> str:\n    return "hello"\n', encoding="utf-8")
    out = run_worker_once(
        '用 fs_patch 把 notes/coding_sample.py 里 return "hello" 改成 return "hello-patched"',
        data_dir=str(data),
        dry_run=True,
        mock_brain=True,
        capture=False,
        max_steps=6,
    )
    # work_root for run_worker_once uses env FAWAI_WORK_ROOT
    _ok("worker coding mock", bool(out.get("ok")), json.dumps(out.get("error")))
    _ok(
        "worker patched",
        "hello-patched" in sample.read_text(encoding="utf-8"),
        sample.read_text(encoding="utf-8")[:80],
    )

    print("demo_coding_loop: all passed")
    print(json.dumps({"root": str(root), "write_set": out.get("write_set")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
