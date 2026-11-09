# term:MCP 外挂工具  doc:doc/功能/MCP外挂工具/说明.md
"""把已发现 MCP tools 映射为动态 Capability。"""

from __future__ import annotations

from typing import Any

from fawai_avatar.brain.capabilities import Capability
from fawai_avatar.mcp.registry import McpRegistry, parse_mcp_action_kind


def list_mcp_capabilities(
    registry: McpRegistry | None = None,
    *,
    available_only: bool = True,
) -> list[Capability]:
    reg = registry or McpRegistry()
    out: list[Capability] = []
    for tool in reg.list_discovered_tools(enabled_servers_only=available_only):
        desc = tool.get("description") or ""
        when = desc.strip() or f"MCP tool {tool['name']} on server {tool['server_id']}"
        schema = tool.get("input_schema") or {}
        props = ""
        if isinstance(schema, dict) and schema.get("properties"):
            keys = list((schema.get("properties") or {}).keys())[:12]
            if keys:
                props = "；args 字段：" + ", ".join(str(k) for k in keys)
        out.append(
            Capability(
                id=str(tool["action_kind"]),
                name=f"MCP·{tool['server_id']}·{tool['name']}",
                modality="background",
                when_to_use=(when + props)[:500],
                action_kind=str(tool["action_kind"]),
                needs_foreground=False,
                write_resources="none",
                available=True,
            )
        )
    return out


def mcp_prompt_extra(registry: McpRegistry | None = None) -> str:
    caps = list_mcp_capabilities(registry)
    if not caps:
        return ""
    lines = [
        "MCP 外挂工具（stdio；优先内置能力，仅当清单含下列 id 时调用）：",
    ]
    for c in caps[:40]:
        lines.append(f"- {c.action_kind}: {c.when_to_use}")
    if len(caps) > 40:
        lines.append(f"…共 {len(caps)} 个 MCP 工具")
    return "\n".join(lines)


__all__ = ["list_mcp_capabilities", "parse_mcp_action_kind", "mcp_prompt_extra"]
