# term:Git 工作流  tool:git_*  doc:doc/功能/编码闭环/说明.md
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.paths import normalize_work_root


class GitError(ValueError):
    pass


def _run(root: Path, args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _ensure_repo(root: Path) -> None:
    proc = _run(root, ["rev-parse", "--is-inside-work-tree"])
    if proc.returncode != 0 or (proc.stdout or "").strip() != "true":
        raise GitError(f"not a git repository: {root}")


def git_status(work_root: str | Path, *, porcelain: bool = True) -> dict[str, Any]:
    root = normalize_work_root(work_root)
    _ensure_repo(root)
    args = ["status", "--porcelain=v1"] if porcelain else ["status"]
    proc = _run(root, args)
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git status failed").strip())
    return {
        "ok": True,
        "cwd": str(root),
        "porcelain": porcelain,
        "output": proc.stdout or "",
    }


def git_diff(
    work_root: str | Path,
    *,
    path: str = "",
    staged: bool = False,
    head_limit: int = 8000,
) -> dict[str, Any]:
    root = normalize_work_root(work_root)
    _ensure_repo(root)
    args = ["diff", "--staged"] if staged else ["diff"]
    if path.strip():
        args.extend(["--", path.strip()])
    proc = _run(root, args)
    if proc.returncode not in (0, 1):
        raise GitError((proc.stderr or proc.stdout or "git diff failed").strip())
    text = proc.stdout or ""
    truncated = False
    if head_limit > 0 and len(text) > head_limit:
        text = text[:head_limit] + "\n…(truncated)"
        truncated = True
    return {"ok": True, "staged": staged, "path": path or ".", "diff": text, "truncated": truncated}


def git_commit(
    work_root: str | Path,
    *,
    message: str,
    paths: list[str] | None = None,
    add_all: bool = False,
) -> dict[str, Any]:
    root = normalize_work_root(work_root)
    _ensure_repo(root)
    msg = (message or "").strip()
    if not msg:
        raise GitError("git_commit requires message")
    if add_all:
        add = _run(root, ["add", "-A"])
        if add.returncode != 0:
            raise GitError((add.stderr or add.stdout or "git add -A failed").strip())
    elif paths:
        add = _run(root, ["add", "--", *[str(p) for p in paths]])
        if add.returncode != 0:
            raise GitError((add.stderr or add.stdout or "git add failed").strip())
    proc = _run(root, ["commit", "-m", msg])
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git commit failed").strip())
    sha = _run(root, ["rev-parse", "HEAD"])
    return {
        "ok": True,
        "message": msg,
        "stdout": (proc.stdout or "").strip(),
        "commit": (sha.stdout or "").strip() if sha.returncode == 0 else "",
    }
