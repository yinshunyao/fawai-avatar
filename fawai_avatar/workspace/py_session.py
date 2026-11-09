# term:Python 扩展  tool:py_write/py_pip/py_run  doc:doc/功能/Python扩展/说明.md
"""任务会话目录：写码、主程序 venv 装包、跑测并落盘结果。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FORBIDDEN_NAMES = frozenset({".env"})
_UNSAFE_PIP_TOKENS = frozenset(
    {
        "uninstall",
        "freeze",
        "download",
        "wheel",
        "hash",
        "config",
        "cache",
        "debug",
        "check",
        "list",
        "show",
        "search",
        "index",
    }
)
_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-\[\]]*(==|>=|<=|~=|!=|>|<)?[A-Za-z0-9._*\-]*$")


class PySessionError(ValueError):
    """Session path / interpreter / pip policy error."""


def session_root(data_dir: str | Path, task_id: str, *, create: bool = True) -> Path:
    tid = (task_id or "").strip()
    if not tid:
        raise PySessionError("py_* requires task_id")
    if "/" in tid or "\\" in tid or tid in {".", ".."}:
        raise PySessionError(f"invalid task_id: {task_id!r}")
    root = Path(data_dir).expanduser().resolve() / "sessions" / tid
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_runtime_python(work_root: str | Path | None = None) -> Path:
    """主程序解释器：优先当前进程，否则 work_root/.venv/bin/python。

    注意：不要对 venv shim 做 Path.resolve()——会解到 Homebrew/系统基座 Python，
    触发 PEP 668 并装错环境。
    """
    exe = Path(sys.executable)
    if exe.exists():
        return exe
    root = Path(work_root or ".").expanduser().resolve()
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        cand = root / rel
        if cand.exists():
            return cand
    raise PySessionError(
        "runtime python not found (sys.executable missing and no .venv/bin/python under work_root)"
    )


def resolve_under_session(root: Path, user_path: str) -> Path:
    raw = (user_path or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw in {".", "/"}:
        raise PySessionError("path required (relative to session root)")
    if raw.startswith("/") or raw.startswith("~") or Path(raw).is_absolute():
        raise PySessionError(f"path must be relative to session root: {user_path}")
    parts = Path(raw).parts
    if ".." in parts:
        raise PySessionError(f"path escapes session root: {user_path}")
    name = Path(raw).name
    if name in _FORBIDDEN_NAMES or name.startswith(".env"):
        raise PySessionError(f"writing {name} is forbidden")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PySessionError(f"path escapes session root: {user_path}") from exc
    return candidate


def write_file(root: Path, *, path: str, content: str) -> dict[str, Any]:
    target = resolve_under_session(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else str(content)
    target.write_text(text, encoding="utf-8")
    rel = target.relative_to(root.resolve()).as_posix()
    return {
        "path": rel,
        "abs_path": str(target),
        "bytes": len(text.encode("utf-8")),
        "session_root": str(root),
    }


def _normalize_packages(packages: Any) -> list[str]:
    items: list[str] = []
    if isinstance(packages, list):
        for p in packages:
            s = str(p).strip()
            if s:
                items.append(s)
    elif isinstance(packages, str):
        items.extend(x for x in packages.replace(",", " ").split() if x.strip())
    else:
        raise PySessionError("py_pip requires packages: list[str] or space-separated string")
    if not items:
        raise PySessionError("py_pip requires non-empty packages")
    out: list[str] = []
    for pkg in items:
        low = pkg.lower().strip()
        if low.startswith("-") or low in _UNSAFE_PIP_TOKENS:
            raise PySessionError(
                f"py_pip only allows package install specs, refused: {pkg!r}"
            )
        if not _PKG_RE.match(pkg):
            raise PySessionError(f"invalid package spec: {pkg!r}")
        out.append(pkg)
    return out


def pip_install(
    *,
    packages: Any,
    work_root: str | Path | None = None,
    timeout_sec: float = 300,
) -> dict[str, Any]:
    python = resolve_runtime_python(work_root)
    pkgs = _normalize_packages(packages)
    cmd = [str(python), "-m", "pip", "install", *pkgs]
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=max(1.0, float(timeout_sec)),
        check=False,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "python": str(python),
        "packages": pkgs,
        "cmd": cmd,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "elapsed_ms": elapsed_ms,
    }


def _run_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def run_script(
    root: Path,
    *,
    work_root: str | Path | None = None,
    script: str | None = None,
    code: str | None = None,
    module: str | None = None,
    args: list[str] | None = None,
    timeout_sec: float = 60,
    result_path: str | None = None,
) -> dict[str, Any]:
    python = resolve_runtime_python(work_root)
    argv_extra = [str(a) for a in (args or [])]
    script_s = (script or "").strip()
    code_s = code if isinstance(code, str) else None
    module_s = (module or "").strip()
    modes = [bool(script_s), bool(code_s and code_s.strip()), bool(module_s)]
    if sum(1 for m in modes if m) != 1:
        raise PySessionError("py_run requires exactly one of script / code / module")

    if script_s:
        target = resolve_under_session(root, script_s)
        if not target.is_file():
            raise PySessionError(f"script not found: {script_s}")
        cmd = [str(python), str(target), *argv_extra]
        mode = "script"
        entry = script_s
    elif module_s:
        if "/" in module_s or "\\" in module_s or ".." in module_s:
            raise PySessionError(f"invalid module name: {module_s!r}")
        cmd = [str(python), "-m", module_s, *argv_extra]
        mode = "module"
        entry = module_s
    else:
        assert code_s is not None
        cmd = [str(python), "-c", code_s, *argv_extra]
        mode = "code"
        entry = "<code>"

    result_rel: str | None = None
    result_abs: str | None = None
    if result_path:
        result_file = resolve_under_session(root, str(result_path))
        result_rel = result_file.relative_to(root.resolve()).as_posix()
        result_abs = str(result_file)

    run_id = _run_stamp()
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # 会话根优先，便于相对 import / 写结果
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(root) + (os.pathsep + prev if prev else "")
    env["FAWAI_PY_SESSION_ROOT"] = str(root)

    wall_started = time.time()
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_sec)),
            env=env,
            check=False,
        )
        returncode = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        )
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        )
        if not stderr:
            stderr = f"timeout after {timeout_sec}s"
    elapsed_ms = int((time.monotonic() - started) * 1000)

    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    meta: dict[str, Any] = {
        "ok": returncode == 0 and not timed_out,
        "returncode": returncode,
        "timed_out": timed_out,
        "mode": mode,
        "entry": entry,
        "python": str(python),
        "cwd": str(root),
        "cmd": cmd if mode != "code" else [str(python), "-c", "<code>", *argv_extra],
        "elapsed_ms": elapsed_ms,
        "timeout_sec": float(timeout_sec),
        "run_dir": f"runs/{run_id}",
        "stdout_path": f"runs/{run_id}/stdout.txt",
        "stderr_path": f"runs/{run_id}/stderr.txt",
        "result_path": result_rel,
        "result_abs": result_abs,
        "result_exists": bool(result_abs and Path(result_abs).is_file()),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    meta["stdout"] = stdout
    meta["stderr"] = stderr
    meta["session_root"] = str(root)
    meta["run_id"] = run_id
    # 交付物：result_path / stdout OK 行 / 本趟会话根内新文件（供会话附件卡）
    try:
        from fawai_avatar.channel.file_deliver import collect_py_run_deliverables

        if meta.get("ok"):
            meta["deliverables"] = collect_py_run_deliverables(
                session_root=root,
                result_abs=result_abs,
                stdout=stdout,
                started_mtime=wall_started,
            )
        else:
            meta["deliverables"] = []
    except Exception:  # noqa: BLE001 — 登记失败不挡跑测
        meta["deliverables"] = []
    return meta
