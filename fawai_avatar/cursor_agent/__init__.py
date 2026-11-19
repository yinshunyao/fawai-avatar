# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""Cursor Agent：设置、SDK 封装与会话管理（独立于 LLM 聊天供应商）。"""
from __future__ import annotations

from fawai_avatar.cursor_agent.settings_store import (
    CursorAgentSettingsStore,
    get_store,
)

__all__ = ["CursorAgentSettingsStore", "get_store"]
