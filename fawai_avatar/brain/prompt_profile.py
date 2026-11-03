# term:规划提示档  tool:prompt_profile  doc:doc/功能/规划提示档/说明.md
"""按本轮目的裁剪规划提示：是否附图、能力清单子集、长短规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fawai_avatar.brain.capabilities import (
    looks_like_coding_goal,
    looks_like_file_goal,
)

ProfileId = str  # vision | fs | gui | full

_GUI_KINDS = frozenset({"click", "type", "hotkey"})

_VISION_KINDS = frozenset(
    {
        "fs_glob",
        "fs_grep",
        "fs_read",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
        "shell",
        "wait",
        "done",
    }
)

_FS_EXTRA_KINDS = frozenset(
    {
        "fs_write",
        "fs_replace",
        "fs_patch",
        "code_check",
        "shell_verify",
        "git_status",
        "git_diff",
        "git_commit",
        "diff_list",
        "diff_confirm",
        "diff_reject",
        "version_snapshot",
        "version_list",
        "version_get",
        "version_delete",
        "version_rollback",
        "skill_search",
        "skill_write",
        "skill_get",
        "lore_search",
        "lore_get",
        "lore_set",
        "lore_note_upsert",
        "lore_buffer_list",
        "lore_vector_reindex",
    }
)

_GUI_PROFILE_KINDS = frozenset(
    {
        "click",
        "type",
        "hotkey",
        "shell",
        "wait",
        "done",
        "fs_glob",
        "fs_grep",
        "fs_read",
        "explore_search",
    }
)

_GUI_GOAL_HINTS = (
    "点击",
    "鼠标",
    "键盘",
    "热键",
    "快捷键",
    "前台",
    "窗口",
    "打开 app",
    "打开应用",
    "微信",
    "浏览器里点",
    "桌面",
    "屏幕上",
    "GUI",
)

_FS_WEB_HINTS = (
    "页面",
    "前端",
    "html",
    "markdown",
    "渲染",
    "会话页",
    "index.html",
    "static/",
    ".css",
    ".js",
    "pwa",
    "web ",
    "web端",
    "网页",
    "粘贴",
    "消息气泡",
    "innerHTML",
    "布局",
    "样式",
    "窄屏",
    "手机",
    "ui ",
    "界面",
)

_TOOL_HISTORY_MARKERS = (
    "执行结果",
    "工具结果",
    "fs_glob",
    "fs_grep",
    "fs_read",
    "fs_patch",
    "fs_write",
    "explore_search",
    "glob ",
    "code_check",
    "shell →",
    "shell:",
)


@dataclass(frozen=True)
class PromptProfile:
    """单轮规划提示配置。"""

    id: ProfileId
    kind_allow: frozenset[str] | None  # None = 全量
    attach_user_images: bool
    include_coding_context: bool
    compact_capabilities: bool
    short_rules: bool


def profile_vision() -> PromptProfile:
    return PromptProfile(
        id="vision",
        kind_allow=_VISION_KINDS,
        attach_user_images=True,
        include_coding_context=False,
        compact_capabilities=True,
        short_rules=True,
    )


def profile_fs(*, coding: bool) -> PromptProfile:
    kinds = frozenset(_VISION_KINDS | _FS_EXTRA_KINDS)
    return PromptProfile(
        id="fs",
        kind_allow=kinds,
        attach_user_images=False,
        include_coding_context=coding,
        compact_capabilities=True,
        short_rules=True,
    )


def profile_gui() -> PromptProfile:
    return PromptProfile(
        id="gui",
        kind_allow=_GUI_PROFILE_KINDS,
        attach_user_images=True,
        include_coding_context=False,
        compact_capabilities=True,
        short_rules=True,
    )


def profile_full(*, coding: bool) -> PromptProfile:
    return PromptProfile(
        id="full",
        kind_allow=None,
        attach_user_images=True,
        include_coding_context=coding,
        compact_capabilities=False,
        short_rules=False,
    )


def goal_has_user_image(goal: str) -> bool:
    from fawai_avatar.channel.message import extract_image_marks

    _, refs = extract_image_marks(goal or "")
    return bool(refs)


def looks_like_fs_web_goal(goal: str) -> bool:
    text = (goal or "").lower()
    if not text:
        return False
    return any(h.lower() in text for h in _FS_WEB_HINTS)


def looks_like_gui_goal(goal: str) -> bool:
    text = goal or ""
    if not text:
        return False
    # 纯「页面/前端改码」不算 GUI 操控
    if looks_like_fs_web_goal(text) and not any(h in text for h in ("点击", "鼠标", "热键", "快捷键")):
        return False
    return any(h.lower() in text.lower() for h in _GUI_GOAL_HINTS)


def history_has_tool_rounds(history: list[dict[str, Any]] | None) -> bool:
    if not history:
        return False
    for msg in history:
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if any(m in content for m in _TOOL_HISTORY_MARKERS):
            return True
    return False


def infer_prompt_profile(
    goal: str,
    *,
    history: list[dict[str, Any]] | None = None,
    observation_has_screen: bool = False,
) -> PromptProfile:
    """规则推断本轮提示档（不加额外 LLM）。"""
    coding = looks_like_coding_goal(goal)
    file_like = (
        looks_like_file_goal(goal)
        or looks_like_fs_web_goal(goal)
        or coding
    )
    gui_like = looks_like_gui_goal(goal)
    has_img = goal_has_user_image(goal)
    tools_done = history_has_tool_rounds(history)

    # 已进入工具闭环的文件/前端任务：卸图走 fs（编码上下文仅真正 coding 目标）
    if tools_done and (file_like or not gui_like):
        if gui_like and observation_has_screen:
            return profile_gui()
        return profile_fs(coding=coding)

    # 首轮附图：精简 VLM 看图定位
    if has_img and not tools_done:
        if gui_like and not file_like:
            return profile_gui()
        return profile_vision()

    if gui_like and not file_like:
        return profile_gui()

    if file_like:
        return profile_fs(coding=coding)

    return profile_full(coding=coding)


def kinds_for_format_example(profile: PromptProfile) -> frozenset[str]:
    """格式行里枚举的 kind；子集档只列允许集。"""
    from fawai_avatar.brain.capabilities import known_action_kinds

    known = known_action_kinds()
    if profile.kind_allow is None:
        return known
    return frozenset(k for k in known if k in profile.kind_allow) or known
