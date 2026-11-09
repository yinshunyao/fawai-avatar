# term:Fast  tool:brain_fast  doc:doc/功能/Fast/说明.md
"""任务极速开关：Fast 开/关（默认关）。

开时：关深度思考、跳过观察反省、禁止进计划模式（单 Worker）。
与 Effort（全力）解耦，可各自开/关；不等于换模型 / 快模型委派角色 / 关执行反省硬闸。
"""

from __future__ import annotations

from typing import Any


def coerce_fast(value: Any) -> bool:
    """把任意入参收成 bool；缺省 / 无法识别 → False（Fast 关）。"""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    s = str(value or "").strip().lower()
    if not s:
        return False
    if s in {"1", "true", "yes", "on", "fast"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return False


def fast_from_meta(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return coerce_fast(meta.get("fast"))


def fast_from_inbound_meta(meta: dict[str, Any] | None) -> bool | None:
    """入站 meta 是否显式带 fast。未带则返回 None（调用方决定是否继承任务）。"""
    if not isinstance(meta, dict) or "fast" not in meta:
        return None
    return coerce_fast(meta.get("fast"))


def planner_fast_rule(*, fast: bool) -> str:
    if not fast:
        return ""
    return (
        "- **Fast 开（极速）**：少推理、直接选工具行动；"
        "不展开长链式思考；优先最短必要路径完成验收。"
        "不等于降低正确性硬闸（lease / shell 禁绕过 fs / 取消等仍有效）；"
        "不等于关执行反省；失败仍可 reflect 后换道。\n"
    )
