# term:MCP 外挂工具  doc:doc/功能/MCP外挂工具/说明.md
"""HTTP helpers for MCP server CRUD / refresh."""

from __future__ import annotations

from typing import Any

from fawai_avatar.mcp.client import mcp_sdk_available, refresh_server_tools
from fawai_avatar.mcp.registry import McpRegistry, McpRegistryError


def _reg() -> McpRegistry:
    return McpRegistry()


def mcp_status() -> dict[str, Any]:
    reg = _reg()
    servers = reg.list_servers()
    return {
        "ok": True,
        "sdk_available": mcp_sdk_available(),
        "servers": servers,
        "count": len(servers),
        "root": str(reg.root),
        "tools": reg.list_discovered_tools(enabled_servers_only=False),
    }


def mcp_list_servers() -> dict[str, Any]:
    reg = _reg()
    return {"ok": True, "servers": reg.list_servers(), "root": str(reg.root)}


def mcp_upsert_server(body: dict[str, Any]) -> dict[str, Any]:
    try:
        server = _reg().upsert_server(body or {})
    except McpRegistryError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "server": server}


def mcp_enable_server(server_id: str, *, enabled: bool) -> dict[str, Any]:
    server = _reg().set_enabled(server_id, enabled)
    if server is None:
        return {"ok": False, "error": f"server not found: {server_id}"}
    return {"ok": True, "server": server}


def mcp_delete_server(server_id: str) -> dict[str, Any]:
    ok = _reg().delete_server(server_id)
    if not ok:
        return {"ok": False, "error": f"server not found: {server_id}"}
    return {"ok": True, "deleted": server_id}


def mcp_refresh_server(server_id: str) -> dict[str, Any]:
    return refresh_server_tools(_reg(), server_id)


def mcp_list_tools(server_id: str | None = None) -> dict[str, Any]:
    reg = _reg()
    if server_id:
        entry = reg.get_tools_cache(server_id)
        return {
            "ok": True,
            "server_id": server_id,
            "tools": (entry.get("tools") if isinstance(entry, dict) else []) or [],
            "error": entry.get("error") if isinstance(entry, dict) else None,
        }
    return {
        "ok": True,
        "tools": reg.list_discovered_tools(enabled_servers_only=False),
    }
