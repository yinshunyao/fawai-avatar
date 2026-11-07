# term:必要路径台账  doc:doc/功能/完成任务/说明.md
# term:大脑  doc:doc/功能/大脑/说明.md
# term:执行反省  doc:doc/功能/执行反省/说明.md
"""多前置目标：必要路径台账、偏科回灌、failed 分项/交付闸。

抽象槽位（鉴权材料 / 作用对象或脚本 / 副作用动作），不绑业务专名。
"""

from __future__ import annotations

import os
import re

from fawai_avatar.brain.reflect import Reflection

PATH_LEDGER_MARKER = "【必要路径台账】"
PATH_LEDGER_FAIL_MARKER = "【必要路径收尾】"

CONSTRAINT_PATH_LEDGER = (
    "遵守必要路径台账：先分清必要路径 vs 可选路径；"
    "各必要路径均须尝试或标明阻塞；一条阻塞时不得遗忘其他已就绪必要路径——"
    "须继续推进副作用，或 status=failed 时按路径分项说明并交付已齐材料上的可执行步骤；"
    "改写/作答所依赖的证据须可回读原文（小窗 fs_read 或压缩 dump）；"
    "禁止把宽搜/反复读说明等可选探索当成唯一进展；"
    "实机截屏/桌面 Chrome/浏览器验页属可选，不得升成必要路径；"
    "已 mutate 时可用 thought 对照 diff 说明可见变化后 success，验不了不得因此 failed"
)

# 反省常把「截屏验手机」升成硬必要并逼 failed——清洗为可选验收
_FORCE_VISUAL_VERIFY_HINTS = (
    "须验证移动端",
    "必须截屏",
    "须截屏",
    "截屏验证",
    "验证移动端布局",
    "验证手机端",
    "缺乏视觉反馈",
    "无法获取Chrome",
    "无法获取 chrome",
    "无法获取窗口画面",
    "画面验证",
    "视觉验证",
    "实机验证",
)

_VISUAL_VERIFY_OPTIONAL = (
    "实机截屏/桌面点 Chrome/浏览器打开属可选验收，不得升成必要路径；"
    "已有 mutate 时 thought 对照 diff/@media 说明可见变化即可 success；"
    "若要验窄屏用 browser_navigate(viewport=mobile)→snapshot；做不到不得因此 failed"
)


def scrub_forced_visual_verify_constraints(constraints: list[str] | None) -> list[str]:
    """丢掉「必须截屏/验移动端否则 failed」类约束，换成可选验收口径。"""
    out: list[str] = []
    replaced = False
    for raw in constraints or []:
        text = str(raw or "").strip()
        if not text:
            continue
        low = text.lower()
        if any(h.lower() in low for h in _FORCE_VISUAL_VERIFY_HINTS):
            if not replaced:
                out.append(_VISUAL_VERIFY_OPTIONAL)
                replaced = True
            continue
        out.append(text)
    return out

# 材料/落点类信号（抽象，非专名）
_MATERIAL_HINTS = (
    "配置",
    "材料",
    "文档",
    "说明",
    "密码",
    "口令",
    "账号",
    "地址",
    "路径",
    "磁盘",
    "凭据",
    "密钥",
    "根据",
    "基于",
    "用到",
    "落点",
    "env",
    "credential",
    "password",
    "config",
)

# 副作用/作用对象类信号
_EFFECT_HINTS = (
    "升级",
    "部署",
    "同步",
    "推送",
    "发布",
    "上传",
    "安装",
    "改",
    "修",
    "写",
    "执行",
    "完成",
    "更新",
    "迁移",
    "deploy",
    "upgrade",
    "sync",
    "push",
)

_BREAKDOWN_HINTS = (
    "必要",
    "可选",
    "已齐",
    "已具备",
    "已就绪",
    "阻塞",
    "分项",
    "路径甲",
    "路径乙",
    "一是",
    "二是",
    "其一",
    "其二",
    "（1）",
    "（2）",
    "(1)",
    "(2)",
    "1.",
    "2.",
    "1、",
    "2、",
)

_DELIVERY_CLAIM = (
    "给出",
    "可复制",
    "请在本机",
    "本机执行",
    "命令如下",
    "待用户",
    "自行执行",
    "供用户",
    "复制命令",
    "完整命令",
)

_CMD_BODY = re.compile(
    r"(?:"
    r"```"
    r"|^\s*(?:\$|#)\s*\S"
    r"|\b(?:bash|ssh|scp|rsync|curl|wget|python3?|npm|pip)\s+"
    r"|\bDEPLOY_[A-Z0-9_]+\s*="
    r"|\bexport\s+\w+"
    r")",
    re.I | re.M,
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def path_ledger_enabled() -> bool:
    return _env_flag("FAWAI_PATH_LEDGER", True)


def goal_needs_path_ledger_gate(goal: str) -> bool:
    """目标同时暗示「材料/落点」与「变更/副作用」→ 多必要路径。"""
    if not path_ledger_enabled():
        return False
    g = (goal or "").strip()
    if not g:
        return False
    low = g.lower()
    has_material = any(h.lower() in low for h in _MATERIAL_HINTS)
    has_effect = any(h.lower() in low for h in _EFFECT_HINTS)
    return has_material and has_effect


def path_ledger_prompt_lines(*, short: bool = False) -> str:
    """注入规划系统提示的必要路径规则。"""
    if not path_ledger_enabled():
        return ""
    if short:
        return (
            "- **必要路径台账**：多前置目标须分清必要 vs 可选；槽位含材料/落点、作用对象、副作用、"
            "**证据保真**（改写或答复依赖的原文：小窗 fs_read / 压缩 dump 回读）；"
            "各必要路径都要尝试或标明阻塞；一条阻塞时勿遗忘已齐路径；"
            "压缩摘要 ≠ 取证完成；禁止只用可选宽搜交差。\n"
        )
    return (
        "- **必要路径台账**：开场与重规划须分清**必要路径**与**可选路径**"
        "（抽象槽位：鉴权/落点材料、作用对象或脚本、副作用动作、**证据保真**——"
        "改/修/patch 或面向用户作答所依赖的可回读原文；观察已压缩时 dump/`path:line` 小窗回读为必要；"
        "勿只记业务专名）。"
        "各必要路径均须尝试或明确阻塞；一条阻塞时不得偏科遗忘其他已就绪必要路径——"
        "须继续推进可验收副作用，或 `status=failed` 时按路径**分项**说明"
        "（已齐 / 阻塞原因），若声称交命令则 thought 须含可复制命令。"
        "宽搜、反复读同一说明、实机截屏/浏览器验页等属可选，不得替代必要路径进展，"
        "也不得因可选验收做不到就整任务 failed；压缩摘要不得当作证据保真已齐。\n"
    )


def enrich_reflection_path_ledger(reflection: Reflection) -> Reflection:
    """alternate/abort 时追加台账约束，防止反省把恢复收成单线偏科。"""
    if not path_ledger_enabled():
        # 仍清洗「强制视觉验收」——不依赖台账总闸
        cleaned = scrub_forced_visual_verify_constraints(list(reflection.constraints or []))
        if cleaned == list(reflection.constraints or []):
            return reflection
        return Reflection(
            cause=reflection.cause,
            decision=reflection.decision,
            constraints=cleaned[:8],
            thought=reflection.thought,
            fingerprint=reflection.fingerprint,
            forced=reflection.forced,
            raw={
                **dict(reflection.raw or {}),
                "visual_verify_scrubbed": True,
            },
        )
    constraints = scrub_forced_visual_verify_constraints(list(reflection.constraints or []))
    if reflection.decision == "alternate":
        if CONSTRAINT_PATH_LEDGER not in constraints:
            constraints.append(CONSTRAINT_PATH_LEDGER)
    return Reflection(
        cause=reflection.cause,
        decision=reflection.decision,
        constraints=constraints[:8],
        thought=reflection.thought,
        fingerprint=reflection.fingerprint,
        forced=reflection.forced,
        raw={
            **dict(reflection.raw or {}),
            "path_ledger": reflection.decision == "alternate",
            "visual_verify_scrubbed": True,
        },
    )


def thought_claims_command_delivery(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return False
    return any(c in t for c in _DELIVERY_CLAIM)


def thought_has_copyable_command(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return False
    if _CMD_BODY.search(t):
        return True
    # 多行且含赋值/管道也视为命令体
    if "\n" in t and ("=" in t or "|" in t or "&&" in t):
        return True
    return False


def thought_has_path_breakdown(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return False
    hits = sum(1 for h in _BREAKDOWN_HINTS if h in t)
    return hits >= 2 or ("必要" in t and ("阻塞" in t or "已齐" in t or "缺" in t))


def failed_needs_path_ledger_retry(*, goal: str, thought: str) -> bool:
    """failed 收尾是否应回灌重写（至多一次，由闭环计数）。"""
    if not path_ledger_enabled():
        return False
    t = (thought or "").strip()
    if not t:
        return goal_needs_path_ledger_gate(goal)
    if thought_claims_command_delivery(t) and not thought_has_copyable_command(t):
        return True
    if goal_needs_path_ledger_gate(goal) and not thought_has_path_breakdown(t):
        return True
    return False


def format_path_ledger_fail_prompt(*, goal: str, thought: str) -> str:
    """failed 未过分项/交付闸时回灌。"""
    reasons: list[str] = []
    if thought_claims_command_delivery(thought) and not thought_has_copyable_command(
        thought
    ):
        reasons.append("声称交命令但 thought 无可复制命令体")
    if goal_needs_path_ledger_gate(goal) and not thought_has_path_breakdown(thought):
        reasons.append("多必要路径目标的 failed 未按路径分项（已齐/阻塞）")
    why = "；".join(reasons) or "收尾未满足必要路径台账"
    return (
        f"{PATH_LEDGER_FAIL_MARKER}\n"
        f"{why}。\n"
        "请重写本轮（可空 actions）：\n"
        f"- {CONSTRAINT_PATH_LEDGER}\n"
        "- status=failed 时 thought 须：① 分项列出各必要路径已齐或阻塞原因；"
        "② 若需用户本机继续，必须写出脱敏可复制命令（勿只写「给出命令」空话）；"
        "③ 拒因须与工具结果一致（not found / 闸拦截 ≠「沙箱禁读目标文件」）。\n"
        f"上一轮 thought：{(thought or '')[:240]}"
    )


def format_path_ledger_history_note() -> str:
    return (
        f"{PATH_LEDGER_MARKER}\n"
        f"{CONSTRAINT_PATH_LEDGER}"
    )
