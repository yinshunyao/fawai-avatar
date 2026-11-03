# term:云端中继  doc:doc/功能/云端中继/说明.md
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fawai_avatar.channel.message import Message

logger = logging.getLogger(__name__)

InboundHandler = Callable[[Message], Awaitable[None] | None]


class CloudRelayClient:
    """云端中继出站挂载骨架：同消息信封；只转发不做编排。

    未配置 `relay_url` 时为 no-op。后续可换成托管隧道或自建转发，保持 `send` / `on_message`。
    """

    name = "cloud_relay"

    def __init__(
        self,
        *,
        relay_url: str | None = None,
        device_token: str | None = None,
        pair_code: str | None = None,
    ) -> None:
        self.relay_url = (relay_url or "").strip() or None
        self.device_token = device_token
        self.pair_code = pair_code
        self._handler: InboundHandler | None = None
        self._connected = False

    @property
    def enabled(self) -> bool:
        return bool(self.relay_url)

    @property
    def connected(self) -> bool:
        return self._connected

    def on_message(self, handler: InboundHandler) -> None:
        self._handler = handler

    async def connect(self) -> bool:
        if not self.enabled:
            logger.info("cloud relay skipped (FAWAI_RELAY_URL unset)")
            return False
        # 骨架：记录意图，不强制连真实服务
        logger.info(
            "cloud relay connect stub url=%s pair=%s",
            self.relay_url,
            (self.pair_code or "")[:4] + "…" if self.pair_code else None,
        )
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, message: Message) -> None:
        if not self._connected:
            return
        # 真实实现：经出站长连接把 message.to_dict() 发给中继
        logger.debug("relay send stub kind=%s id=%s", message.kind, message.message_id)

    async def inject_inbound_for_test(self, message: Message) -> None:
        """测试/本地联调用：模拟中继把手机帧转发给本机。"""
        if self._handler is None:
            return
        result = self._handler(message)
        if asyncio_is_awaitable(result):
            await result  # type: ignore[misc]


def asyncio_is_awaitable(value: Any) -> bool:
    import asyncio
    from collections.abc import Awaitable

    return asyncio.iscoroutine(value) or isinstance(value, Awaitable)


def relay_from_env() -> CloudRelayClient:
    import os

    from fawai_avatar.config import load_dotenv

    load_dotenv()
    return CloudRelayClient(
        relay_url=os.environ.get("FAWAI_RELAY_URL"),
        device_token=os.environ.get("FAWAI_RELAY_DEVICE_TOKEN"),
        pair_code=os.environ.get("FAWAI_RELAY_PAIR_CODE"),
    )
