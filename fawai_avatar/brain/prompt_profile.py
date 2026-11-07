# term:规划提示档  tool:prompt_profile  doc:doc/功能/规划提示档/说明.md
"""按本轮目的裁剪规划提示：是否附图、能力清单子集、长短规则。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fawai_avatar.brain.capabilities import (
    looks_like_coding_goal,
    looks_like_file_goal,
)

ProfileId = str  # vision | fs | gui | browser | full

_GUI_KINDS = frozenset(
    {
        "click",
        "type",
        "hotkey",
        "app_focus",
        "ui_click",
        "ui_type",
        "ui_verify",
        "double_click",
        "right_click",
        "scroll",
        "press",
    }
)

_BROWSER_KINDS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_close",
    }
)

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
        "py_write",
        "py_pip",
        "py_run",
        "shovel_search",
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
        "app_focus",
        "ui_click",
        "ui_type",
        "ui_verify",
        "double_click",
        "right_click",
        "scroll",
        "press",
        "shell",
        "wait",
        "done",
        "fs_glob",
        "fs_grep",
        "fs_read",
        "explore_search",
    }
    | _BROWSER_KINDS
)

_BROWSER_PROFILE_KINDS = frozenset(
    {
        "shell",
        "wait",
        "done",
        "fs_glob",
        "fs_grep",
        "fs_read",
        "explore_search",
    }
    | _BROWSER_KINDS
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
    "桌面",
    "屏幕上",
    "GUI",
    "gui",
    "操作界面",
    "操控界面",
    "操作 cursor",
    "操作Cursor",
    "Cursor界面",
    "cursor界面",
    "点一下",
    "键入",
    "双击",
    "右键",
    "滚动",
    "app_focus",
    "ui_click",
    "ui_type",
    "obs_ax",
    "文本编辑",
    "TextEdit",
    "记事本",
)

_BROWSER_GOAL_HINTS = (
    "打开网页",
    "打开网站",
    "访问网址",
    "访问 url",
    "访问URL",
    "http://",
    "https://",
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "无头浏览器",
    "playwright",
    "网页里",
    "网页上",
    "在网页",
    "浏览网页",
    "打开百度",
    "打开 google",
    "打开Google",
)

# 明确「改前端/网页代码」——不含单独的「界面」（避免误伤桌面操控）
_FS_WEB_HINTS = (
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
    "onpaste",
    "datatransfer",
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
        kind_allow=frozenset(_VISION_KINDS | _BROWSER_KINDS),
        attach_user_images=True,
        include_coding_context=False,
        compact_capabilities=True,
        short_rules=True,
    )


def profile_fs(*, coding: bool) -> PromptProfile:
    kinds = frozenset(_VISION_KINDS | _FS_EXTRA_KINDS | _BROWSER_KINDS)
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


def profile_browser() -> PromptProfile:
    return PromptProfile(
        id="browser",
        kind_allow=_BROWSER_PROFILE_KINDS,
        attach_user_images=False,
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


_OTHER_REPORT_FORM_HINTS = (
    "一句话",
    "简短",
    "简单说",
    "不要图",
    "不要图表",
    "不要流程图",
    "不要 mermaid",
    "纯文本",
    "不要表格",
    "只要结论",
    "直接说",
    "别写报告",
    "不要报告",
    "不要分点",
    "口语",
)


def user_specifies_report_form(goal: str) -> bool:
    """用户已规定交稿篇幅或形式（含不要图表）。"""
    g = goal or ""
    return any(h.lower() in g.lower() for h in _OTHER_REPORT_FORM_HINTS)


def report_delivery_rule(goal: str) -> str:
    """终稿体例。用户另有指令时不再加图表。"""
    if user_specifies_report_form(goal):
        return (
            "- 用户已规定交稿体例：success/failed 的终稿 thought 只按用户要求的篇幅和形式，"
            "不要另加图表或流程图。\n"
        )
    return (
        "- 面向用户的终稿（success 或 failed，且本步是交稿）：thought 写成可读报告，"
        "先一句结论，再分项；有对照用 Markdown 表；有步骤、分支、依赖或结构关系时，"
        "在 thought 内加一段 mermaid（```mermaid 围栏，优先 flowchart TD）。"
        "没有这种关系就不要硬画，也不要编造数字。换行写在 JSON 字符串里，双引号要转义，"
        "JSON 前后不要再包 markdown。\n"
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


def looks_like_browser_goal(goal: str) -> bool:
    text = (goal or "").lower()
    if not text:
        return False
    if any(h.lower() in text for h in _BROWSER_GOAL_HINTS):
        return True
    # 「打开 xxx.com / 访问站点」弱启发
    if ("打开" in text or "访问" in text) and (
        ".com" in text or ".cn" in text or ".org" in text or "网站" in text or "网址" in text
    ):
        return True
    return False


def looks_like_gui_goal(goal: str) -> bool:
    text = goal or ""
    if not text:
        return False
    # 纯「改前端/网页代码」不算 GUI 操控；但含明确键鼠词仍算 GUI
    gui_hit = any(h.lower() in text.lower() for h in _GUI_GOAL_HINTS)
    if not gui_hit:
        return False
    if looks_like_fs_web_goal(text) and not any(
        h in text for h in ("点击", "鼠标", "热键", "快捷键", "ui_click", "操作界面", "操控")
    ):
        # 前端改码目标里偶然出现「窗口」等弱词时，仍判非 GUI
        weak_only = gui_hit and not any(
            h in text
            for h in (
                "点击",
                "鼠标",
                "热键",
                "快捷键",
                "操作界面",
                "操控界面",
                "Cursor界面",
                "操作Cursor",
                "操作 cursor",
                "ui_click",
                "ui_type",
                "obs_ax",
                "键入",
                "双击",
                "右键",
            )
        )
        if weak_only:
            return False
    return True


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
    browser_like = looks_like_browser_goal(goal)
    gui_like = looks_like_gui_goal(goal)
    has_img = goal_has_user_image(goal)
    tools_done = history_has_tool_rounds(history)

    # 网页内操作优先于桌面点浏览器；不进 fs（避免剔除 browser_*）
    if browser_like and not gui_like:
        return profile_browser()

    # 桌面操控优先：不得进 fs（否则能力清单剔除键鼠，出现「未提供 GUI」假失败）
    if gui_like:
        return profile_gui()

    # 已进入工具闭环的文件/前端任务：卸图走 fs
    if tools_done and file_like:
        if observation_has_screen:
            return profile_gui()
        return profile_fs(coding=coding)

    # 首轮附图：精简 VLM 看图定位
    if has_img and not tools_done:
        return profile_vision()

    if file_like:
        return profile_fs(coding=coding)

    if browser_like:
        return profile_browser()

    return profile_full(coding=coding)


def kinds_for_format_example(profile: PromptProfile) -> frozenset[str]:
    """格式行里枚举的 kind；子集档只列允许集。"""
    from fawai_avatar.brain.capabilities import known_action_kinds

    known = known_action_kinds()
    if profile.kind_allow is None:
        return known
    return frozenset(k for k in known if k in profile.kind_allow) or known
