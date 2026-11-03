# term:探索进度闸门  doc:doc/功能/编码闭环/说明.md
"""改文件类目标：连续只读探索过久则注入约束 / 强制改写 / 止损。"""

from __future__ import annotations

import os
from typing import Any

from fawai_avatar.brain.capabilities import looks_like_coding_goal, looks_like_file_goal
from fawai_avatar.brain.prompt_profile import looks_like_fs_web_goal
from fawai_avatar.types import Action, ExecResult

_EXPLORE_KINDS = frozenset(
    {
        "fs_glob",
        "fs_grep",
        "fs_read",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
        "git_status",
        "git_diff",
        "diff_list",
        "version_list",
    }
)

_MUTATE_KINDS = frozenset(
    {
        "fs_write",
        "fs_replace",
        "fs_patch",
        "git_commit",
        "version_snapshot",
        "version_rollback",
        "diff_apply",
        "diff_reject",
    }
)

# 原生工具面：闸门注入 / 改写强制时只暴露写类 + 收尾
_MUTATE_PREFER_TOOL_KINDS = frozenset(
    {
        "fs_write",
        "fs_replace",
        "fs_patch",
        "code_check",
        "shell_verify",
        "diff_list",
        "diff_apply",
        "diff_reject",
        "git_commit",
        "version_snapshot",
        "done",
    }
)

# 反省要求核对原文时：写类工具面 + 定向只读（禁止继续无目标的探仓）
_MUTATE_PREFER_WITH_VERIFY_READ_TOOL_KINDS = _MUTATE_PREFER_TOOL_KINDS | frozenset(
    {
        "fs_read",
        "fs_read_many",
        "fs_grep",
        "fs_glob",
    }
)

_VERIFY_READ_ONLY_KINDS = frozenset(
    {"fs_read", "fs_read_many", "fs_grep", "fs_glob"}
)

_READ_PATH_KINDS = frozenset({"fs_read", "fs_read_many"})

GATE_MARKER = "【探索进度闸门】"
MUTATE_FORCE_MARKER = "【探索改写强制】"
VERIFY_READ_MARKER = "【反省开读】"
LOCATE_BUDGET_MARKER = "【定位预算】"
WIDE_GREP_MARKER = "【宽检索降质】"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def explore_only_gate_threshold(*, goal: str = "") -> int:
    """连续只读探索多少步后开始注入硬约束；0=关闭。

    默认 8；前端/页面类（fs_web）默认 5（可用 FAWAI_EXPLORE_ONLY_GATE 统一覆盖）。
    """
    raw = (os.environ.get("FAWAI_EXPLORE_ONLY_GATE") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    if goal and looks_like_fs_web_goal(goal) and not looks_like_coding_goal(goal):
        return 5
    return 8


def explore_only_abort_threshold(*, goal: str = "") -> int:
    """连续只读达到此值则进入强制改写 / 最终 failed；默认 2×gate；gate=0 时关闭。"""
    gate = explore_only_gate_threshold(goal=goal)
    if gate <= 0:
        return 0
    raw = (os.environ.get("FAWAI_EXPLORE_ONLY_ABORT") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return gate * 2


def repeated_read_boost_threshold() -> int:
    """同一 path 成功 fs_read 达到此次数时，探索 streak 额外 +1；0=关。默认 3。"""
    return _env_int("FAWAI_EXPLORE_REPEAT_READ_BOOST", 3)


def overlap_read_boost_enabled() -> bool:
    """同 path 重叠行窗反复 fs_read 是否加速探索 streak；默认开。"""
    raw = (os.environ.get("FAWAI_EXPLORE_OVERLAP_READ_BOOST") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


# 未指定 limit/end 时视为读到文件末尾的开区间上界（仅用于重叠判定）
_OPEN_END = 10**9
_MAX_WINDOWS_PER_PATH = 16


def read_window_from_args(args: dict[str, Any] | None) -> tuple[int, int]:
    """fs_read 行窗 → (start, end) 闭区间；缺省视为整文件。"""
    raw = args or {}
    start = 0
    for key in ("offset", "start_line"):
        if raw.get(key) is not None:
            try:
                start = max(0, int(raw.get(key)))
            except (TypeError, ValueError):
                start = 0
            break
    end: int | None = None
    if raw.get("end_line") is not None:
        try:
            end = max(0, int(raw.get("end_line")))
        except (TypeError, ValueError):
            end = None
    limit = raw.get("limit")
    if end is None and limit is not None:
        try:
            lim = max(0, int(limit))
        except (TypeError, ValueError):
            lim = 0
        if start > 0 and lim > 0:
            end = start + lim - 1
        elif lim > 0:
            end = lim
    if start <= 0 and end is None:
        return (1, _OPEN_END)
    if start <= 0:
        start = 1
    if end is None:
        end = _OPEN_END
    if end < start:
        end = start
    return (start, end)


def windows_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _coerce_window_list(raw: Any) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                s, e = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if e < s:
                e = s
            out.append((s, e))
    return out


def normalize_read_windows(
    windows: dict[str, list[tuple[int, int]]] | dict[str, Any] | None,
) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for path, raw in dict(windows or {}).items():
        key = str(path or "").strip()
        if not key:
            continue
        items = _coerce_window_list(raw)
        if items:
            out[key] = items[-_MAX_WINDOWS_PER_PATH:]
    return out


def goal_needs_explore_gate(goal: str) -> bool:
    return (
        looks_like_coding_goal(goal)
        or looks_like_file_goal(goal)
        or looks_like_fs_web_goal(goal)
    )


def is_explore_only_actions(actions: list[Action]) -> bool:
    if not actions:
        return False
    kinds = [(a.kind or "").strip().lower() for a in actions]
    if any(k in _MUTATE_KINDS for k in kinds):
        return False
    return all(k in _EXPLORE_KINDS for k in kinds)


def is_explore_only_step(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    if not is_explore_only_actions(actions):
        return False
    if not results or len(results) < len(actions):
        return False
    return all(r.ok for r in results[: len(actions)])


def is_mutate_actions(actions: list[Action]) -> bool:
    if not actions:
        return False
    return any((a.kind or "").strip().lower() in _MUTATE_KINDS for a in actions)


def mutate_prefer_tool_kinds() -> frozenset[str]:
    return _MUTATE_PREFER_TOOL_KINDS


def mutate_prefer_with_verify_read_tool_kinds() -> frozenset[str]:
    return _MUTATE_PREFER_WITH_VERIFY_READ_TOOL_KINDS


def is_verify_read_only_actions(actions: list[Action]) -> bool:
    """仅定向核对类只读（反省开读轮允许，不触发 mutate_prefer 拒读重规划）。"""
    if not actions:
        return False
    kinds = [(a.kind or "").strip().lower() for a in actions]
    if any(k in _MUTATE_KINDS for k in kinds):
        return False
    return all(k in _VERIFY_READ_ONLY_KINDS for k in kinds)


def read_paths_from_actions(actions: list[Action]) -> list[str]:
    """本步成功只读涉及的 path（去重保序）。"""
    out: list[str] = []
    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in _READ_PATH_KINDS:
            continue
        args = action.args or {}
        path = str(args.get("path") or "").strip()
        if path and path not in out:
            out.append(path)
        for p in args.get("paths") or []:
            s = str(p or "").strip()
            if s and s not in out:
                out.append(s)
    return out


def update_read_path_counts(
    counts: dict[str, int],
    *,
    actions: list[Action],
    results: list[ExecResult],
) -> dict[str, int]:
    """累计成功 fs_read path 次数。"""
    out = dict(counts or {})
    if not is_explore_only_step(actions, results):
        return out
    for path in read_paths_from_actions(actions):
        out[path] = int(out.get(path) or 0) + 1
    return out


def step_read_windows(actions: list[Action]) -> dict[str, list[tuple[int, int]]]:
    """本步 fs_read / fs_read_many 的 path→行窗。"""
    out: dict[str, list[tuple[int, int]]] = {}
    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in _READ_PATH_KINDS:
            continue
        args = action.args or {}
        path = str(args.get("path") or "").strip()
        if path:
            out.setdefault(path, []).append(read_window_from_args(args))
        for p in args.get("paths") or []:
            s = str(p or "").strip()
            if not s:
                continue
            # read_many 通常无单窗参数，按整文件计
            out.setdefault(s, []).append(read_window_from_args(args if path == s else {}))
    return out


def step_has_overlapping_read(
    windows: dict[str, list[tuple[int, int]]] | None,
    *,
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """本步成功只读的行窗是否与历史同 path 窗口重叠。"""
    if not overlap_read_boost_enabled():
        return False
    if not is_explore_only_step(actions, results):
        return False
    prior = normalize_read_windows(windows)
    if not prior:
        return False
    for path, cur_wins in step_read_windows(actions).items():
        hist = prior.get(path) or []
        if not hist:
            continue
        for cur in cur_wins:
            if any(windows_overlap(cur, old) for old in hist):
                return True
    return False


def update_read_windows(
    windows: dict[str, list[tuple[int, int]]] | None,
    *,
    actions: list[Action],
    results: list[ExecResult],
) -> dict[str, list[tuple[int, int]]]:
    """合并本步成功只读行窗（供重叠软停滞）。"""
    out = normalize_read_windows(windows)
    if not is_explore_only_step(actions, results):
        return out
    for path, cur_wins in step_read_windows(actions).items():
        bucket = list(out.get(path) or [])
        for win in cur_wins:
            if win not in bucket:
                bucket.append(win)
        out[path] = bucket[-_MAX_WINDOWS_PER_PATH:]
    return out


def repeated_read_boost(
    counts: dict[str, int],
    *,
    actions: list[Action],
    results: list[ExecResult],
    read_windows: dict[str, list[tuple[int, int]]] | None = None,
) -> int:
    """重复 path 次数 / 重叠行窗 → 额外 streak 增量（可叠到 2）。"""
    if not is_explore_only_step(actions, results):
        return 0
    boost = 0
    boost_at = repeated_read_boost_threshold()
    if boost_at > 0:
        for path in read_paths_from_actions(actions):
            if int(counts.get(path) or 0) >= boost_at:
                boost += 1
                break
    if step_has_overlapping_read(
        read_windows, actions=actions, results=results
    ):
        boost += 1
    return boost


def update_explore_only_streak(
    streak: int,
    *,
    goal: str,
    write_set: list[str],
    actions: list[Action],
    results: list[ExecResult],
    read_path_counts: dict[str, int] | None = None,
    read_windows: dict[str, list[tuple[int, int]]] | None = None,
) -> int:
    if write_set or not goal_needs_explore_gate(goal):
        return 0
    if is_explore_only_step(actions, results):
        nxt = int(streak) + 1
        boost = repeated_read_boost(
            read_path_counts or {},
            actions=actions,
            results=results,
            read_windows=read_windows,
        )
        return nxt + boost
    if actions and not is_explore_only_actions(actions):
        return 0
    return int(streak)


def format_explore_gate_message(
    *,
    streak: int,
    gate: int,
    abort_at: int,
    repeated_paths: list[str] | None = None,
) -> str:
    abort_hint = (
        f"达到 {abort_at} 步将先强制改写一轮，仍无写入再 failed。"
        if abort_at > 0
        else "请尽快进入改写相位。"
    )
    repeat_hint = ""
    localized = bool(repeated_paths)
    if repeated_paths:
        shown = ", ".join(repeated_paths[:4])
        more = "…" if len(repeated_paths) > 4 else ""
        repeat_hint = f" 已对同一 path 反复只读（{shown}{more}）。"
    localized_hint = (
        "已定位改点却未写入，禁止继续只读探索。"
        if localized
        else "证据不足也须 need_input/failed 说明缺什么，禁止无新问题盲读。"
    )
    return (
        f"{GATE_MARKER}已连续 {streak} 步仅成功只读探索（阈值 {gate}），"
        f"且尚未写入任何路径。{repeat_hint}{localized_hint}"
        "下一步必须：fs_patch / fs_replace / fs_write"
        "（或 status=need_input / failed 说明缺什么），禁止无新问题继续盲读。"
        f"{abort_hint}"
    )


def format_mutate_force_message(
    *,
    streak: int,
    gate: int,
    abort_at: int,
    repeated_paths: list[str] | None = None,
) -> str:
    """中止阈值到达后、真正 failed 前的最后一轮改写机会。"""
    paths = ""
    if repeated_paths:
        shown = ", ".join(repeated_paths[:4])
        more = "…" if len(repeated_paths) > 4 else ""
        paths = f" 优先改这些已观察 path：{shown}{more}。"
    return (
        f"{MUTATE_FORCE_MARKER}已连续 {streak} 步只读（闸门 {gate} / 中止 {abort_at}），"
        f"这是 failed 前唯一强制改写轮。{paths}"
        "本步禁止 fs_read / fs_grep / fs_glob / explore_*；"
        "必须 fs_patch / fs_replace / fs_write（或 need_input / failed）。"
        "若本轮仍只读成功，将强制 failed。"
    )


def explore_gate_should_inject(*, streak: int, gate: int) -> bool:
    return gate > 0 and streak >= gate


def explore_gate_should_abort(*, streak: int, abort_at: int) -> bool:
    return abort_at > 0 and streak >= abort_at


def explore_gate_should_force_mutate(
    *,
    streak: int,
    abort_at: int,
    write_set: list[str],
    mutate_forced: bool,
) -> bool:
    """达中止阈值且尚未给过强制改写轮 → 先强制改写，不立刻 failed。"""
    if mutate_forced or write_set:
        return False
    return explore_gate_should_abort(streak=streak, abort_at=abort_at)


def explore_gate_should_abort_after_force(
    *,
    streak: int,
    abort_at: int,
    write_set: list[str],
    mutate_forced: bool,
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """强制改写轮之后仍成功只读 / 或已强制过仍无写且达阈值 → 真正 abort。"""
    if write_set or abort_at <= 0:
        return False
    if mutate_forced and is_explore_only_step(actions, results):
        return True
    if mutate_forced and explore_gate_should_abort(streak=streak, abort_at=abort_at):
        return True
    return False


def history_requests_mutate_prefer(history: list[dict[str, Any]] | None) -> bool:
    """近期 history 含闸门注入或改写强制 → 规划侧收紧工具面。"""
    if not history:
        return False
    for msg in reversed(list(history)[-8:]):
        text = str(msg.get("content") or "")
        if MUTATE_FORCE_MARKER in text:
            return True
        if LOCATE_BUDGET_MARKER in text and (
            "必须 fs_patch" in text or "下一步必须" in text
        ):
            return True
        if GATE_MARKER in text and (
            "下一步必须" in text or "禁止无新问题" in text or "禁止 fs_read" in text
        ):
            return True
    return False


def history_requests_verify_read(history: list[dict[str, Any]] | None) -> bool:
    """近 4 条含【反省开读】→ 写工具面临时放行定向 fs_read/fs_grep。

    只看很近的窗，避免开读标记长期削弱探索闸门；若其间又出现改写强制则取消。
    """
    if not history:
        return False
    for msg in reversed(list(history)[-4:]):
        text = str(msg.get("content") or "")
        if VERIFY_READ_MARKER in text:
            return True
        if MUTATE_FORCE_MARKER in text:
            return False
    return False


def format_verify_read_message(*, paths: list[str] | None = None) -> str:
    path_hint = ""
    cleaned = [p for p in (paths or []) if str(p or "").strip()]
    if cleaned:
        shown = ", ".join(cleaned[:4])
        path_hint = f"优先核对：{shown}。"
    return (
        f"{VERIFY_READ_MARKER}上一轮改写失败，本轮允许定向 fs_read/fs_grep/fs_glob 核对原文，"
        f"再立即 fs_patch/fs_replace；{path_hint}"
        "禁止无新问题盲读扩搜；禁止因「工具面曾禁读」直接 status=failed。"
    )


def hot_repeated_paths(
    counts: dict[str, int],
    *,
    threshold: int | None = None,
) -> list[str]:
    thr = repeated_read_boost_threshold() if threshold is None else threshold
    if thr <= 0:
        return []
    return [p for p, n in sorted((counts or {}).items()) if int(n) >= thr]


def locate_budget_threshold() -> int:
    """已定位 path:line / 入口 path 后，允许再只读探索的步数；0=关。默认 3。"""
    return _env_int("FAWAI_LOCATE_BUDGET_STEPS", 3)


def wide_grep_hit_threshold() -> int:
    """单次检索命中数 ≥ 此值视为宽检索；0=关。默认 20。"""
    return _env_int("FAWAI_WIDE_GREP_HITS", 20)


def _grep_hit_rows(result: ExecResult) -> list[dict[str, Any]]:
    data = result.data or {}
    hits = data.get("hits")
    if hits is None:
        hits = data.get("matches")
    if not isinstance(hits, list):
        return []
    return [h for h in hits if isinstance(h, dict)]


def extract_locate_anchors(
    actions: list[Action],
    results: list[ExecResult],
) -> list[str]:
    """从成功 grep/explore/glob 提取可复用定位锚点（path:line 或 path）。"""
    out: list[str] = []
    if not actions or not results:
        return out
    for i, result in enumerate(results):
        if not result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        kind = ((action.kind if action else "") or "").strip().lower()
        if kind in {"fs_grep", "explore_search", "code_symbols"}:
            for hit in _grep_hit_rows(result)[:12]:
                path = str(hit.get("path") or "").strip()
                line = hit.get("line")
                if path and line is not None and str(line).strip() != "":
                    key = f"{path}:{line}"
                elif path:
                    key = path
                else:
                    continue
                if key not in out:
                    out.append(key)
            # 无 hits 时用 count/path 兜底
            if not out:
                data = result.data or {}
                path = str((action.args or {}).get("path") or "").strip() if action else ""
                count = data.get("count")
                if path and isinstance(count, int) and count > 0 and path not in out:
                    out.append(path)
        elif kind == "fs_glob":
            paths = (result.data or {}).get("paths") or []
            if isinstance(paths, list):
                for raw in paths[:8]:
                    p = str(raw or "").strip()
                    # 跳过 runtime_releases 历史副本，避免假定位
                    if not p or "/runtime_releases/" in p or p.startswith("data/runtime"):
                        continue
                    if p not in out:
                        out.append(p)
    return out


def merge_locate_anchors(
    existing: list[str] | None,
    new_anchors: list[str],
    *,
    limit: int = 24,
) -> list[str]:
    out = [str(x) for x in (existing or []) if str(x).strip()]
    for a in new_anchors:
        s = str(a or "").strip()
        if s and s not in out:
            out.append(s)
    return out[: max(1, limit)]


def step_has_ok_mutate(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    if not actions or not results:
        return False
    for i, result in enumerate(results):
        if not result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        kind = ((action.kind if action else "") or "").strip().lower()
        if kind in _MUTATE_KINDS:
            return True
    return False


def update_post_locate_streak(
    streak: int,
    *,
    goal: str,
    locate_anchors: list[str],
    actions: list[Action],
    results: list[ExecResult],
    wide_grep_ban_paths: list[str] | None = None,
) -> int:
    """已有定位锚点且仍只读成功 → 累加定位后探索步数；成功 mutate 则清零。"""
    if not goal_needs_explore_gate(goal):
        return 0
    if step_has_ok_mutate(actions, results):
        return 0
    if not locate_anchors:
        return 0
    if is_explore_only_step(actions, results):
        nxt = int(streak) + 1
        if step_repeats_banned_wide_grep(
            actions=actions, banned_paths=wide_grep_ban_paths
        ):
            nxt += 1
        return nxt
    if actions and not is_explore_only_actions(actions):
        return int(streak)
    return int(streak)


def locate_budget_should_inject(*, streak: int, budget: int | None = None) -> bool:
    thr = locate_budget_threshold() if budget is None else budget
    return thr > 0 and streak >= thr


def format_locate_budget_message(
    *,
    streak: int,
    budget: int,
    anchors: list[str] | None = None,
) -> str:
    shown = ""
    if anchors:
        joined = ", ".join(anchors[:4])
        more = "…" if len(anchors) > 4 else ""
        shown = f" 已掌握锚点：{joined}{more}。"
    return (
        f"{LOCATE_BUDGET_MARKER}定位后已连续 {streak} 步仍只读（预算 {budget}）。{shown}"
        "禁止无新问题对同一 path / 同一 path:line 再 fs_grep/fs_read 扫读；"
        "下一步必须 fs_patch/fs_replace/fs_write，或 status=need_input/failed 说明缺什么。"
        "定位成功 ≠ 还要继续找；核对原文最多一次定向小窗。"
    )


def grep_fingerprints(actions: list[Action]) -> list[str]:
    """本步检索指纹：kind|path|pattern（小写）。"""
    out: list[str] = []
    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        args = action.args or {}
        path = str(args.get("path") or args.get("root") or "").strip().lower()
        pattern = str(
            args.get("pattern") or args.get("query") or args.get("q") or ""
        ).strip().lower()
        fp = f"{kind}|{path}|{pattern}"
        if fp not in out:
            out.append(fp)
    return out


def step_wide_grep_paths(
    actions: list[Action],
    results: list[ExecResult],
    *,
    hit_threshold: int | None = None,
) -> list[str]:
    """本步命中过宽的检索 path（或 root）。"""
    thr = wide_grep_hit_threshold() if hit_threshold is None else hit_threshold
    if thr <= 0:
        return []
    out: list[str] = []
    for i, result in enumerate(results):
        if not result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        kind = ((action.kind if action else "") or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        data = result.data or {}
        hits = _grep_hit_rows(result)
        count = data.get("count")
        n = len(hits)
        if isinstance(count, int):
            n = max(n, count)
        if n < thr:
            continue
        args = (action.args if action else {}) or {}
        path = str(args.get("path") or args.get("root") or "").strip() or "(repo)"
        if path not in out:
            out.append(path)
    return out


def format_wide_grep_message(
    *,
    paths: list[str],
    hit_threshold: int,
) -> str:
    shown = ", ".join(paths[:4]) if paths else "当前检索范围"
    more = "…" if len(paths) > 4 else ""
    return (
        f"{WIDE_GREP_MARKER}检索命中过宽（≥{hit_threshold}）或观察已压缩：{shown}{more}。"
        "下一步禁止对同一 path/范围用近似 pattern 再 grep；"
        "须收窄 pattern、加 context_before/after，或按已有 path:line 小窗 fs_read；"
        "已定位改点则直接 fs_patch/fs_replace。"
    )


def step_repeats_banned_wide_grep(
    *,
    actions: list[Action],
    banned_paths: list[str] | None,
) -> bool:
    """下一步是否又对刚降质的 path 做宽范围 grep。"""
    banned = [str(p).strip() for p in (banned_paths or []) if str(p).strip()]
    if not banned:
        return False
    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        args = action.args or {}
        path = str(args.get("path") or args.get("root") or "").strip()
        if not path:
            # 全仓 explore 也算撞「(repo)」
            if "(repo)" in banned:
                return True
            continue
        for ban in banned:
            if ban == "(repo)":
                continue
            if path == ban or path.startswith(ban.rstrip("/") + "/") or ban.startswith(
                path.rstrip("/") + "/"
            ):
                # 有 context 且 pattern 较长时仍算违规重复宽搜（降质后应定向）
                return True
    return False
