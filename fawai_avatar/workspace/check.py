# term:改码回证  tool:code_check  doc:doc/功能/编码闭环/说明.md
"""改后机读检查：按后缀选默认命令。"""

from __future__ import annotations

import py_compile
import subprocess
from pathlib import Path
from typing import Any

from fawai_avatar.workspace.paths import (
    WorkspacePathError,
    is_skipped_rel,
    normalize_work_root,
    rel_posix,
    resolve_under_root,
)


def default_check_for(path: str) -> dict[str, Any] | None:
    """返回 {mode, ...}；未知后缀返回 None。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return {"mode": "py_compile"}
    if suffix in {".json"}:
        return {"mode": "json_load"}
    return None


def run_code_check(
    work_root: str | Path,
    *,
    path: str = "",
    paths: list[str] | None = None,
    command: str | None = None,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    root = normalize_work_root(work_root)
    targets = [str(p) for p in (paths or []) if str(p).strip()]
    if path and path.strip():
        targets.insert(0, path.strip())
    if not targets and not command:
        raise ValueError("code_check requires path/paths or command")

    results: list[dict[str, Any]] = []
    if command:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        ok = proc.returncode == 0
        results.append(
            {
                "mode": "command",
                "command": command,
                "ok": ok,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[-4000:],
                "stderr": (proc.stderr or "")[-4000:],
            }
        )
        return {"ok": ok, "checks": results}

    all_ok = True
    for rel in targets:
        file_path = resolve_under_root(root, rel)
        norm = rel_posix(file_path, root)
        if is_skipped_rel(norm):
            raise WorkspacePathError(f"path is skipped: {norm}")
        if not file_path.is_file():
            raise FileNotFoundError(norm)
        spec = default_check_for(norm)
        if spec is None:
            results.append(
                {
                    "path": norm,
                    "mode": "skip",
                    "ok": True,
                    "message": f"no default checker for {Path(norm).suffix or '(no suffix)'}",
                }
            )
            continue
        mode = spec["mode"]
        if mode == "py_compile":
            try:
                py_compile.compile(str(file_path), doraise=True)
                results.append({"path": norm, "mode": mode, "ok": True})
            except py_compile.PyCompileError as exc:
                all_ok = False
                results.append({"path": norm, "mode": mode, "ok": False, "message": str(exc)})
        elif mode == "json_load":
            import json

            try:
                json.loads(file_path.read_text(encoding="utf-8"))
                results.append({"path": norm, "mode": mode, "ok": True})
            except Exception as exc:  # noqa: BLE001
                all_ok = False
                results.append({"path": norm, "mode": mode, "ok": False, "message": str(exc)})
        else:
            results.append({"path": norm, "mode": mode, "ok": False, "message": "unknown mode"})
            all_ok = False
    return {"ok": all_ok, "checks": results}
