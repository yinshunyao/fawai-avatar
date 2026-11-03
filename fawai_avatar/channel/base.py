# term:会话通道  doc:doc/功能/会话通道/说明.md
from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from fawai_avatar.channel.message import Message

OutboundHandler = Callable[[Message], Awaitable[None] | None]


class SessionChannel(Protocol):
    """会话通道：只收发消息信封，不操控键鼠。"""

    name: str

    async def send(self, message: Message) -> None:
        """向已连接的用户端推送出站消息。"""

    def on_outbound(self, handler: OutboundHandler) -> None:
        """注册出站旁路（例如 hub 记账）；可选。"""
