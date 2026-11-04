# term:会话通道  doc:doc/功能/会话通道/说明.md
"""进度 / 回放 / 工具回灌文案的口令处理。

当前策略（暂定）：``redact_secrets`` **原样透传**，不做替换。

原因：``云服务重要信息.md`` 等「ssh 行下一行裸口令」被脱敏成 ``***`` 后，规划
history 读不到凭据，会误判「配置缺密码」并空 SSH。调用点仍经本函数，便于以后
恢复。

后续安全（未做）：宜拆「仅 UI/进度脱敏」与「规划回灌保留可用口令」；恢复时把
``_redact_secrets_impl`` 接回 ``redact_secrets``，或按通道分流。
"""
from __future__ import annotations

import re

# --- 历史实现保留，供后续安全分流恢复（当前未调用）---

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
    """历史脱敏实现；当前未启用。"""
    s = text or ""
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    s = _redact_ssh_followup_password(s)
    return s


def redact_secrets(text: str) -> str:
    """口令处理入口：暂透传（见模块说明）。恢复脱敏时改为 ``return _redact_secrets_impl(text)``。"""
    return text or ""
