# term:MCP 外挂工具  doc:doc/功能/MCP外挂工具/说明.md
"""MCP server 配置与已发现 tools 缓存。"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
_LOCK = threading.RLock()


class McpRegistryError(ValueError):
    """配置非法。"""


def default_mcp_dir(data_dir: str | Path | None = None) -> Path:
    if data_dir is None:
        from fawai_avatar.config import RuntimeConfig

        data_dir = RuntimeConfig.from_env().data_dir
    root = Path(data_dir) / "mcp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_tool_name(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s[:80] or "tool"


def mcp_action_kind(server_id: str, tool_name: str) -> str:
    return f"mcp__{server_id}__{sanitize_tool_name(tool_name)}"


def parse_mcp_action_kind(kind: str) -> tuple[str, str] | None:
    raw = (kind or "").strip().lower()
    if not raw.startswith("mcp__"):
        return None
    parts = raw.split("__", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _as_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def validate_server(raw: dict[str, Any]) -> dict[str, Any]:
    sid = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.match(sid):
        raise McpRegistryError("server id must match [a-z0-9][a-z0-9_-]{1,48}")
    command = str(raw.get("command") or "").strip()
    if not command:
        raise McpRegistryError("command required")
    args = raw.get("args") or []
    if isinstance(args, str):
        args = [a for a in args.split() if a]
    if not isinstance(args, list):
        raise McpRegistryError("args must be a list")
    arg_list = [str(a) for a in args][:64]
    env = raw.get("env") or {}
    if not isinstance(env, dict):
        raise McpRegistryError("env must be an object")
    env_map = {str(k): str(v) for k, v in env.items() if str(k).strip()}
    cwd = str(raw.get("cwd") or "").strip() or None
    enabled = _as_bool(raw.get("enabled"), default=True)
    title = str(raw.get("title") or sid).strip()[:120]
    return {
        "id": sid,
        "title": title,
        "command": command,
        "args": arg_list,
        "env": env_map,
        "cwd": cwd,
        "enabled": enabled,
    }


class McpRegistry:
    def __init__(self, root: str | Path | None = None) -> None:
        self._root = default_mcp_dir(root) if root is None else Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._servers_path = self._root / "servers.json"
        self._tools_path = self._root / "tools_cache.json"

    @property
    def root(self) -> Path:
        return self._root

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_servers(self) -> list[dict[str, Any]]:
        with _LOCK:
            raw = self._read_json(self._servers_path, {"servers": []})
            servers = raw.get("servers") if isinstance(raw, dict) else []
            if not isinstance(servers, list):
                return []
            out: list[dict[str, Any]] = []
            for item in servers:
                if isinstance(item, dict) and item.get("id"):
                    try:
                        out.append(validate_server(item))
                    except McpRegistryError:
                        continue
            return out

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        sid = str(server_id or "").strip().lower()
        for s in self.list_servers():
            if s["id"] == sid:
                return s
        return None

    def upsert_server(self, raw: dict[str, Any]) -> dict[str, Any]:
        server = validate_server(raw)
        with _LOCK:
            servers = self.list_servers()
            replaced = False
            for i, s in enumerate(servers):
                if s["id"] == server["id"]:
                    servers[i] = server
                    replaced = True
                    break
            if not replaced:
                servers.append(server)
            self._write_json(self._servers_path, {"servers": servers})
        return server

    def set_enabled(self, server_id: str, enabled: bool) -> dict[str, Any] | None:
        server = self.get_server(server_id)
        if server is None:
            return None
        server["enabled"] = bool(enabled)
        return self.upsert_server(server)

    def delete_server(self, server_id: str) -> bool:
        sid = str(server_id or "").strip().lower()
        with _LOCK:
            before = self.list_servers()
            servers = [s for s in before if s["id"] != sid]
            if len(servers) == len(before):
                return False
            self._write_json(self._servers_path, {"servers": servers})
            cache = self._read_json(self._tools_path, {})
            if isinstance(cache, dict) and sid in cache:
                cache.pop(sid, None)
                self._write_json(self._tools_path, cache)
        return True

    def set_tools_cache(
        self, server_id: str, tools: list[dict[str, Any]], *, error: str | None = None
    ) -> None:
        sid = str(server_id or "").strip().lower()
        with _LOCK:
            cache = self._read_json(self._tools_path, {})
            if not isinstance(cache, dict):
                cache = {}
            cache[sid] = {
                "tools": tools,
                "error": error,
            }
            self._write_json(self._tools_path, cache)

    def get_tools_cache(self, server_id: str | None = None) -> dict[str, Any]:
        with _LOCK:
            cache = self._read_json(self._tools_path, {})
            if not isinstance(cache, dict):
                return {}
            if server_id is None:
                return cache
            sid = str(server_id).strip().lower()
            entry = cache.get(sid)
            return entry if isinstance(entry, dict) else {}

    def list_discovered_tools(
        self, *, enabled_servers_only: bool = True
    ) -> list[dict[str, Any]]:
        """Flatten tools with server_id / action_kind / original name."""
        enabled_ids = {
            s["id"]
            for s in self.list_servers()
            if (not enabled_servers_only) or s.get("enabled", True)
        }
        out: list[dict[str, Any]] = []
        cache = self.get_tools_cache()
        for sid, entry in cache.items():
            if sid not in enabled_ids:
                continue
            if not isinstance(entry, dict):
                continue
            tools = entry.get("tools") or []
            if not isinstance(tools, list):
                continue
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                if not name:
                    continue
                out.append(
                    {
                        "server_id": sid,
                        "name": name,
                        "sanitized": sanitize_tool_name(name),
                        "action_kind": mcp_action_kind(sid, name),
                        "description": str(tool.get("description") or "")[:500],
                        "input_schema": tool.get("inputSchema")
                        or tool.get("input_schema")
                        or {},
                    }
                )
        return out
