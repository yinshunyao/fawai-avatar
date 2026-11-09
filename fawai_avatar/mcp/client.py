# term:MCP 外挂工具  doc:doc/功能/MCP外挂工具/说明.md
"""MCP stdio 客户端：list_tools / call_tool（同步封装）。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fawai_avatar.mcp.registry import McpRegistry

logger = logging.getLogger(__name__)


def mcp_sdk_available() -> bool:
    try:
        import mcp  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401

        return True
    except Exception:
        return False


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已在事件循环内（如 FastAPI）：新线程跑独立 loop
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result(timeout=120)


async def _session_work(
    server: dict[str, Any],
    *,
    list_only: bool = False,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    for k, v in (server.get("env") or {}).items():
        env[str(k)] = str(v)
    params = StdioServerParameters(
        command=str(server["command"]),
        args=list(server.get("args") or []),
        env=env,
        cwd=server.get("cwd") or None,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if list_only:
                listed = await session.list_tools()
                tools = []
                for t in listed.tools:
                    tools.append(
                        {
                            "name": t.name,
                            "description": getattr(t, "description", None) or "",
                            "inputSchema": getattr(t, "inputSchema", None) or {},
                        }
                    )
                return {"ok": True, "tools": tools}
            assert tool_name
            result = await session.call_tool(
                tool_name, arguments=arguments or {}
            )
            # 序列化 content
            content_out: list[Any] = []
            is_error = bool(getattr(result, "isError", False))
            for block in getattr(result, "content", None) or []:
                btype = getattr(block, "type", None) or type(block).__name__
                text = getattr(block, "text", None)
                if text is not None:
                    content_out.append({"type": btype, "text": text})
                else:
                    try:
                        content_out.append(
                            block.model_dump()
                            if hasattr(block, "model_dump")
                            else {"type": str(btype), "raw": str(block)}
                        )
                    except Exception:
                        content_out.append({"type": str(btype), "raw": str(block)})
            texts = [
                str(c.get("text") or "")
                for c in content_out
                if isinstance(c, dict) and c.get("text")
            ]
            message = "\n".join(texts) if texts else ("mcp error" if is_error else "mcp ok")
            return {
                "ok": not is_error,
                "message": message[:4000],
                "content": content_out,
                "is_error": is_error,
            }


def refresh_server_tools(
    registry: McpRegistry, server_id: str
) -> dict[str, Any]:
    server = registry.get_server(server_id)
    if server is None:
        return {"ok": False, "error": f"server not found: {server_id}"}
    if not mcp_sdk_available():
        err = "mcp SDK not installed; pip install mcp"
        registry.set_tools_cache(server_id, [], error=err)
        return {"ok": False, "error": err}
    try:
        result = _run_async(_session_work(server, list_only=True))
    except Exception as exc:  # noqa: BLE001
        logger.exception("mcp refresh failed: %s", server_id)
        err = str(exc)
        registry.set_tools_cache(server_id, [], error=err)
        return {"ok": False, "error": err, "server_id": server_id}
    tools = result.get("tools") or []
    registry.set_tools_cache(server_id, tools, error=None)
    return {
        "ok": True,
        "server_id": server_id,
        "tools": tools,
        "count": len(tools),
    }


def call_mcp_tool(
    registry: McpRegistry,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    server = registry.get_server(server_id)
    if server is None:
        return {"ok": False, "error": f"server not found: {server_id}"}
    if not server.get("enabled", True):
        return {"ok": False, "error": f"server disabled: {server_id}"}
    if not mcp_sdk_available():
        return {"ok": False, "error": "mcp SDK not installed; pip install mcp"}
    # 校验 tool 在缓存中（若有缓存）
    cache = registry.get_tools_cache(server_id)
    cached_tools = cache.get("tools") if isinstance(cache, dict) else None
    if isinstance(cached_tools, list) and cached_tools:
        names = {str(t.get("name") or "") for t in cached_tools if isinstance(t, dict)}
        # 允许原始名或 sanitized 匹配
        from fawai_avatar.mcp.registry import sanitize_tool_name

        resolved = None
        for n in names:
            if n == tool_name or sanitize_tool_name(n) == tool_name:
                resolved = n
                break
        if resolved is None:
            return {
                "ok": False,
                "error": f"tool not in cache: {tool_name} (refresh server first)",
            }
        tool_name = resolved
    try:
        result = _run_async(
            _session_work(
                server,
                tool_name=tool_name,
                arguments=arguments or {},
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp call failed: %s %s: %s", server_id, tool_name, exc)
        return {"ok": False, "error": str(exc)}
    return result
