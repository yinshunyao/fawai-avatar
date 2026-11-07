# term:步骤分类与执行流  doc:doc/功能/步骤分类与执行流/说明.md
"""Worker 步型：取证 / 串行执行 / 并行 / 分析 / 收尾。"""
from __future__ import annotations

import os
from typing import Any

from fawai_avatar.brain.action_segment import MECHANICAL_READ_KINDS
from fawai_avatar.types import Action

STEP_KINDS = frozenset(
    {
        "sense",
        "act_serial",
        "act_parallel",
        "analyze",
        "finalize",
    }
)

# 这些步型之后，下一步默认跳过浅环境观察（obs_fs 顶层列表）
SKIP_SHALLOW_ENV_AFTER = frozenset(
    {
        "act_serial",
        "act_parallel",
        "analyze",
        "finalize",
    }
)

# 显式取证方式：出现则不因「跳过浅观察」而抹掉
MEANINGFUL_ENV_METHODS = frozenset({"obs_screen", "obs_ax", "obs_shell"})

STEP_KIND_LABELS_ZH: dict[str, str] = {
    "sense": "取证",
    "act_serial": "串行执行",
    "act_parallel": "并行",
    "analyze": "分析",
    "finalize": "收尾",
}


def step_kind_label(kind: str) -> str:
    k = (kind or "").strip().lower()
    return STEP_KIND_LABELS_ZH.get(k, k or "步骤")


def normalize_step_kind(raw: Any) -> str:
    """规范化 step_kind；无法识别则返回空串（交启发式）。"""
    if raw is None:
        return ""
    m = str(raw or "").strip().lower()
    aliases = {
        "observe": "sense",
        "observation": "sense",
        "env": "sense",
        "serial": "act_serial",
        "act": "act_serial",
        "execute": "act_serial",
        "exec": "act_serial",
        "parallel": "act_parallel",
        "concurrent": "act_parallel",
        "reflect": "analyze",
        "analysis": "analyze",
        "obs_reflect": "analyze",
        "done": "finalize",
        "finish": "finalize",
        "final": "finalize",
    }
    m = aliases.get(m, m)
    if m in STEP_KINDS:
        return m
    return ""


def infer_step_kind(
    *,
    status: str,
    actions: list[Action],
    observe_kinds: list[str] | None = None,
    awaiting_user_answer: bool = False,
) -> str:
    """缺省回填 step_kind。"""
    st = (status or "").strip().lower()
    if awaiting_user_answer or st in {"success", "failed"} and not actions:
        return "finalize"
    if st == "need_input" and not actions:
        return "analyze"
    if not actions:
        # 仅点选环境观察 / 空转规划
        kinds = [str(x).strip().lower() for x in (observe_kinds or []) if str(x).strip()]
        if kinds and any(k in MEANINGFUL_ENV_METHODS or k == "obs_fs" for k in kinds):
            return "sense"
        return "analyze"
    if _batch_allows_parallel(actions):
        return "act_parallel"
    return "act_serial"


def resolve_step_kind(
    raw: Any,
    *,
    status: str,
    actions: list[Action],
    observe_kinds: list[str] | None = None,
    awaiting_user_answer: bool = False,
) -> str:
    explicit = normalize_step_kind(raw)
    if explicit:
        # 显式 parallel 但批不合格 → 降为 serial（执行侧也会再降）
        if explicit == "act_parallel" and not _batch_allows_parallel(actions):
            return "act_serial" if actions else "analyze"
        return explicit
    return infer_step_kind(
        status=status,
        actions=actions,
        observe_kinds=observe_kinds,
        awaiting_user_answer=awaiting_user_answer,
    )


def default_observe_kinds_for_step_kind(step_kind: str) -> list[str] | None:
    """规划未给 observe_kinds 时：act/analyze/finalize → none；其余不干预。"""
    kind = (step_kind or "").strip().lower()
    if kind in SKIP_SHALLOW_ENV_AFTER:
        return ["none"]
    return None


def should_skip_shallow_env_observe(
    requested: list[str] | None,
    *,
    prev_step_kind: str | None,
) -> bool:
    """上一段为 act/analyze/finalize 且未显式点选非浅取证 → 跳过浅 obs_fs。"""
    prev = (prev_step_kind or "").strip().lower()
    if prev not in SKIP_SHALLOW_ENV_AFTER:
        return False
    req = [str(x).strip().lower() for x in (requested or []) if str(x).strip()]
    if any(m in MEANINGFUL_ENV_METHODS for m in req):
        return False
    # 空 / 仅 none / 仅 obs_fs（± none）→ 跳过
    meaningful = [m for m in req if m not in {"none", "obs_fs"}]
    return not meaningful


def coerce_observe_kinds_skip_shallow(
    requested: list[str] | None,
    *,
    prev_step_kind: str | None,
) -> list[str] | None:
    """若应跳过浅观察，返回 ['none']；否则原样（None 表示未改）。"""
    if should_skip_shallow_env_observe(requested, prev_step_kind=prev_step_kind):
        return ["none"]
    return requested


# 与同批其它动作抢同一份未知副作用：不能并进并行
_EXCLUSIVE_KINDS = frozenset(
    {
        "shell",
        "shell_verify",
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
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_close",
        "git_commit",
        "diff_confirm",
        "diff_reject",
        "py_pip",
        "py_run",
    }
)

# 不带 path 时会扫整棵树或整库，与任何写冲突
_WIDE_READ_KINDS = frozenset(
    {
        "fs_grep",
        "fs_glob",
        "explore_search",
        "code_map",
        "code_index_refresh",
        "git_status",
        "git_diff",
    }
)

_MUTATING_KINDS = frozenset(
    {
        "fs_write",
        "fs_replace",
        "fs_patch",
        "py_write",
        "lore_set",
        "lore_note_upsert",
        "skill_write",
    }
)


def _norm_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def _action_paths(action: Action) -> list[str]:
    args = action.args or {}
    found: list[str] = []
    for key in ("path", "paths"):
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            found.append(_norm_path(raw))
        elif isinstance(raw, list):
            found.extend(_norm_path(str(x)) for x in raw if str(x).strip())
    found.extend(_norm_path(x) for x in (action.write_resources or []) if str(x).strip())
    # 去重保序
    out: list[str] = []
    for item in found:
        if item and item not in out:
            out.append(item)
    return out


def _path_hits(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _is_exclusive(action: Action) -> bool:
    if action.needs_foreground or action.expect:
        return True
    kind = (action.kind or "").strip().lower()
    if kind in _EXCLUSIVE_KINDS:
        return True
    if kind in _MUTATING_KINDS and not _action_paths(action):
        return True
    return False


def _batch_allows_parallel(actions: list[Action]) -> bool:
    """至少两个动作，且两两不抢同一路径、不含前台/shell/带 expect 的动作。"""
    if len(actions) < 2:
        return False
    if any(_is_exclusive(a) for a in actions):
        return False
    write_paths: list[str] = []
    read_paths: list[str] = []
    wide_read = False
    for action in actions:
        kind = (action.kind or "").strip().lower()
        paths = _action_paths(action)
        if kind in _MUTATING_KINDS or list(action.write_resources or []):
            write_paths.extend(paths)
        else:
            if kind in _WIDE_READ_KINDS and not paths:
                wide_read = True
            read_paths.extend(paths)
    if wide_read and write_paths:
        return False
    for i, left in enumerate(write_paths):
        for right in write_paths[i + 1 :]:
            if _path_hits(left, right):
                return False
        for right in read_paths:
            if _path_hits(left, right):
                return False
    return True


def batch_allows_parallel(actions: list[Action]) -> bool:
    """对外：整批是否可走 act_parallel。"""
    return _batch_allows_parallel(actions)


def should_execute_parallel(step_kind: str, actions: list[Action]) -> bool:
    if (step_kind or "").strip().lower() != "act_parallel":
        return False
    return batch_allows_parallel(actions)


def parallel_worker_count() -> int:
    raw = (os.environ.get("FAWAI_ACT_PARALLEL_WORKERS") or "4").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 4
    return max(1, min(n, 16))


def is_mechanical_kind(kind: str) -> bool:
    return (kind or "").strip().lower() in MECHANICAL_READ_KINDS
