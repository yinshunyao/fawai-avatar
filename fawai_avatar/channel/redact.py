# term:会话通道  doc:doc/功能/会话通道/说明.md
"""口令 / 敏感串处理：规划侧保留可读凭据，Web 出站强制脱敏。

分流（强制）：

- **后端处理 / 规划 history / 工具回灌**：``redact_secrets`` **透传**，不抠口令
  （否则「ssh 下一行裸口令」变成 ``***`` 后模型会误判缺密码）。
- **发给 Web 前端**（WebSocket、HTTP history/session/task 摘要等）：必须走
  ``redact_for_client`` / ``message_dict_for_client`` / ``redact_tree_for_client``。

落盘 ``session_history.jsonl`` 与任务 meta 仍可保留原文；脱敏只发生在出站序列化。
"""
from __future__ import annotations

import re
from typing import Any

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(sshpass\s+-p\s*)(['\"])([^'\"]*)\2", re.I), r"\1\2***\2"),
    (re.compile(r"(sshpass\s+-p\s+)(\S+)", re.I), r"\1***"),
    (re.compile(r"(echo\s+)(['\"])([^'\"]{6,})\2(\s*\|\s*sshpass)", re.I), r"\1\2***\2\4"),
    (re.compile(r"(send\s+)(['\"])([^'\"]+)\2", re.I), r"\1\2***\2"),
    (re.compile(r"(--password[= ]+)(\S+)", re.I), r"\1***"),
    (re.compile(r"((?:FAWAI_|SSH)?PASS(?:WORD|CODE)?|SSHPASS)\s*=\s*\S+", re.I), r"\1=***"),
    (re.compile(r"(密码\s*[:=：]?\s*)(\S+)"), r"\1***"),
    (re.compile(r"(password\s*[:=]\s*)(\S+)", re.I), r"\1***"),
    (re.compile(r"(passwd\s+)(\S+)", re.I), r"\1***"),
)

_SSH_LINE = re.compile(
    r"(?im)^([ \t]*ssh\s+[^\n]*)$\n([^\s#][^\s\n]{5,80})$"
)

# 出站 meta 里这些键即使未命中正则也整段脱敏（命令行常夹口令）
_CLIENT_FORCE_REDACT_KEYS = frozenset(
    {
        "cmd",
        "command",
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
    }
)


def _redact_ssh_followup_password(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        token = m.group(2)
        if token.startswith(("/", "./", "~/", "http://", "https://", "|", "+", "-", "#")):
            return m.group(0)
        if re.fullmatch(r"\d{2,5}", token):
            return m.group(0)
        return f"{m.group(1)}\n***"

    return _SSH_LINE.sub(repl, text or "")


def _redact_secrets_impl(text: str) -> str:
    """实际脱敏实现（Web 出站与 durable_memory 等显式调用）。"""
    s = text or ""
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    s = _redact_ssh_followup_password(s)
    return s


def redact_secrets(text: str) -> str:
    """规划 / 工具回灌入口：**透传**，保留可用凭据。"""
    return text or ""


def redact_for_client(text: str) -> str:
    """发给 Web / 中继前端的文本：强制脱敏。"""
    return _redact_secrets_impl(text or "")


def redact_tree_for_client(value: Any, *, key: str | None = None) -> Any:
    """递归脱敏 JSON 树；``key`` 命中强制键时对该字符串必跑脱敏。"""
    if isinstance(value, str):
        if key and key.lower() in _CLIENT_FORCE_REDACT_KEYS:
            return redact_for_client(value)
        return redact_for_client(value)
    if isinstance(value, list):
        return [redact_tree_for_client(v, key=key) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_tree_for_client(v, key=key) for v in value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            sk = str(k)
            out[sk] = redact_tree_for_client(v, key=sk)
        return out
    return value


def message_dict_for_client(message: Any) -> dict[str, Any]:
    """``Message.to_dict()`` 后再脱敏，供 WebSocket / HTTP 出站。不改原信封。"""
    if hasattr(message, "to_dict"):
        raw = message.to_dict()
    elif isinstance(message, dict):
        raw = dict(message)
    else:
        raise TypeError(f"unsupported message type: {type(message)!r}")
    return redact_tree_for_client(raw)
