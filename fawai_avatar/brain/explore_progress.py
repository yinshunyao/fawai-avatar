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
        "py_write",
        "py_pip",
        "py_run",
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
VERIFY_MUST_PATCH_MARKER = "【开读须改写】"
LOCATE_BUDGET_MARKER = "【定位预算】"
LOCATE_MUTATE_FORCE_MARKER = "【定位改写强制】"
WIDE_GREP_MARKER = "【宽检索降质】"

# 锚点质量：文档 / 演示 / 测试路径降权（通用后缀与目录模式，不绑具体业务文件）
_DOC_NAME_PREFIXES = ("readme", "changelog", "license", "contributing", "authors")
_LOW_PRIORITY_DIR_PARTS = (
    "/demo_",
    "/demos/",
    "/examples/",
    "/example/",
    "/fixtures/",
    "/testdata/",
    "/__tests__/",
    "/test/",
    "/tests/",
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def explore_only_gate_threshold(*, goal: str = "") -> int:
    """连续只读探索多少步后注入软提示；0=关闭（默认）。

    多轮分片 fs_read 属正常取证，默认不因只读步数逼写。
    仅显式 `FAWAI_EXPLORE_ONLY_GATE`>0 时注入软提示（不再按前端 goal 默认 5/8）。
    """
    _ = goal  # 保留参数兼容；默认关后不再按 fs_web 收紧
    raw = (os.environ.get("FAWAI_EXPLORE_ONLY_GATE") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def explore_only_abort_threshold(*, goal: str = "") -> int:
    """连续只读达此值则【探索改写强制】；默认关。

    须显式 `FAWAI_EXPLORE_ONLY_ABORT`>0；未设置时即使 GATE>0 也不自动 2×GATE。
    """
    _ = goal
    raw = (os.environ.get("FAWAI_EXPLORE_ONLY_ABORT") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


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


def explore_progress_armed(
    *,
    goal: str,
    locate_anchors: list[str] | None = None,
    behavior_armed: bool = False,
) -> bool:
    """探索/定位进度是否应跟踪。

    除 goal 启发式外：已有定位锚点，或本 run 已成功只读探索过（行为武装），
    均启用——避免口语 goal 漏检导致有锚点仍无限只读。
    """
    if goal_needs_explore_gate(goal):
        return True
    if any(str(x or "").strip() for x in (locate_anchors or [])):
        return True
    return bool(behavior_armed)


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
    locate_anchors: list[str] | None = None,
    behavior_armed: bool = False,
) -> int:
    armed = explore_progress_armed(
        goal=goal,
        locate_anchors=locate_anchors,
        behavior_armed=behavior_armed,
    )
    if write_set or not armed:
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
    """软提示：提效 / 读不到则 failed；默认产品路径不注入（GATE=0）。"""
    abort_hint = (
        f" 实验开关：达 {abort_at} 步将强制改写轮。"
        if abort_at > 0
        else " 默认不因只读步数强制 mutate。"
    )
    repeat_hint = ""
    if repeated_paths:
        shown = ", ".join(repeated_paths[:4])
        more = "…" if len(repeated_paths) > 4 else ""
        repeat_hint = f" 已对同一 path 多次只读（{shown}{more}）。"
    return (
        f"{GATE_MARKER}已连续 {streak} 步仅成功只读探索（软提示阈值 {gate}），"
        f"且尚未写入。{repeat_hint}"
        "多轮分片取证允许；优先提高读取效率（path:line 小窗、读 dump、收窄 pattern），"
        "证据已够再 fs_patch/fs_replace；读不到目标材料则 status=failed/need_input。"
        f"{abort_hint}"
    )


def format_mutate_force_message(
    *,
    streak: int,
    gate: int,
    abort_at: int,
    repeated_paths: list[str] | None = None,
) -> str:
    """仅当显式开启 FAWAI_EXPLORE_ONLY_ABORT 时使用。"""
    paths = ""
    if repeated_paths:
        shown = ", ".join(repeated_paths[:4])
        more = "…" if len(repeated_paths) > 4 else ""
        paths = f" 已观察 path：{shown}{more}。"
    return (
        f"{MUTATE_FORCE_MARKER}已连续 {streak} 步只读（闸门 {gate} / 强制 {abort_at}，实验开关已开）。"
        f"{paths}"
        "本步须 fs_patch / fs_replace / fs_write，或 need_input / failed 说明读不到的材料；"
        "若本轮仍只读成功，将 failed。默认产品路径应关闭本强制。"
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
    """近期 history 含闸门注入或改写强制 → 规划侧收紧工具面。

    已退役的【反省开读】/【开读须改写】标记不再驱动 mutate_prefer（易把找文件收成禁定位）。
    """
    if not history:
        return False
    for msg in reversed(list(history)[-8:]):
        text = str(msg.get("content") or "")
        if MUTATE_FORCE_MARKER in text or LOCATE_MUTATE_FORCE_MARKER in text:
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


def history_requests_verify_must_patch(history: list[dict[str, Any]] | None) -> bool:
    """【开读须改写】硬闸已退役：恒为 False（保留符号供旧调用/单测）。"""
    _ = history
    return False


def history_requests_verify_read(history: list[dict[str, Any]] | None) -> bool:
    """【反省开读】硬闸已退役：恒为 False（保留符号供旧调用/单测）。"""
    _ = history
    return False


def extract_exact_old_windows(
    results: list[ExecResult] | None,
    *,
    max_windows: int = 2,
    max_chars: int = 800,
) -> list[str]:
    """从补丁失败 message 抽出可复制的「原文小窗」块，供开读提示内联。"""
    out: list[str] = []
    for result in results or []:
        msg = str(getattr(result, "message", None) or "")
        if "原文小窗" not in msg and "须逐字作 old" not in msg:
            continue
        idx = msg.find("原文小窗")
        if idx < 0:
            idx = msg.find("须逐字作 old")
        if idx < 0:
            continue
        chunk = msg[idx:].strip()
        if len(chunk) > max_chars:
            chunk = chunk[: max_chars - 1] + "…"
        if chunk and chunk not in out:
            out.append(chunk)
        if len(out) >= max(1, max_windows):
            break
    return out


def is_blind_file_head_read(actions: list[Action] | None) -> bool:
    """是否在已知改写失败后仍做文件头大窗盲读（offset≤1 且 limit 偏大）。"""
    for action in actions or []:
        kind = (action.kind or "").strip().lower()
        if kind != "fs_read":
            continue
        args = action.args or {}
        raw_off = args.get("offset")
        if raw_off is None:
            raw_off = args.get("start_line")
        if raw_off is None:
            raw_off = args.get("line")
        try:
            offset = int(raw_off) if raw_off is not None else 1
        except (TypeError, ValueError):
            offset = 1
        if offset == 0:
            offset = 1
        if offset > 1:
            continue
        raw_lim = args.get("limit")
        if raw_lim is None and args.get("end_line") is not None:
            try:
                end_line = int(args.get("end_line"))
                raw_lim = max(0, end_line - offset + 1)
            except (TypeError, ValueError):
                raw_lim = 200
        try:
            limit = int(raw_lim) if raw_lim is not None else 200
        except (TypeError, ValueError):
            limit = 200
        if limit >= 80:
            return True
    return False


def format_verify_read_message(
    *,
    paths: list[str] | None = None,
    exact_windows: list[str] | None = None,
) -> str:
    """文案保留供单测/历史对照；闭环不再注入（硬闸已退役）。"""
    path_hint = ""
    cleaned = [p for p in (paths or []) if str(p or "").strip()]
    if cleaned:
        shown = ", ".join(cleaned[:4])
        path_hint = f"优先核对：{shown}。"
    windows = [w for w in (exact_windows or []) if str(w or "").strip()]
    window_block = ""
    if windows:
        joined = "\n---\n".join(windows[:2])
        window_block = (
            f"\n已附失败回执原文小窗（可直接逐字作 old，无需再读文件头）：\n{joined}\n"
        )
    return (
        f"{VERIFY_READ_MARKER}（已退役硬闸，仅文案）上一轮改写失败时可定向小窗核对；"
        f"{path_hint}"
        "old/new 宜来自失败回执「原文小窗」或本轮小窗逐字复制；"
        "仍允许 fs_glob/fs_grep 纠路径，禁止因标记禁定位。"
        f"{window_block}"
    )


def format_verify_must_patch_message(
    *,
    paths: list[str] | None = None,
    reason: str = "",
) -> str:
    """文案保留供单测/历史对照；闭环不再注入（硬闸已退役）。"""
    path_hint = ""
    cleaned = [p for p in (paths or []) if str(p or "").strip()]
    if cleaned:
        path_hint = f"目标 path：{', '.join(cleaned[:4])}。"
    why = (reason or "").strip() or "开读后仍只读"
    return (
        f"{VERIFY_MUST_PATCH_MARKER}（已退役硬闸，仅文案）{why}。{path_hint}"
        "不再禁止 fs_read/fs_grep/fs_glob；可继续定位或 patch，"
        "或 status=failed/need_input。"
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
    """已定位后「只读步数注入」阈值；0=关（默认）。

    分片/多轮小窗 fs_read 属正常取证，默认不因定位后步数逼写。
    仅显式设 `FAWAI_LOCATE_BUDGET_STEPS`>0 时才注入软提示。
    """
    return _env_int("FAWAI_LOCATE_BUDGET_STEPS", 0)


def locate_force_threshold(*, budget: int | None = None) -> int:
    """定位后强制改写的绝对步数；默认关。

    须显式设 `FAWAI_LOCATE_FORCE_STEPS`>0 才启用【定位改写强制】与随后 abort。
    未设置时恒为 0（即使 budget>0 也不自动 budget+1），避免「读几轮就被逼 mutate」。
    """
    _ = budget  # 保留参数兼容调用方；强制阈值不再隐含绑定 budget+1
    raw = (os.environ.get("FAWAI_LOCATE_FORCE_STEPS") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def wide_grep_hit_threshold() -> int:
    """目录 / 仓范围单次检索命中数 ≥ 此值视为宽检索；0=关。

    默认 `0`：不做宽检索降质注入；体量靠工具 head_limit，由模型自行翻页。
    """
    return _env_int("FAWAI_WIDE_GREP_HITS", 0)


def wide_grep_file_hit_threshold() -> int:
    """单文件 path 上的命中阈值；0=沿用目录阈值。默认 0（降质总开关关时无意义）。"""
    return _env_int("FAWAI_WIDE_GREP_FILE_HITS", 0)


_FILE_PATH_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".json",
        ".md",
        ".mdc",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".less",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".rb",
        ".php",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".txt",
        ".xml",
    }
)


def looks_like_file_path(path: str) -> bool:
    """path 是否像单文件（按后缀启发式，不依赖磁盘）。"""
    raw = (path or "").strip().rstrip("/")
    if not raw or raw in {".", "(repo)", "*"}:
        return False
    name = raw.rsplit("/", 1)[-1]
    if "." not in name or name.startswith("."):
        return False
    dot = name.rfind(".")
    return name[dot:].lower() in _FILE_PATH_SUFFIXES


def effective_wide_grep_threshold(path: str, *, hit_threshold: int | None = None) -> int:
    """按检索范围取生效阈值：单文件用更高值，目录用目录阈值。"""
    base = wide_grep_hit_threshold() if hit_threshold is None else int(hit_threshold)
    if base <= 0:
        return 0
    if not looks_like_file_path(path):
        return base
    file_thr = wide_grep_file_hit_threshold()
    if file_thr <= 0:
        return base
    return max(base, file_thr)


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


def _anchor_path(anchor: str) -> str:
    raw = str(anchor or "").strip()
    if not raw:
        return ""
    # path:line → path；Windows 盘符保留
    if ":" in raw:
        left, right = raw.rsplit(":", 1)
        if right.isdigit():
            return left
    return raw


def anchor_quality_score(anchor: str) -> int:
    """越高越优先作为 mutate 目标提示；文档/demo/test 降权。"""
    path = _anchor_path(anchor).replace("\\", "/")
    if not path:
        return -1
    name = path.rsplit("/", 1)[-1].lower()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    score = 2
    if ":" in str(anchor) and str(anchor).rsplit(":", 1)[-1].isdigit():
        score += 2
    if looks_like_file_path(path):
        score += 2
    lowered = path.lower()
    if any(stem.startswith(p) for p in _DOC_NAME_PREFIXES) or name.endswith(".md"):
        score -= 4
    if any(part in lowered for part in _LOW_PRIORITY_DIR_PARTS):
        score -= 3
    if name.startswith("demo_") or "/demo_" in lowered:
        score -= 2
    return score


def preferred_locate_anchors(
    anchors: list[str] | None,
    *,
    limit: int = 4,
) -> list[str]:
    """按质量排序后取前若干条，供提示与 mutate 优先 path。"""
    cleaned = [str(x).strip() for x in (anchors or []) if str(x).strip()]
    if not cleaned:
        return []
    ranked = sorted(
        cleaned,
        key=lambda a: (-anchor_quality_score(a), cleaned.index(a)),
    )
    return ranked[: max(1, limit)]


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
    # 保留全量但按质量重排，避免提示/强制轮被 README 占满
    ranked = sorted(
        out,
        key=lambda a: (-anchor_quality_score(a), out.index(a)),
    )
    return ranked[: max(1, limit)]


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
    wide_grep_ban_patterns: dict[str, str] | None = None,
    behavior_armed: bool = False,
) -> int:
    """已有定位锚点且仍只读成功 → 累加定位后探索步数；成功 mutate 则清零。

    有锚点时**不依赖** goal 启发式（口语「修改一下代码」漏检仍计预算）。
    """
    _ = (goal, behavior_armed)  # 锚点本身即武装；保留参数兼容调用方
    if step_has_ok_mutate(actions, results):
        return 0
    if not any(str(x or "").strip() for x in (locate_anchors or [])):
        return 0
    if is_explore_only_step(actions, results):
        nxt = int(streak) + 1
        if step_repeats_banned_wide_grep(
            actions=actions,
            banned_paths=wide_grep_ban_paths,
            banned_patterns=wide_grep_ban_patterns,
        ):
            nxt += 1
        return nxt
    if actions and not is_explore_only_actions(actions):
        return int(streak)
    return int(streak)


def locate_budget_should_inject(*, streak: int, budget: int | None = None) -> bool:
    thr = locate_budget_threshold() if budget is None else budget
    return thr > 0 and streak >= thr


def locate_budget_should_force_mutate(
    *,
    streak: int,
    budget: int | None = None,
    write_set: list[str],
    mutate_forced: bool,
) -> bool:
    """定位后只读超过 force 阈值且尚未强制过 → 先强制改写一轮。"""
    if mutate_forced or write_set:
        return False
    force_at = locate_force_threshold(budget=budget)
    return force_at > 0 and streak >= force_at


def locate_budget_should_abort_after_force(
    *,
    streak: int,
    budget: int | None = None,
    write_set: list[str],
    mutate_forced: bool,
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """定位强制改写轮之后仍成功只读 → 真正 abort。"""
    if write_set:
        return False
    force_at = locate_force_threshold(budget=budget)
    if force_at <= 0:
        return False
    if mutate_forced and is_explore_only_step(actions, results):
        return True
    if mutate_forced and streak >= force_at:
        return True
    return False


def format_locate_budget_message(
    *,
    streak: int,
    budget: int,
    anchors: list[str] | None = None,
) -> str:
    shown = ""
    preferred = preferred_locate_anchors(anchors, limit=4)
    if preferred:
        joined = ", ".join(preferred)
        more = "…" if anchors and len(anchors) > len(preferred) else ""
        shown = f" 已掌握锚点：{joined}{more}。"
    return (
        f"{LOCATE_BUDGET_MARKER}定位后已连续 {streak} 步仍只读（软提示阈值 {budget}）。{shown}"
        "多轮分片 fs_read 取证允许；优先提高读取效率（path:line 小窗、读 dump、收窄 pattern），"
        "证据已够再 fs_patch/fs_replace；若读不到目标材料则 status=failed/need_input 说明缺口。"
        "默认不因只读步数强制 mutate。"
    )


def format_locate_mutate_force_message(
    *,
    streak: int,
    budget: int,
    anchors: list[str] | None = None,
) -> str:
    """仅当显式开启 FAWAI_LOCATE_FORCE_STEPS 时使用的强制改写轮文案。"""
    preferred = preferred_locate_anchors(anchors, limit=4)
    paths = ""
    if preferred:
        shown = ", ".join(_anchor_path(a) or a for a in preferred)
        paths = f" 已定位 path：{shown}。"
    force_at = locate_force_threshold(budget=budget)
    return (
        f"{LOCATE_MUTATE_FORCE_MARKER}定位后已连续 {streak} 步只读"
        f"（软提示 {budget} / 强制阈值 {force_at}，实验开关已开）。{paths}"
        "本步须 fs_patch / fs_replace / fs_write，或 need_input / failed 说明读不到的材料；"
        "若本轮仍只读成功，将 failed。默认产品路径应关闭本强制。"
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
        pattern = _action_grep_pattern(args).lower()
        fp = f"{kind}|{path}|{pattern}"
        if fp not in out:
            out.append(fp)
    return out


def _action_grep_pattern(args: dict[str, Any] | None) -> str:
    raw = args or {}
    for key in ("pattern", "query", "q", "symbols_query"):
        val = raw.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    queries = raw.get("queries")
    if isinstance(queries, list):
        parts = [str(q).strip() for q in queries if str(q).strip()]
        if parts:
            return " | ".join(parts[:4])
    if isinstance(queries, str) and queries.strip():
        return queries.strip()
    return ""


def _action_grep_context_span(args: dict[str, Any] | None) -> int:
    raw = args or {}
    try:
        before = int(raw.get("context_before") or raw.get("B") or 0)
    except (TypeError, ValueError):
        before = 0
    try:
        after = int(raw.get("context_after") or raw.get("A") or 0)
    except (TypeError, ValueError):
        after = 0
    return max(0, before) + max(0, after)


def pattern_clearly_narrower(prev: str, new: str) -> bool:
    """新 pattern 相对上次是否明显更特异。"""
    p = (prev or "").strip().lower()
    n = (new or "").strip().lower()
    if not n:
        return False
    if len(n) >= 16 and len(n) > len(p):
        return True
    if len(n) >= len(p) + 8:
        return True
    if len(p) > 0 and len(n) >= max(8, int(len(p) * 1.5)):
        return True
    if p and p in n and len(n) >= len(p) + 4:
        return True
    return False


def grep_call_is_narrowed(
    args: dict[str, Any] | None,
    *,
    prev_pattern: str = "",
) -> bool:
    """本步检索是否已收窄（可放行，不按重复宽搜处罚）。"""
    pattern = _action_grep_pattern(args)
    ctx = _action_grep_context_span(args)
    path = str((args or {}).get("path") or (args or {}).get("root") or "").strip()
    if prev_pattern and pattern_clearly_narrower(prev_pattern, pattern):
        return True
    if len(pattern) >= 16:
        return True
    if ctx > 0 and len(pattern) >= 6:
        return True
    if looks_like_file_path(path) and len(pattern) >= 10 and ctx > 0:
        return True
    return False


def _result_grep_hit_count(result: ExecResult, *, kind: str) -> int:
    data = result.data or {}
    hits = _grep_hit_rows(result)
    n = len(hits)
    count = data.get("count")
    if isinstance(count, int):
        n = max(n, count)
    if kind == "explore_search" and n == 0:
        # explore 顶层无 hits：取各 grep 通道最大 count
        for block in data.get("results") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("kind") or "").strip().lower() != "grep":
                continue
            c = block.get("count")
            if isinstance(c, int):
                n = max(n, c)
            block_hits = block.get("hits")
            if isinstance(block_hits, list):
                n = max(n, len(block_hits))
    return n


def step_wide_grep_paths(
    actions: list[Action],
    results: list[ExecResult],
    *,
    hit_threshold: int | None = None,
) -> list[str]:
    """本步命中过宽的检索 path（或 root）。单文件用更高阈值。"""
    base = wide_grep_hit_threshold() if hit_threshold is None else hit_threshold
    if base <= 0:
        return []
    out: list[str] = []
    for i, result in enumerate(results):
        if not result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        kind = ((action.kind if action else "") or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        args = (action.args if action else {}) or {}
        path = str(args.get("path") or args.get("root") or "").strip() or "(repo)"
        thr = effective_wide_grep_threshold(path, hit_threshold=base)
        if thr <= 0:
            continue
        n = _result_grep_hit_count(result, kind=kind)
        if n < thr:
            continue
        if path not in out:
            out.append(path)
    return out


def step_wide_grep_patterns(
    actions: list[Action],
    results: list[ExecResult],
    *,
    hit_threshold: int | None = None,
) -> dict[str, str]:
    """本步过宽检索的 path → pattern，供后续判断是否已收窄。"""
    paths = set(step_wide_grep_paths(actions, results, hit_threshold=hit_threshold))
    out: dict[str, str] = {}
    if not paths:
        return out
    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        args = action.args or {}
        path = str(args.get("path") or args.get("root") or "").strip() or "(repo)"
        if path not in paths:
            continue
        pattern = _action_grep_pattern(args)
        if pattern:
            out[path] = pattern
    return out


def format_wide_grep_message(
    *,
    paths: list[str],
    hit_threshold: int,
) -> str:
    shown = ", ".join(paths[:4]) if paths else "当前检索范围"
    more = "…" if len(paths) > 4 else ""
    return (
        f"{WIDE_GREP_MARKER}检索命中过宽（目录≥{hit_threshold} / "
        f"单文件≥{wide_grep_file_hit_threshold() or hit_threshold}）：{shown}{more}。"
        "下一步禁止对同一 path/范围用近似 pattern 再 grep；"
        "须收窄 pattern、加 context_before/after，或按已有 path:line 小窗 fs_read；"
        "已定位改点则直接 fs_patch/fs_replace。"
    )


COMPRESSED_HITS_MARKER = "【检索观察压缩】"
COMPRESSED_UNREAD_BAN_MARKER = "【压缩未读·禁宽搜】"


def format_compressed_hits_message(*, paths: list[str]) -> str:
    """hits 观察被压缩：须先小窗取证；同范围近似宽搜禁扫（进 ban 名单由闭环挂接）。"""
    shown = ", ".join(paths[:4]) if paths else "当前检索"
    more = "…" if len(paths) > 4 else ""
    return (
        f"{COMPRESSED_HITS_MARKER}hits 观察已压缩：{shown}{more}。"
        "压缩摘要 ≠ 取证完成：下一步须按 path:line 小窗 fs_read（或读 observe dump）；"
        "禁止同范围再发近似宽 grep；已收窄 pattern 且带 context 后方可再搜。"
    )


def format_compressed_unread_ban_message(*, dump_paths: list[str]) -> str:
    """有压缩落盘且尚未 fs_read：硬禁宽搜，逼取证相位。

    闭环在已对源码 path 小窗取证或已进入 mutate 后应抑制本注入（见
    `should_suppress_compressed_unread_ban`），避免检索 hits 误压后拴死改写。
    """
    shown: list[str] = []
    for raw in dump_paths:
        p = str(raw or "").strip()
        if p and p not in shown:
            shown.append(p)
        if len(shown) >= 3:
            break
    joined = "、".join(f"`{p}`" for p in shown) or "（observe 落盘）"
    return (
        f"{COMPRESSED_UNREAD_BAN_MARKER}工具结果已压缩且全文未读：{joined}。"
        "本步禁止 fs_grep/explore_search 宽搜；必须先 fs_read 上述 dump 或已有 path:line 小窗取证，"
        "再改写/作答；禁止用再搜一轮顶替取证；禁止 shell cat 代替 fs_read。"
    )


def step_has_search_actions(actions: list[Action] | None) -> bool:
    """本步是否含仓检索类动作（宽搜空转检测用）。"""
    for action in actions or []:
        kind = (action.kind or "").strip().lower()
        if kind in {"fs_grep", "explore_search"}:
            return True
    return False


def step_repeats_banned_wide_grep(
    *,
    actions: list[Action],
    banned_paths: list[str] | None,
    banned_patterns: dict[str, str] | None = None,
) -> bool:
    """下一步是否又对刚降质的 path 做仍偏宽的 grep。

    已明显收窄（更长/更特异 pattern，或带 context）则放行，与降质文案一致。
    """
    banned = [str(p).strip() for p in (banned_paths or []) if str(p).strip()]
    if not banned:
        return False
    patterns = {
        str(k).strip(): str(v)
        for k, v in dict(banned_patterns or {}).items()
        if str(k).strip() and str(v).strip()
    }

    def _prev_for(path: str, ban: str) -> str:
        return patterns.get(path) or patterns.get(ban) or ""

    for action in actions:
        kind = (action.kind or "").strip().lower()
        if kind not in {"fs_grep", "explore_search"}:
            continue
        args = action.args or {}
        path = str(args.get("path") or args.get("root") or "").strip()
        if not path:
            # 全仓 explore：无 path 且仍宽才算撞「(repo)」
            if "(repo)" in banned and not grep_call_is_narrowed(
                args, prev_pattern=patterns.get("(repo)", "")
            ):
                return True
            continue
        for ban in banned:
            if ban == "(repo)":
                # 显式全仓禁扫：任何未收窄的仓级检索
                if path in {".", "(repo)"} and not grep_call_is_narrowed(
                    args, prev_pattern=_prev_for(path, ban)
                ):
                    return True
                continue
            overlaps = (
                path == ban
                or path.startswith(ban.rstrip("/") + "/")
                or ban.startswith(path.rstrip("/") + "/")
            )
            if not overlaps:
                continue
            if grep_call_is_narrowed(args, prev_pattern=_prev_for(path, ban)):
                continue
            return True
    return False
