# term:动作段预期闸  doc:doc/功能/动作段预期闸/说明.md
"""多只读动作段：机读 expect + 机械成功跳过 LLM 观察反省。"""
from __future__ import annotations

import json
import os
from typing import Any

from fawai_avatar.types import Action, ExecResult

# 机械只读族：成功且整批均属此类 → 默认可跳过 LLM 观察反省
MECHANICAL_READ_KINDS = frozenset(
    {
        "fs_grep",
        "fs_glob",
        "fs_read",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
        "git_status",
        "git_diff",
        "version_list",
        "version_get",
        "skill_search",
        "skill_get",
        "lore_search",
        "lore_get",
        "lore_buffer_list",
        "diff_list",
        "platform_info",
        "shovel_search",
        "task_context_search",
        "task_context_get",
        "success_ledger_search",
        "success_ledger_get",
        "failure_ledger_search",
        "failure_ledger_get",
    }
)


def _env_flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip().lower()


def mechanical_obs_reflect_mode() -> str:
    """skip（默认）| llm。总闸 FAWAI_OBS_REFLECT=0 时由调用方先短路。"""
    raw = _env_flag("FAWAI_OBS_REFLECT_MECHANICAL", "skip")
    if raw in {"llm", "on", "1", "true", "yes", "force"}:
        return "llm"
    return "skip"


def is_mechanical_read_action(action: Action) -> bool:
    kind = (action.kind or "").strip().lower()
    if kind not in MECHANICAL_READ_KINDS:
        return False
    if action.needs_foreground:
        return False
    if list(action.write_resources or []):
        return False
    return True


def batch_is_mechanical_success(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    if not actions or not results:
        return False
    if len(results) < len(actions):
        # 中途打断：不按「整批机械成功」跳过
        return False
    if not all(r.ok for r in results):
        return False
    return all(is_mechanical_read_action(a) for a in actions)


def should_skip_obs_reflect_llm(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """机械只读整批成功且模式为 skip → 不调 LLM 观察反省。"""
    if mechanical_obs_reflect_mode() == "llm":
        return False
    return batch_is_mechanical_success(actions, results)


def _result_haystack(result: ExecResult) -> str:
    parts: list[str] = [str(result.message or "")]
    data = result.data if isinstance(result.data, dict) else {}
    if data:
        try:
            parts.append(json.dumps(data, ensure_ascii=False, default=str))
        except TypeError:
            parts.append(str(data))
    return "\n".join(parts)


# 模型常对 mutate 臆造「成功口令」；真实回执是 `{kind} {path} · applied`
_INVENTED_SUCCESS_NEEDLES = frozenset(
    {
        "patch applied",
        "fs_patch applied",
        "replace applied",
        "fs_replace applied",
        "write applied",
        "fs_write applied",
        "successfully applied",
        "applied successfully",
    }
)

# GUI 工具回执通常只有 clicked/typed；模型却用 expect 断言画面态 → 假 FAIL
_GUI_KINDS_FOR_EXPECT = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "type",
        "press",
        "hotkey",
        "ui_click",
        "ui_type",
        "app_focus",
    }
)
_INVENTED_UI_STATE_MARKERS = (
    "获得焦点",
    "已获得焦点",
    "焦点",
    "光标闪烁",
    "键盘弹出",
    "已打开",
    "搜索框获得",
    "输入框获得",
    "got focus",
    "has focus",
    "focused",
    "caret blink",
)

_WRITE_LANDED_HINT = (
    "【写入已落盘】文件已按补丁/替换改写（见 data.diff / hunks_applied），"
    "勿当未修改；下一轮须按当前原文取 old，勿复用写入前锚点"
)


def write_already_landed(result: ExecResult) -> bool:
    """工具已成功写入磁盘（含 auto diff 模式落地）。"""
    if not result.ok:
        return False
    data = result.data if isinstance(result.data, dict) else {}
    if data.get("written") is True:
        return True
    try:
        if int(data.get("hunks_applied") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    # fs_replace / fs_write 成功常见：有 path + diff，且非 pending
    if data.get("diff") and data.get("path") and data.get("diff_mode") == "auto":
        return True
    return False


def _needle_is_invented_success(needle: str) -> bool:
    text = str(needle or "").strip().lower()
    if not text:
        return False
    if text in _INVENTED_SUCCESS_NEEDLES:
        return True
    if text == "applied":
        return True
    if "applied" in text and any(
        token in text for token in ("patch", "replace", "write", "success", "fs_")
    ):
        return True
    return False


def _needle_is_invented_ui_state(needle: str) -> bool:
    """是否把画面/焦点态当成工具回执子串（GUI expect 常见臆造）。"""
    text = str(needle or "").strip().lower()
    if not text:
        return False
    return any(m.lower() in text for m in _INVENTED_UI_STATE_MARKERS)


def _expect_miss_is_invented_success_only(action: Action, reason: str) -> bool:
    """失败原因是否仅为臆造的成功口令未出现在回执里。"""
    expect = action.expect if isinstance(action.expect, dict) else {}
    reason_l = (reason or "").lower()
    if reason_l.startswith("contains ") and "missing" in reason_l:
        return _needle_is_invented_success(str(expect.get("contains") or ""))
    if reason_l.startswith("contains_any"):
        needles = expect.get("contains_any")
        if not isinstance(needles, (list, tuple)) or not needles:
            return False
        return all(_needle_is_invented_success(str(n)) for n in needles)
    return False


def _expect_miss_is_invented_ui_state_only(action: Action, reason: str) -> bool:
    """GUI 动作：expect 是否只断言回执里不会出现的画面态口令。"""
    kind = (action.kind or "").strip().lower()
    if kind not in _GUI_KINDS_FOR_EXPECT:
        return False
    expect = action.expect if isinstance(action.expect, dict) else {}
    reason_l = (reason or "").lower()
    if reason_l.startswith("contains ") and "missing" in reason_l:
        return _needle_is_invented_ui_state(str(expect.get("contains") or ""))
    if reason_l.startswith("contains_any"):
        needles = expect.get("contains_any")
        if not isinstance(needles, (list, tuple)) or not needles:
            return False
        return all(_needle_is_invented_ui_state(str(n)) for n in needles)
    return False


def _diff_preview(data: dict[str, Any], *, limit: int = 480) -> str:
    diff = str(data.get("diff") or "").strip()
    if not diff:
        return ""
    if len(diff) <= limit:
        return diff
    return diff[: limit - 1] + "…"


def check_action_expect(
    action: Action,
    result: ExecResult,
) -> tuple[bool, str]:
    """校验 Action.expect；无 expect 则通过。返回 (ok, reason)。

    工具本身已失败时仍核对文本预期：已命中则通过（保留原失败 message，
    不盖成 ``tool not ok``）；未命中则写明缺哪个标记。
    """
    expect = action.expect
    if not isinstance(expect, dict) or not expect:
        return True, ""
    hay = _result_haystack(result)
    hay_l = hay.lower()

    if "contains" in expect and expect.get("contains") is not None:
        needle = str(expect.get("contains") or "")
        if needle and needle not in hay and needle.lower() not in hay_l:
            return False, f"contains {needle!r} missing"

    if "not_contains" in expect and expect.get("not_contains") is not None:
        needle = str(expect.get("not_contains") or "")
        if needle and (needle in hay or needle.lower() in hay_l):
            return False, f"not_contains hit {needle!r}"

    any_list = expect.get("contains_any")
    if isinstance(any_list, (list, tuple)) and any_list:
        needles = [str(x) for x in any_list if str(x)]
        if needles and not any(n in hay or n.lower() in hay_l for n in needles):
            return False, f"contains_any none of {needles!r}"

    return True, ""


_EXPECT_PRE_MSG_CHARS = 1200


def _clip_expect_pre_message(text: str, *, limit: int = _EXPECT_PRE_MSG_CHARS) -> str:
    raw = (text or "").strip()
    if not raw or len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def apply_expect_to_result(action: Action, result: ExecResult) -> ExecResult:
    """工具已返回后套 expect；失败则改写为 ok=false。

    特例：mutate 已落盘，但 expect 只断言臆造成功口令（如 ``patch applied``）→
    **保持 ok**，避免「表面失败、磁盘已改」级联。真实内容 expect 仍可失败，
    但文案须标明【写入已落盘】。

    工具本身已失败且 expect 文本已满足 → **不改写**，保留原 message/data，
    供失败反省吃齐证据（成功预期路径仍轻量：本函数早退）。
    """
    ok, reason = check_action_expect(action, result)
    if ok:
        return result

    data = dict(result.data or {}) if isinstance(result.data, dict) else {}
    data["expect"] = dict(action.expect or {})
    data["expect_pre_ok"] = bool(result.ok)
    data["expect_pre_message"] = result.message
    landed = bool(result.ok) and write_already_landed(result)

    if landed and _expect_miss_is_invented_success_only(action, reason):
        data["expect_ignored"] = True
        data["expect_ignore_reason"] = reason
        note = "（已写入；忽略臆造成功 expect，勿再断言 patch applied）"
        msg = str(result.message or "").rstrip()
        if note.strip("（）") not in msg:
            msg = f"{msg}{note}" if msg else note.strip("（）")
        return ExecResult(ok=True, message=msg, data=data)

    # 工具已 ok：忽略 GUI 臆造画面态 expect（焦点等须下一步观察回证）
    if result.ok and _expect_miss_is_invented_ui_state_only(action, reason):
        data["expect_ignored"] = True
        data["expect_ignore_reason"] = reason
        note = "（忽略臆造画面态 expect；请下一步 obs_screen/obs_ax 回证）"
        msg = str(result.message or "").rstrip()
        if "忽略臆造画面态 expect" not in msg:
            msg = f"{msg}{note}" if msg else note.strip("（）")
        return ExecResult(ok=True, message=msg, data=data)

    message = f"expect failed: {reason}"
    # 工具已失败时仍缺预期标记：标签之外带上工具原文，避免反省只见笼统句
    if not result.ok:
        pre = _clip_expect_pre_message(str(result.message or ""))
        if pre and pre not in message:
            message = f"{message}\n工具原文：\n{pre}"
    if landed:
        data["write_landed_despite_expect"] = True
        message = f"{message}；{_WRITE_LANDED_HINT}"
        preview = _diff_preview(data)
        if preview:
            message = f"{message}\n已落盘 diff 小窗（须按此现状续改）：\n{preview}"

    return ExecResult(
        ok=False,
        message=message,
        data=data,
    )


def format_mechanical_obs_summary(
    actions: list[Action],
    results: list[ExecResult],
) -> str:
    """规则观察摘要（替代 LLM 观察反省入 history）。"""
    lines = [
        "【观察反省·规则】",
        "progress: advanced",
        "thought: 机械只读批全部成功，跳过 LLM 观察反省。",
        "notes:",
    ]
    for i, action in enumerate(actions):
        r = results[i] if i < len(results) else None
        kind = (action.kind or "").strip() or "?"
        msg = (r.message if r else "") or ""
        lines.append(f"- {kind}: ok — {msg[:120]}")
    lines.append("hints: []")
    return "\n".join(lines)
