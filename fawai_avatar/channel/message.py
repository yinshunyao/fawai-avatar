# term:消息信封  doc:doc/功能/会话通道/说明.md
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# goal / 气泡里嵌入的附图标记：`[image:/media/…]` 或本地绝对路径
_IMAGE_MARK_RE = re.compile(r"\[image:([^\]]+)\]", re.IGNORECASE)


def extract_image_marks(text: str) -> tuple[str, list[str]]:
    """剥开 `[image:…]`：返回 (纯文本, 引用列表)。"""
    raw = text or ""
    refs = [m.group(1).strip() for m in _IMAGE_MARK_RE.finditer(raw) if m.group(1).strip()]
    cleaned = _IMAGE_MARK_RE.sub("", raw)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, refs


def strip_image_marks(text: str) -> str:
    """展示用：去掉附图标记，只留可读正文。"""
    return extract_image_marks(text)[0]


MessageKind = Literal[
    "text",
    "image",
    "control",
    "progress",
    "card",
    "error",
    "audio",
    "file",
]
MessageRole = Literal["user", "assistant", "system"]


@dataclass
class Message:
    """通道无关的统一消息信封。"""

    kind: MessageKind
    role: MessageRole = "user"
    text: str = ""
    media_path: str | None = None
    media_url: str | None = None
    task_id: str | None = None
    control: str | None = None  # pause | resume | cancel
    meta: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        kind = str(data.get("kind") or "text")
        role = str(data.get("role") or "user")
        return cls(
            kind=kind,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            text=str(data.get("text") or ""),
            media_path=data.get("media_path"),
            media_url=data.get("media_url"),
            task_id=data.get("task_id"),
            control=data.get("control"),
            meta=dict(data.get("meta") or {}),
            message_id=str(data.get("message_id") or uuid.uuid4().hex),
            ts=float(data.get("ts") or time.time()),
        )

    @classmethod
    def text_user(cls, text: str, *, task_id: str | None = None) -> "Message":
        return cls(kind="text", role="user", text=text, task_id=task_id)

    @classmethod
    def text_assistant(cls, text: str, *, task_id: str | None = None) -> "Message":
        return cls(kind="text", role="assistant", text=text, task_id=task_id)

    @classmethod
    def image_out(
        cls,
        *,
        media_path: str | None = None,
        media_url: str | None = None,
        text: str = "",
        task_id: str | None = None,
    ) -> "Message":
        return cls(
            kind="image",
            role="assistant",
            text=text,
            media_path=media_path,
            media_url=media_url,
            task_id=task_id,
        )

    @classmethod
    def progress(cls, text: str, *, task_id: str | None = None, meta: dict[str, Any] | None = None) -> "Message":
        return cls(kind="progress", role="assistant", text=text, task_id=task_id, meta=dict(meta or {}))

    @classmethod
    def card(cls, text: str, *, task_id: str | None = None, meta: dict[str, Any] | None = None) -> "Message":
        return cls(kind="card", role="assistant", text=text, task_id=task_id, meta=dict(meta or {}))

    @classmethod
    def error(cls, text: str, *, task_id: str | None = None) -> "Message":
        return cls(kind="error", role="system", text=text, task_id=task_id)

    @classmethod
    def control_cmd(cls, control: str, *, task_id: str | None = None, text: str = "") -> "Message":
        return cls(
            kind="control",
            role="user",
            control=control,
            task_id=task_id,
            text=text or control,
        )
