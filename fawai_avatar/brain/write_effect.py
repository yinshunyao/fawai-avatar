# term:弱写入闸门 / 可见副作用闸门  doc:doc/功能/编码闭环/说明.md
"""写文件后的目标对齐副作用：死补丁（未引用 CSS 变量等）与收尾可见效果闸门。"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from typing import Any

from fawai_avatar.brain.prompt_profile import (
    goal_has_user_image,
    looks_like_fs_web_goal,
)

WEAK_WRITE_MARKER = "【弱写入】"
VISIBLE_EFFECT_MARKER = "【可见副作用】"

_CSS_VAR_DECL = re.compile(r"--([a-zA-Z0-9_-]+)\s*:")
# 整条自定义属性（含值），用于判断「新增是否只剩死变量」
_CSS_VAR_FULL = re.compile(r"--[a-zA-Z0-9_-]+\s*:[^;{}]*;?")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)//.*?$")
_PLACEHOLDER_THOUGHTS = frozenset(
    {
        "native tool_calls",
        "done",
        "success",
        "ok",
        "完成",
        "已完成",
        "任务完成",
        "success.",
    }
)
_NO_EFFECT_HINTS = (
    "未改动",
    "没有改",
    "无变化",
    "没变",
    "未变化",
    "看不到变化",
    "无可见",
    "没有可见",
    "仅声明",
    "只加了变量",
)
_UI_EFFECT_HINTS = (
    "布局",
    "间距",
    "样式",
    "窄屏",
    "气泡",
    "按钮",
    "输入栏",
    "可见",
    "已调整",
    "已优化",
    "已改",
    "padding",
    "margin",
    "gap",
    "flex",
    "grid",
    "scroll",
    "header",
    "footer",
    "safe-area",
)


@dataclass(frozen=True)
class WriteEffectAssessment:
    weak: bool
    reasons: tuple[str, ...] = ()


def weak_write_gate_enabled() -> bool:
    raw = (os.environ.get("FAWAI_WEAK_WRITE_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def visible_effect_gate_enabled() -> bool:
    raw = (os.environ.get("FAWAI_VISIBLE_EFFECT_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def goal_needs_visible_effect_gate(goal: str) -> bool:
    """前端/页面/布局类目标：success 须说明用户可见副作用。"""
    if not visible_effect_gate_enabled():
        return False
    text = goal or ""
    if looks_like_fs_web_goal(text):
        return True
    ui_bits = ("布局", "样式", "UI", "界面", "窄屏", "手机页", "会话页")
    return any(b.lower() in text.lower() for b in ui_bits)


def goal_needs_weak_write_gate(goal: str, *, path: str = "") -> bool:
    if not weak_write_gate_enabled():
        return False
    if goal_needs_visible_effect_gate(goal):
        return True
    rel = (path or "").replace("\\", "/").lower()
    return rel.endswith((".html", ".css", ".scss", ".vue", ".jsx", ".tsx"))


def _strip_css_noise(text: str) -> str:
    cleaned = _CSS_COMMENT.sub("", text or "")
    cleaned = _LINE_COMMENT.sub("", cleaned)
    return cleaned


def _added_text(old_text: str, new_text: str) -> str:
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    added: list[str] = []
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("+ "):
            added.append(line[2:])
    return "".join(added)


def _is_trivial_remainder(text: str) -> bool:
    """去掉整条 CSS 自定义属性后，是否只剩空白/标点/大括号。"""
    leftover = _CSS_VAR_FULL.sub("", text or "")
    leftover = re.sub(r"[:;{},\s]", "", leftover)
    return not leftover.strip()


def assess_patch_effect(
    path: str,
    old_text: str,
    new_text: str,
    *,
    goal: str = "",
) -> WriteEffectAssessment:
    """判断补丁是否仅有「死 token」级副作用（未引用 CSS 变量 / 纯空白注释）。"""
    if not goal_needs_weak_write_gate(goal, path=path):
        return WriteEffectAssessment(weak=False)
    if (old_text or "") == (new_text or ""):
        return WriteEffectAssessment(weak=True, reasons=("补丁无净变化",))

    added = _added_text(old_text, new_text)
    added_clean = _strip_css_noise(added).strip()
    if not added_clean and (new_text or "").strip() == (old_text or "").strip():
        return WriteEffectAssessment(weak=True, reasons=("仅空白/注释变化",))
    if not added_clean:
        # 可能是删多于增；删规则一般仍算有效。仅当实质文本几乎相同：
        old_c = re.sub(r"\s+", "", _strip_css_noise(old_text))
        new_c = re.sub(r"\s+", "", _strip_css_noise(new_text))
        if old_c == new_c:
            return WriteEffectAssessment(weak=True, reasons=("仅空白/注释变化",))
        return WriteEffectAssessment(weak=False)

    decl_names = _CSS_VAR_DECL.findall(added_clean)
    if not decl_names:
        return WriteEffectAssessment(weak=False)

    unused: list[str] = []
    for name in decl_names:
        needle = f"var(--{name}"
        if needle not in (new_text or ""):
            unused.append(f"--{name}")

    if unused and _is_trivial_remainder(added_clean):
        shown = ", ".join(unused[:6])
        more = "…" if len(unused) > 6 else ""
        return WriteEffectAssessment(
            weak=True,
            reasons=(f"仅新增未引用 CSS 变量（{shown}{more}）",),
        )
    if unused and len(unused) == len(decl_names) and len(added_clean) < 800:
        # 新增里声明的变量全部未引用，且去掉这些声明后无其它实质内容
        if _is_trivial_remainder(added_clean):
            shown = ", ".join(unused[:6])
            return WriteEffectAssessment(
                weak=True,
                reasons=(f"仅新增未引用 CSS 变量（{shown}）",),
            )
    return WriteEffectAssessment(weak=False)


def format_weak_write_message(
    *,
    path: str,
    reasons: list[str] | tuple[str, ...] | None = None,
) -> str:
    why = "；".join(r for r in (reasons or []) if r) or "补丁无目标对齐的可见副作用"
    return (
        f"{WEAK_WRITE_MARKER}路径 {path} 的写入被判定为弱写入：{why}。"
        "write_set 非空不等于验收通过。请继续 fs_patch/fs_replace，"
        "改到实际生效的选择器/属性（或同补丁内声明并 var() 引用），"
        "禁止只堆未使用的 CSS 变量/注释/空白后 status=success。"
        "若目标无法在当前能力内做出可见改动，须 status=failed 说明边界。"
    )


def format_visible_effect_prompt(*, goal: str, write_set: list[str]) -> str:
    paths = ", ".join(write_set[:6]) if write_set else "(无)"
    img_hint = ""
    if goal_has_user_image(goal):
        img_hint = (
            "目标含用户附图：thought 须点名对照图中至少一处可见问题，"
            "并说明本次改动如何缓解该问题。"
        )
    return (
        f"{VISIBLE_EFFECT_MARKER}已有写入（{paths}）。"
        "请给出面向用户的最终回答：须写清用户应看到的可见变化"
        "（布局/间距/组件位置等），禁止空话或 native tool_calls。"
        f"{img_hint}"
        "若实际无可见副作用，禁止 success，改 status=failed 说明原因。"
        "本步必须 actions 为空；若可 success，则 status=success、done=true，"
        "thought 即为给用户的最终回复。"
    )


def thought_admits_no_visible_effect(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return False
    return any(h in t for h in _NO_EFFECT_HINTS)


def thought_is_placeholder(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return True
    return t.lower() in {x.lower() for x in _PLACEHOLDER_THOUGHTS}


def thought_ok_for_visible_effect(thought: str, *, goal: str) -> bool:
    """收尾 thought 是否足以支撑 UI/页面类 success。"""
    t = (thought or "").strip()
    if thought_is_placeholder(t):
        return False
    if thought_admits_no_visible_effect(t):
        return False
    if not goal_needs_visible_effect_gate(goal):
        return len(t) >= 4
    if any(h.lower() in t.lower() for h in _UI_EFFECT_HINTS):
        return True
    # 无显式效果词时要求稍长自然语言，避免一句「好了」
    return len(t) >= 28


def results_have_weak_write(results: list[Any] | None) -> tuple[bool, str, list[str]]:
    """从工具结果提取弱写入标记。返回 (hit, path, reasons)。"""
    for r in results or []:
        data = getattr(r, "data", None)
        if not isinstance(data, dict):
            continue
        if not data.get("weak_write"):
            continue
        path = str(data.get("path") or "").strip() or "(unknown)"
        reasons = data.get("weak_write_reasons") or []
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        return True, path, [str(x) for x in reasons if str(x).strip()]
    return False, "", []
