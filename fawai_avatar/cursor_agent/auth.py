# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""Cursor Agent 登录：读 ~/.cursor/sdk/auth.json；浏览器登录走 Node @cursor/sdk。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_login_lock = threading.Lock()
_login_state: dict[str, Any] = {
    "running": False,
    "login_url": "",
    "error": "",
    "started_at": 0.0,
    "finished_at": 0.0,
    "email": "",
}


def default_auth_path() -> Path:
    return Path.home() / ".cursor" / "sdk" / "auth.json"


def vendored_sdk_root() -> Path | None:
    try:
        import cursor_sdk
    except ImportError:
        return None
    root = Path(cursor_sdk.__file__).resolve().parent / (
        "_vendor/bridge/node_modules/@cursor/sdk"
    )
    return root if root.is_dir() else None


def node_auth_script() -> Path:
    return Path(__file__).resolve().parent / "node_auth.mjs"


def _node_major(node_bin: str) -> int | None:
    try:
        out = subprocess.check_output(
            [node_bin, "-p", "process.versions.node"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip()
        return int(out.split(".", 1)[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _which_node() -> str:
    """优先 Node ≥22（@cursor/sdk engines）；可用 FAWAI_CURSOR_NODE 指定。"""
    override = (os.environ.get("FAWAI_CURSOR_NODE") or "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    which = shutil.which("node") or ""
    if which:
        candidates.append(which)
    # cursor-sdk 自带 bridge Node（常 ≥22），不依赖用户 PATH / nvm 默认版
    try:
        import cursor_sdk

        vendor_node = (
            Path(cursor_sdk.__file__).resolve().parent
            / "_vendor"
            / "bridge"
            / "bin"
            / "node"
        )
        if vendor_node.is_file():
            candidates.append(str(vendor_node))
    except Exception:  # noqa: BLE001
        pass
    home = Path.home()
    for rel in (
        ".nvm/versions/node/v22.22.1/bin/node",
        ".nvm/versions/node/v22.21.1/bin/node",
        ".nvm/versions/node/v22.20.0/bin/node",
    ):
        p = home / rel
        if p.is_file():
            candidates.append(str(p))
    # 扫 nvm 下 v22+（含 v23/v24…）；按主版本再按路径倒序
    nvm_root = home / ".nvm/versions/node"
    if nvm_root.is_dir():
        found: list[tuple[int, str]] = []
        for d in nvm_root.iterdir():
            name = d.name  # v22.22.1
            if not name.startswith("v"):
                continue
            try:
                major = int(name[1:].split(".", 1)[0])
            except ValueError:
                continue
            if major < 22:
                continue
            p = d / "bin" / "node"
            if p.is_file():
                found.append((major, str(p)))
        for _maj, path in sorted(found, key=lambda x: (x[0], x[1]), reverse=True):
            candidates.append(path)
    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if not Path(c).is_file() and not shutil.which(c):
            continue
        major = _node_major(c)
        if major is None:
            continue
        if major >= 22:
            return c
    return ""


def read_auth_file(path: Path | None = None) -> dict[str, Any] | None:
    p = path or default_auth_path()
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if int(data.get("version") or 0) != 1:
        return None
    key = str(data.get("apiKey") or "").strip()
    if not key:
        return None
    return data


def env_api_key() -> str:
    for name in ("CURSOR_API_KEY", "FAWAI_CURSOR_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def resolve_api_key() -> tuple[str, str]:
    """返回 (api_key, source)；source 为 env / auth_json / store / 空。"""
    env_key = env_api_key()
    if env_key:
        return env_key, "env"
    data = read_auth_file()
    if data:
        return str(data.get("apiKey") or "").strip(), "auth_json"
    try:
        from fawai_avatar.cursor_agent.settings_store import get_store

        legacy = get_store().api_key().strip()
        if legacy:
            return legacy, "store"
    except Exception:  # noqa: BLE001
        pass
    return "", ""


def auth_snapshot() -> dict[str, Any]:
    key, source = resolve_api_key()
    file_data = read_auth_file()
    email = ""
    expires_at_ms = None
    if file_data:
        email = str(file_data.get("email") or "").strip()
        exp = file_data.get("apiKeyExpiresAtMs")
        if isinstance(exp, (int, float)):
            expires_at_ms = int(exp)
    with _login_lock:
        pending = dict(_login_state)
    logged_in = bool(key)
    status = "logged-in" if logged_in else "logged-out"
    if pending.get("running"):
        status = "logging-in"
    node_bin = _which_node()
    node_version = ""
    if node_bin:
        try:
            out = subprocess.check_output(
                [node_bin, "-p", "process.versions.node"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip()
            node_version = out
        except (OSError, subprocess.SubprocessError):
            node_version = ""
    return {
        "logged_in": logged_in,
        "status": status,
        "email": email or str(pending.get("email") or ""),
        "source": source if logged_in else "",
        "auth_path": str(default_auth_path()),
        "has_api_key": logged_in,
        "api_key_expires_at_ms": expires_at_ms,
        "login_pending": bool(pending.get("running")),
        "login_url": str(pending.get("login_url") or ""),
        "login_error": str(pending.get("error") or ""),
        "node_available": bool(node_bin),
        "node_bin": node_bin,
        "node_version": node_version,
        "sdk_node_available": vendored_sdk_root() is not None,
    }


def _run_node_auth(cmd: str, *, open_browser: bool = True, timeout: float | None = 120.0) -> dict[str, Any]:
    node = _which_node()
    if not node:
        return {
            "ok": False,
            "error": (
                "未找到可用的 Node.js（@cursor/sdk 需要 ≥22.13；"
                "请安装 nvm 的 v22 或设置 FAWAI_CURSOR_NODE）"
            ),
        }
    sdk = vendored_sdk_root()
    if sdk is None:
        return {"ok": False, "error": "cursor-sdk 未安装或缺少 @cursor/sdk"}
    script = node_auth_script()
    if not script.is_file():
        return {"ok": False, "error": f"缺少登录脚本：{script}"}
    env = os.environ.copy()
    env["FAWAI_CURSOR_SDK_NODE"] = str(sdk)
    env["FAWAI_CURSOR_OPEN_BROWSER"] = "1" if open_browser else "0"
    try:
        proc = subprocess.Popen(
            [node, str(script), cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    login_url = ""
    stderr_buf: list[str] = []

    def _drain_stderr() -> None:
        nonlocal login_url
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_buf.append(line)
            line_s = line.strip()
            if not line_s:
                continue
            try:
                obj = json.loads(line_s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "login_url":
                login_url = str(obj.get("url") or "").strip()
                with _login_lock:
                    _login_state["login_url"] = login_url

    drain = threading.Thread(target=_drain_stderr, name="cursor-auth-stderr", daemon=True)
    drain.start()
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": "登录超时", "login_url": login_url}
    drain.join(timeout=2.0)
    out_line = (stdout or "").strip().splitlines()
    payload: dict[str, Any] = {}
    if out_line:
        try:
            parsed = json.loads(out_line[-1])
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {"ok": False, "error": out_line[-1][:500]}
    if not payload:
        err_tail = "".join(stderr_buf)[-500:]
        payload = {"ok": False, "error": err_tail or f"node exit {proc.returncode}"}
    if login_url and "login_url" not in payload:
        payload["login_url"] = login_url
    if proc.returncode not in (0, None) and payload.get("ok") is not False:
        payload.setdefault("ok", False)
        payload.setdefault("error", f"node exit {proc.returncode}")
    return payload


def node_auth_status() -> dict[str, Any]:
    result = _run_node_auth("status", open_browser=False, timeout=30.0)
    snap = auth_snapshot()
    result.update(
        {
            "snapshot": snap,
            "logged_in": snap["logged_in"],
            "email": snap["email"],
            "status": snap["status"],
        }
    )
    return result


def logout() -> dict[str, Any]:
    result = _run_node_auth("logout", open_browser=False, timeout=30.0)
    # 再清本地文件兜底
    try:
        p = default_auth_path()
        if p.is_file():
            p.unlink()
    except OSError:
        pass
    with _login_lock:
        _login_state.update(
            {
                "running": False,
                "login_url": "",
                "error": "",
                "email": "",
                "finished_at": time.time(),
            }
        )
    snap = auth_snapshot()
    result["snapshot"] = snap
    result["logged_in"] = snap["logged_in"]
    result.setdefault("ok", True)
    return result


def start_login(*, open_browser: bool = True) -> dict[str, Any]:
    with _login_lock:
        if _login_state.get("running"):
            return {
                "ok": True,
                "started": False,
                "already_running": True,
                "login_url": str(_login_state.get("login_url") or ""),
                "snapshot": auth_snapshot(),
            }
        _login_state.update(
            {
                "running": True,
                "login_url": "",
                "error": "",
                "started_at": time.time(),
                "finished_at": 0.0,
                "email": "",
            }
        )

    def _worker() -> None:
        try:
            result = _run_node_auth("login", open_browser=open_browser, timeout=300.0)
            with _login_lock:
                _login_state["running"] = False
                _login_state["finished_at"] = time.time()
                if result.get("ok"):
                    _login_state["email"] = str(result.get("email") or "")
                    _login_state["error"] = ""
                    if result.get("login_url"):
                        _login_state["login_url"] = str(result.get("login_url") or "")
                else:
                    _login_state["error"] = str(result.get("error") or "登录失败")
                    if result.get("login_url"):
                        _login_state["login_url"] = str(result.get("login_url") or "")
        except Exception as exc:  # noqa: BLE001
            with _login_lock:
                _login_state["running"] = False
                _login_state["finished_at"] = time.time()
                _login_state["error"] = str(exc)

    threading.Thread(target=_worker, name="cursor-auth-login", daemon=True).start()
    return {
        "ok": True,
        "started": True,
        "already_running": False,
        "snapshot": auth_snapshot(),
    }
