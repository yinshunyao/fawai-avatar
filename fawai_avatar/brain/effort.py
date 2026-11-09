# term:Effort  tool:brain_effort  doc:doc/功能/Effort/说明.md
"""任务努力开关：Effort 开/关（默认关）。

开时收紧 enough_to_answer、可修复失败不得早退；关时保持平衡收束。
不等于换模型；不等于关闭硬闸。
"""

from __future__ import annotations

from typing import Any


def coerce_effort(value: Any) -> bool:
    """把任意入参收成 bool；缺省 / 无法识别 → False（Effort 关）。"""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    s = str(value or "").strip().lower()
    if not s:
        return False
    if s in {"1", "true", "yes", "on", "effort"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return False


def effort_from_meta(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return coerce_effort(meta.get("effort"))


def effort_from_inbound_meta(meta: dict[str, Any] | None) -> bool | None:
    """入站 meta 是否显式带 effort。未带则返回 None（调用方决定是否继承任务）。"""
    if not isinstance(meta, dict) or "effort" not in meta:
        return None
    return coerce_effort(meta.get("effort"))


def planner_effort_rule(*, effort: bool) -> str:
    if not effort:
        return (
            "- **Effort 关**（默认）：证据已覆盖当前验收口径时可收尾；"
            "可参考【观察反省】enough_to_answer，但仍须 thought 写清事实与是否未刷新。"
            "可修复失败（相对路径/cwd）建议先改路径再交，但不强制无限追。\n"
        )
    return (
        "- **Effort 开**：尽最大努力围绕目标完成必要路径。"
        "计划模式节点：把本节点目标当作完整交付，勿因父图仍在跑而降档糊弄。"
        "用户要求实时/重连/必须查现况时，过期基线不得当完成；"
        "【观察反省】若标 enough_to_answer 但必要路径未齐或证据不新鲜，须继续而非 success。"
        "相对路径/cwd/`no such file` 且上下文已有绝对路径或正解 → 必须改绝对路径/`cwd`/已定位铲子再试，"
        "禁止写成沙箱断网/工具族不可用并停止必要路径；"
        "仅真硬阻塞（认证失败且无第二凭证等）才 failed 并交付可复制命令。"
        "要 PPT/PDF/表格等交付物时，须落到对应可打开文件（如 `.pptx`），"
        "禁止仅用大纲 Markdown 当完成（用户明确只要提纲除外）。"
        "能力清单只约束 action_kind 选型，不限制模型生成正文/脚本/故事；"
        "禁止因「清单没有创意 skill / 编排元概念」而早退 failed——须生成并落盘后再 success。\n"
    )


def obs_reflect_effort_rule(*, effort: bool) -> str:
    if not effort:
        return (
            "Effort 关：证据已覆盖当前 goal 验收口径时可 enough_to_answer；"
            "用户未要求刷新时可用合理基线，hints 须提示是否未做本轮新观测。"
        )
    return (
        "Effort 开（强制谨慎）：默认倾向 advanced。"
        "仅当同时满足才可 enough_to_answer："
        "（1）必要路径均已尝试或硬阻塞已写明；"
        "（2）证据相对用户最新约束仍新鲜（追问「必须查现况/重新连」则旧 epoch/旧日志不得够用）；"
        "（3）最近失败不是可修复的 path/cwd/拼写（上下文已有绝对路径或正解时禁止够用）；"
        "（4）不得因压缩摘录不全而标够用。"
        "ENOENT/相对路径找不到 ≠ 工具不可用。"
    )


def reflect_effort_rule(*, effort: bool) -> str:
    if not effort:
        return ""
    return (
        "Effort 开：decision=alternate 时禁止要求「停止自动化连接/停止必要观测」仅因相对路径找不到或 cwd 错；"
        "constraints 须先要求用已知绝对路径、正确 cwd、或已定位铲子/ssh 换道重试。"
        "stderr 已是 no such file / Permission denied 时，禁止升成「沙箱断网 / expect 工具不可用」"
        "除非另有独立证据。可修复失败未换道前，不得引导规划用过期基线 success。"
    )


def ground_effort_constraints(
    constraints: list[str],
    *,
    effort: bool,
    err_blob: str = "",
) -> list[str]:
    """Effort 开时清洗：去掉「因路径问题停止必要路径」类约束，并补换道提示。"""
    if not effort:
        return list(constraints)
    err_low = (err_blob or "").lower()
    pathish = any(
        tok in err_low
        for tok in (
            "no such file",
            "not found",
            "couldn't read file",
            "no such directory",
            "errno 2",
        )
    )
    cleaned: list[str] = []
    dropped_stop = False
    for raw in constraints:
        c = str(raw or "").strip()
        if not c:
            continue
        low = c.lower()
        if pathish and any(
            tok in c
            for tok in (
                "停止尝试自动化",
                "停止自动",
                "停止 SSH",
                "停止连接",
                "禁止再连",
                "不要再连",
                "放弃自动",
                "工具不可用",
                "expect 不可用",
                "沙箱环境",
                "网络隔离",
            )
        ):
            dropped_stop = True
            continue
        if pathish and ("sandbox" in low and "stop" in low):
            dropped_stop = True
            continue
        if c not in cleaned:
            cleaned.append(c)
    if pathish or dropped_stop:
        fix = (
            "路径/cwd 类失败：用上下文已出现的绝对路径或正确 cwd 重试"
            "（或已定位的 ssh/铲子）；禁止写成环境/沙箱不可用并停止必要路径"
        )
        if fix not in cleaned:
            cleaned.insert(0, fix)
    return cleaned[:8]
