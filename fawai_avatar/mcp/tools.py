# term:MCP 外挂工具  tool:mcp__  doc:doc/功能/MCP外挂工具/说明.md
"""控制层 MCP 执行入口。"""

from __future__ import annotations

from typing import Any

from fawai_avatar.mcp.client import call_mcp_tool
from fawai_avatar.mcp.registry import McpRegistry, parse_mcp_action_kind
from fawai_avatar.types import Action, ExecResult


class McpTools:
    def __init__(self, registry: McpRegistry | None = None) -> None:
        self.registry = registry or McpRegistry()

    def execute(self, action: Action) -> ExecResult:
        parsed = parse_mcp_action_kind(action.kind or "")
        if parsed is None:
            return ExecResult(ok=False, message=f"invalid mcp kind: {action.kind!r}")
        server_id, tool_sanitized = parsed
        args = dict(action.args or {})
        # 允许显式 tool 覆盖 sanitized（原始名）
        tool_name = str(args.pop("tool", None) or args.pop("name", None) or tool_sanitized)
        # 其余 args 作为 MCP arguments；若整包在 arguments 键下则用该键
        if isinstance(args.get("arguments"), dict):
            arguments = dict(args.get("arguments") or {})
        else:
            arguments = {
                k: v
                for k, v in args.items()
                if k not in {"server", "server_id"}
            }
        result = call_mcp_tool(
            self.registry, server_id, tool_name, arguments=arguments
        )
        if not result.get("ok"):
            return ExecResult(
                ok=False,
                message=str(result.get("error") or result.get("message") or "mcp failed"),
                data=result,
            )
        return ExecResult(
            ok=True,
            message=str(result.get("message") or f"mcp {server_id}/{tool_name}"),
            data=result,
        )
