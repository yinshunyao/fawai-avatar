# term:MCP 外挂工具  doc:doc/功能/MCP外挂工具/说明.md
"""MCP 注册表 / 动态能力 / 执行分发冒烟（可不连真 MCP server）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fawai_avatar.mcp.capabilities_dyn import list_mcp_capabilities
from fawai_avatar.mcp.client import mcp_sdk_available
from fawai_avatar.mcp.registry import (
    McpRegistry,
    mcp_action_kind,
    parse_mcp_action_kind,
)
from fawai_avatar.mcp.tools import McpTools
from fawai_avatar.types import Action


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = McpRegistry(Path(tmp))
        server = reg.upsert_server(
            {
                "id": "echo_demo",
                "title": "Echo",
                "command": "python",
                "args": ["-c", "print('noop')"],
                "enabled": True,
            }
        )
        assert server["id"] == "echo_demo"
        reg.set_tools_cache(
            "echo_demo",
            [
                {
                    "name": "ping-tool",
                    "description": "ping",
                    "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
                }
            ],
        )
        tools = reg.list_discovered_tools()
        assert len(tools) == 1
        kind = tools[0]["action_kind"]
        assert kind == mcp_action_kind("echo_demo", "ping-tool")
        parsed = parse_mcp_action_kind(kind)
        assert parsed == ("echo_demo", "ping_tool")

        caps = list_mcp_capabilities(reg)
        assert any(c.action_kind == kind for c in caps)

        reg.set_enabled("echo_demo", False)
        caps2 = list_mcp_capabilities(reg, available_only=True)
        assert not any(c.action_kind == kind for c in caps2)

        # 执行：无真 MCP 会话时 call 失败可读
        reg.set_enabled("echo_demo", True)
        mcp_tools = McpTools(reg)
        result = mcp_tools.execute(Action(kind=kind, args={"msg": "hi"}))
        # 假 command 不是 MCP server → ok False，但不应抛异常
        assert result.ok is False
        assert result.message

        assert reg.delete_server("echo_demo") is True
        print(
            "demo_mcp: OK",
            json.dumps({"sdk": mcp_sdk_available(), "kind": kind}, ensure_ascii=False),
        )


if __name__ == "__main__":
    main()
