# term:Python 扩展  doc:doc/功能/Python扩展/说明.md
"""Python 扩展冒烟：py_write / py_pip / py_run + 路径闸 / 无 task_id。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action
from fawai_avatar.workspace.py_session import resolve_runtime_python


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="fawai-py-session-"))
    data = root / "data"
    data.mkdir()
    # 指向本仓 .venv 若存在，否则用当前解释器（resolve_runtime_python 优先 sys.executable）
    os.environ["FAWAI_WORK_ROOT"] = str(root)
    os.environ["FAWAI_DATA_DIR"] = str(data)

    task_id = "demo_task_py_session"
    tools = BackgroundTools(work_root=root, data_dir=data, auto_version=False, task_id=task_id)

    missing = tools.execute(
        Action(kind="py_write", args={"path": "hello.py", "content": "print(1)\n"})
    )
    # task_id 已在 tools 上
    _ok("py_write with task_id on tools", missing.ok, missing.message)

    hello = data / "sessions" / task_id / "hello.py"
    _ok("session file exists", hello.is_file(), str(hello))
    _ok("session content", hello.read_text(encoding="utf-8").strip() == "print(1)")

    no_tid = BackgroundTools(work_root=root, data_dir=data, auto_version=False, task_id=None)
    denied = no_tid.execute(
        Action(kind="py_write", args={"path": "x.py", "content": "x"})
    )
    _ok("no task_id fails", not denied.ok and "task_id" in denied.message.lower(), denied.message)

    escape = tools.execute(
        Action(kind="py_write", args={"path": "../escape.py", "content": "bad"})
    )
    _ok("path escape fails", not escape.ok and "escape" in escape.message.lower(), escape.message)

    env_forbid = tools.execute(
        Action(kind="py_write", args={"path": ".env", "content": "SECRET=1"})
    )
    _ok(".env forbidden", not env_forbid.ok, env_forbid.message)

    # 已装包 no-op：typing-extensions（主程序常见依赖）
    python = resolve_runtime_python(root)
    pip = tools.execute(
        Action(
            kind="py_pip",
            args={"packages": ["typing-extensions"], "timeout_sec": 120},
        )
    )
    _ok("py_pip typing-extensions", pip.ok, pip.message)
    _ok("py_pip uses runtime python", str(pip.data.get("python")) == str(python), str(pip.data.get("python")))

    write_script = tools.execute(
        Action(
            kind="py_write",
            args={
                "path": "hello.py",
                "content": (
                    "import json\n"
                    "from pathlib import Path\n"
                    "print('hello-py-session')\n"
                    "Path('out.json').write_text("
                    "json.dumps({'ok': True}), encoding='utf-8')\n"
                ),
            },
        )
    )
    _ok("py_write script", write_script.ok, write_script.message)

    run = tools.execute(
        Action(
            kind="py_run",
            args={"script": "hello.py", "result_path": "out.json", "timeout_sec": 30},
        )
    )
    _ok("py_run ok", run.ok, run.message)
    stdout = str((run.data or {}).get("stdout") or "")
    _ok("py_run stdout", "hello-py-session" in stdout, stdout[:200])
    run_dir = data / "sessions" / task_id / str((run.data or {}).get("run_dir") or "")
    _ok("runs stdout.txt", (run_dir / "stdout.txt").is_file(), str(run_dir))
    _ok("result_exists", bool((run.data or {}).get("result_exists")))

    bad = tools.execute(
        Action(kind="py_write", args={"path": "bad.py", "content": "raise SystemExit(7)\n"})
    )
    _ok("py_write bad", bad.ok)
    fail_run = tools.execute(Action(kind="py_run", args={"script": "bad.py"}))
    _ok(
        "py_run non-zero",
        not fail_run.ok and (fail_run.data or {}).get("returncode") == 7,
        fail_run.message,
    )
    fail_dir = data / "sessions" / task_id / str((fail_run.data or {}).get("run_dir") or "")
    _ok("fail stderr.txt", (fail_dir / "stderr.txt").is_file() or (fail_dir / "meta.json").is_file())

    code_run = tools.execute(
        Action(kind="py_run", args={"code": "print(2+2)"})
    )
    _ok("py_run code", code_run.ok and "4" in str((code_run.data or {}).get("stdout") or ""))

    print("demo_py_session: all passed")


if __name__ == "__main__":
    main()
