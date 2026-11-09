# term:MCP 外挂工具  tool:mcp__  doc:doc/功能/MCP外挂工具/说明.md
"""MCP stdio 外挂：注册表、客户端、动态能力与执行。"""

from fawai_avatar.mcp.capabilities_dyn import list_mcp_capabilities, parse_mcp_action_kind
from fawai_avatar.mcp.registry import McpRegistry
from fawai_avatar.mcp.tools import McpTools

__all__ = [
    "McpRegistry",
    "McpTools",
    "list_mcp_capabilities",
    "parse_mcp_action_kind",
]
