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

_PATH_ALIASES = ("file", "target", "filepath", "filename")
_PATHS_ALIASES = ("files", "targets")
_COMMAND_ALIASES = ("cmd",)
_CHECKABLE_SUFFIXES = {".py", ".json"}

_MISSING_ARGS_MSG = (
    "code_check requires path/paths or command "
    "(aliases: file/filepath/target → path; files/targets → paths; cmd → command)"
)


def default_check_for(path: str) -> dict[str, Any] | None:
    """返回 {mode, ...}；未知后缀返回 None。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return {"mode": "py_compile"}
    if suffix in {".json"}:
        return {"mode": "json_load"}
    return None


def coerce_code_check_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """归一 code_check 参数：path / paths / command。

    兼容模型常见误写：file / filepath / target → path；files / targets → paths；cmd → command。
    规划 apply_capability_defaults 与执行侧共用。
    """
    src = dict(args or {})
    out = dict(src)

    path = str(src.get("path") or "").strip()
    if not path:
        for key in _PATH_ALIASES:
            cand = str(src.get(key) or "").strip()
            if cand:
                path = cand
                break
    if path:
        out["path"] = path

    path_list: list[str] = []
    raw_paths = src.get("paths")
    if isinstance(raw_paths, list):
        for item in raw_paths:
            s = str(item).strip()
            if s and s not in path_list:
                path_list.append(s)
    elif isinstance(raw_paths, str) and raw_paths.strip():
        path_list.append(raw_paths.strip())
    if not path_list:
        for key in _PATHS_ALIASES:
            raw = src.get(key)
            if isinstance(raw, list):
                for item in raw:
                    s = str(item).strip()
                    if s and s not in path_list:
                        path_list.append(s)
            elif isinstance(raw, str) and raw.strip():
                if raw.strip() not in path_list:
                    path_list.append(raw.strip())
            if path_list:
                break
    if path_list:
        out["paths"] = path_list

    command = str(src.get("command") or "").strip()
    if not command:
        for key in _COMMAND_ALIASES:
            cand = str(src.get(key) or "").strip()
            if cand:
                command = cand
                break
    if command:
        out["command"] = command

    for key in _PATH_ALIASES + _PATHS_ALIASES + _COMMAND_ALIASES:
        out.pop(key, None)
    return out


def fill_code_check_args(
    args: dict[str, Any] | None,
    *,
    write_set: list[str] | None = None,
) -> dict[str, Any]:
    """coerce 后若仍无检查目标，则用本任务 write_set 回填 paths。

    优先回填 .py/.json；若无已知后缀则回填全部已写路径。无 write_set 则原样返回。
    """
    out = coerce_code_check_args(args)
    has_path = bool(str(out.get("path") or "").strip())
    raw_paths = out.get("paths")
    has_paths = isinstance(raw_paths, list) and any(str(p).strip() for p in raw_paths)
    has_cmd = bool(str(out.get("command") or "").strip())
    if has_path or has_paths or has_cmd:
        return out
    candidates = [str(p).strip() for p in (write_set or []) if str(p).strip()]
    if not candidates:
        return out
    checkable = [
        p for p in candidates if Path(p).suffix.lower() in _CHECKABLE_SUFFIXES
    ]
    out["paths"] = checkable or list(candidates)
    return out


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
        raise ValueError(_MISSING_ARGS_MSG)

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
