# term:会话通道  doc:doc/功能/会话通道/说明.md
"""进度 / 回放文案脱敏：口令、sshpass、expect send 等不进用户可见时间线。"""

from __future__ import annotations

import re

# 通用：避免把 sshpass / expect / 管道回显的口令再塞进时间线或 progress meta
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

# ssh 连接行下一行常为裸口令（无 key= 前缀）；避免配置 cat 原文进轨迹
_SSH_LINE = re.compile(
    r"(?im)^([ \t]*ssh\s+[^\n]*)$\n([^\s#][^\s\n]{5,80})$"
)


def _redact_ssh_followup_password(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        token = m.group(2)
        # 排除明显非口令：路径、URL、表格线、纯端口数字
        if token.startswith(("/", "./", "~/", "http://", "https://", "|", "+", "-", "#")):
            return m.group(0)
        if re.fullmatch(r"\d{2,5}", token):
            return m.group(0)
        return f"{m.group(1)}\n***"

    return _SSH_LINE.sub(repl, text or "")


def redact_secrets(text: str) -> str:
    """替换常见口令/密钥片段为 ***；无匹配则原样返回。"""
    s = text or ""
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    s = _redact_ssh_followup_password(s)
    return s
