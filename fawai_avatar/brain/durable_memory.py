# term:规划历史窗  doc:doc/功能/编码闭环/说明.md
"""会反复注入的摘要和待办：去掉口令，并禁止把「读过」写成「禁读」。"""

from __future__ import annotations

from fawai_avatar.channel.redact import _redact_secrets_impl

_FORBID = ("禁止再读", "勿重复盲读", "不要再读", "勿再盲读", "不得再读")
_NOTE = "口令未写入这份记忆；下一步从原配置文件读进环境，不要向用户索要。"


def strip_forbid_reread(text: str) -> str:
    kept: list[str] = []
    for line in (text or "").splitlines():
        cleaned = line
        for phrase in _FORBID:
            cleaned = cleaned.replace(phrase, "")
        cleaned = cleaned.strip(" ，。;；")
        if cleaned:
            kept.append(cleaned)
    return "\n".join(kept).strip()


def durable_memory_text(text: str, *, with_note: bool = True) -> str:
    raw = text or ""
    redacted = _redact_secrets_impl(raw)
    cleaned = strip_forbid_reread(redacted)
    if with_note and redacted != raw and _NOTE not in cleaned:
        cleaned = (cleaned + "\n" + _NOTE).strip()
    return cleaned
