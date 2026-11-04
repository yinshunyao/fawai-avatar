# term:观察方式  doc:doc/议题/观察选取策略/结论.md
# term:观察选取  doc:doc/功能/观察/说明.md
"""观察方式目录 + 硬闸门裁候选；LLM 经 Plan.observe_kinds 在候选内选取。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.brain.capabilities import looks_like_coding_goal, looks_like_file_goal
from fawai_avatar.config import RuntimeConfig
from fawai_avatar.types import Observation

# 方式 id → 界面中文名（进度/气泡用）
OBSERVE_LABELS: dict[str, str] = {
    "obs_screen": "截屏",
    "obs_fs": "工作区文件",
    "obs_ax": "AX控件树",
    "obs_shell": "脚本查询",
    "obs_tool": "工具结果",
    "none": "跳过",
}

_ENV_METHODS = frozenset({"obs_screen", "obs_fs", "obs_ax", "obs_shell", "none"})


def observe_label(method: str) -> str:
    return OBSERVE_LABELS.get(method, method or "观察")


# 路径/源码扩展：可作为「结构化只读取证」硬闸门信号
_STRUCTURED_OBS_EXTS = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".htm",
    ".css",
    ".json",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".rs",
    ".go",
    ".java",
)


def _looks_like_structured_observe_goal(goal: str) -> bool:
    if looks_like_coding_goal(goal) or looks_like_file_goal(goal):
        return True
    lowered = (goal or "").lower()
    if any(ext in lowered for ext in _STRUCTURED_OBS_EXTS):
        return True
    return any(
        tip in lowered
        for tip in (
            "工作区",
            "仓库",
            "源码",
            "代码",
            "文件",
            "目录",
            "fs_",
            "glob",
            "grep",
            "前端",
            "组件",
            "粘贴",
            "paste",
            "onpaste",
            "datatransfer",
            "对话窗口",
            "web对话",
            "修改支持",
            "需要修改",
            "不支持需要",
            "页面",
            "index.html",
            "探仓",
            "explore_search",
        )
    )


def candidate_observe_methods(goal: str, *, capture: bool) -> list[str]:
    """硬闸门：缩小候选集（不含 obs_tool；工具结果观察在动作后固定走）。"""
    if not capture:
        return ["none"]
    try:
        from fawai_avatar.platform_info import is_headless

        headless = is_headless()
    except Exception:  # noqa: BLE001
        headless = False
    if headless:
        return ["obs_fs", "none"]
    if _looks_like_structured_observe_goal(goal):
        # 结构化：不以截屏/AX 为候选
        return ["obs_fs", "none"]
    # 非结构化且有桌面：截屏 +（macOS）AX + 工作区
    cands = ["obs_fs", "obs_screen", "none"]
    try:
        from fawai_avatar.platform_info import runtime_platform

        ax_ok = runtime_platform().system == "Darwin"
    except Exception:  # noqa: BLE001
        import sys

        ax_ok = sys.platform == "darwin"
    if ax_ok:
        cands.insert(1, "obs_ax")
    return cands


def choose_observe_methods(goal: str, *, capture: bool) -> list[str]:
    """步 0 / 无 LLM 点选时的默认方式（候选内取偏好）。

    禁止无点选就默截屏：GUI 类目标默认 none，由计划 observe_kinds 再选 obs_screen。
    """
    cands = candidate_observe_methods(goal, capture=capture)
    if not capture:
        return ["none"]
    if "obs_fs" in cands and _looks_like_structured_observe_goal(goal):
        return ["obs_fs"]
    # 非结构化（含 GUI）：步 0 跳过截屏；候选仍可含 obs_screen 供下一步点选
    if "none" in cands:
        return ["none"]
    return [cands[0]]


def resolve_observe_methods(
    goal: str,
    *,
    capture: bool,
    requested: list[str] | None = None,
) -> list[str]:
    """将 LLM 点选与硬闸门候选求交；无效则回落默认。"""
    cands = candidate_observe_methods(goal, capture=capture)
    cand_set = set(cands)
    picked: list[str] = []
    for raw in requested or []:
        m = str(raw or "").strip().lower()
        if m == "obs_tool":
            continue  # 环境观察步不用
        if m in {"screen", "screenshot", "ui_observe"}:
            m = "obs_screen"
        if m in {"fs", "workspace", "files"}:
            m = "obs_fs"
        if m in {"ax", "accessibility", "ui_tree", "obs_a11y"}:
            m = "obs_ax"
        if m in {"skip", "no_observe"}:
            m = "none"
        if m in cand_set and m in _ENV_METHODS and m not in picked:
            picked.append(m)
    if picked:
        return picked
    return choose_observe_methods(goal, capture=capture)


def collect_observation(
    methods: list[str],
    *,
    screen_observe: Any | None = None,
    goal: str = "",
    capture: bool = True,
    frontmost_probe: Any | None = None,
    observe_args: dict[str, Any] | None = None,
) -> Observation:
    """按方式采集并合并为一条 Observation；meta 带 observe_methods / 下一步候选。

    无论何种环境观察方式，都会附加廉价前台窗摘要（可注入 frontmost_probe 便于冒烟）。
    observe_args.obs_screen.screen_target 控制截哪块屏（默认 frontmost）。
    """
    from fawai_avatar.observe.displays import (
        format_displays_block,
        list_displays,
        screen_target_from_observe_args,
    )

    methods = [m for m in methods if m] or ["none"]
    try:
        from fawai_avatar.platform_info import is_headless

        headless = is_headless()
    except Exception:  # noqa: BLE001
        headless = False
    displays = [] if headless else list_displays()
    screen_target = screen_target_from_observe_args(observe_args)
    parts: list[Observation] = []
    for method in methods:
        if method == "obs_screen":
            if headless:
                parts.append(
                    Observation(
                        summary=(
                            "无界面运行，无法截屏（obs_screen）。"
                            "请改用 obs_fs / shell / fs_* 取证。"
                        ),
                        meta={
                            "observe_method": "obs_screen",
                            "ok": False,
                            "reason": "headless",
                        },
                    )
                )
                continue
            if screen_observe is None:
                from fawai_avatar.observe.screen import observe as screen_observe_fn

                def _default_screen_observe() -> Observation:
                    return screen_observe_fn(
                        screen_target=screen_target, displays=displays
                    )

                screen_observe = _default_screen_observe
            try:
                # 注入版 screen_observe 可无参；兼容旧无参 mock
                try:
                    obs = screen_observe(
                        screen_target=screen_target, displays=displays
                    )
                except TypeError:
                    obs = screen_observe()
            except Exception as exc:  # noqa: BLE001 — 截屏失败不崩闭环
                parts.append(
                    Observation(
                        summary=f"截屏失败：{exc}",
                        meta={
                            "observe_method": "obs_screen",
                            "ok": False,
                            "reason": "capture_failed",
                            "error": str(exc),
                            "screen_target": screen_target,
                        },
                    )
                )
                continue
            meta = dict(obs.meta or {})
            meta["observe_method"] = "obs_screen"
            meta.setdefault("screen_target", screen_target)
            if displays and "displays" not in meta:
                meta["displays"] = displays
            parts.append(
                Observation(
                    summary=obs.summary,
                    image_path=obs.image_path,
                    width=obs.width,
                    height=obs.height,
                    meta=meta,
                )
            )
        elif method == "obs_fs":
            parts.append(_observe_workspace())
        elif method == "obs_ax":
            try:
                from fawai_avatar.observe.ax_tree import observe_ax

                parts.append(observe_ax())
            except Exception as exc:  # noqa: BLE001
                parts.append(
                    Observation(
                        summary=f"obs_ax 失败：{exc}",
                        meta={
                            "observe_method": "obs_ax",
                            "ok": False,
                            "reason": "ax_exception",
                            "error": str(exc),
                        },
                    )
                )
        elif method == "none":
            parts.append(
                Observation(
                    summary=(
                        "本步未截屏、未采工作区目录（capture 关闭、步 0 默认跳过或模型点选 none）。"
                        "可见前台态见下方前台窗摘要；需要画面时请在 observe_kinds 点选 obs_screen；"
                        "需要控件树时点选 obs_ax。"
                    ),
                    meta={"observe_method": "none"},
                )
            )
        elif method == "obs_shell":
            parts.append(
                Observation(
                    summary="obs_shell 尚未落地；请用 shell 动作做只读探活，或改选 obs_fs。",
                    meta={"observe_method": "obs_shell"},
                )
            )
        else:
            parts.append(
                Observation(
                    summary=f"未知观察方式 {method!r}，已跳过。",
                    meta={"observe_method": method},
                )
            )
    merged = _merge_observations(parts, methods)
    meta = dict(merged.meta or {})
    meta["observe_candidates"] = candidate_observe_methods(goal, capture=capture)
    meta["observe_methods"] = methods
    meta["observe_label"] = "、".join(observe_label(m) for m in methods)
    if displays:
        meta["displays"] = displays
        block = format_displays_block(displays)
        summary = (merged.summary or "").rstrip()
        if "显示器清单" not in summary:
            summary = f"{summary}\n\n{block}" if summary else block
        merged = Observation(
            summary=summary,
            image_path=merged.image_path,
            width=merged.width,
            height=merged.height,
            meta=meta,
        )
    else:
        merged = Observation(
            summary=merged.summary,
            image_path=merged.image_path,
            width=merged.width,
            height=merged.height,
            meta=meta,
        )
    from fawai_avatar.observe.frontmost import attach_frontmost

    return attach_frontmost(merged, probe=frontmost_probe)


def progress_observe_text(
    methods: list[str],
    *,
    pending: bool = True,
    preview: str = "",
    max_preview_chars: int = 1800,
) -> str:
    """环境观察进度文案；完成态须附带实际采到的摘要（含前台窗），禁止只写标签。"""
    labels = [observe_label(m) for m in methods]
    joined = "、".join(labels) if labels else "观察"
    if pending:
        return f"观察中（{joined}）…"
    head = f"观察完成（{joined}）"
    body = (preview or "").strip()
    if not body:
        return head
    if len(body) > max_preview_chars:
        body = body[: max_preview_chars - 1] + "…"
    return f"{head}\n{body}"


def progress_tool_observe_text(
    *,
    pending: bool = True,
    compressed: bool = False,
    preview: str = "",
    max_preview_chars: int = 1800,
) -> str:
    """工具结果观察进度文案；完成时可附带摘要供时间线展示。"""
    if pending:
        return "观察工具结果…"
    head = "观察工具结果完成（已压缩）" if compressed else "观察工具结果完成"
    body = (preview or "").strip()
    if not body:
        return head
    # 去掉与 head 重复的标题行，只留可看的结果
    lines = body.splitlines()
    if lines and ("工具结果观察" in lines[0] or lines[0].startswith("执行结果")):
        lines = lines[1:]
        body = "\n".join(lines).strip()
    if not body:
        return head
    if len(body) > max_preview_chars:
        body = body[: max_preview_chars - 1] + "…"
    return f"{head}\n{body}"


def image_caption_for_observation(obs: Observation | None) -> str:
    if obs is None:
        return "观察"
    meta = obs.meta or {}
    label = str(meta.get("observe_label") or "").strip()
    if label:
        return f"观察（{label}）"
    if obs.image_path:
        return "观察（截屏）"
    return "观察"


def _observe_workspace() -> Observation:
    cfg = RuntimeConfig.from_env()
    root = Path(cfg.work_root).expanduser().resolve()
    listing: list[str] = []
    try:
        if root.is_dir():
            entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for p in entries[:24]:
                mark = "/" if p.is_dir() else ""
                listing.append(f"{p.name}{mark}")
            more = max(0, sum(1 for _ in root.iterdir()) - len(listing))
            if more > 0:
                listing.append(f"…另有 {more} 项")
    except OSError as exc:
        listing = [f"（列举失败：{exc}）"]
    body = "\n".join(f"- {x}" for x in listing) if listing else "- （空）"
    summary = (
        f"工作区文件观察（未截屏）。根目录：{root}\n"
        f"顶层条目：\n{body}\n"
        "需要更细证据时请用 fs_read / fs_glob / shell 等只读工具。"
    )
    return Observation(
        summary=summary,
        meta={"observe_method": "obs_fs", "work_root": str(root)},
    )


def _merge_observations(parts: list[Observation], methods: list[str]) -> Observation:
    if not parts:
        return Observation(
            summary="无观察结果",
            meta={
                "observe_methods": methods,
                "observe_method": methods[0] if methods else "none",
                "observe_label": observe_label(methods[0] if methods else "none"),
            },
        )
    if len(parts) == 1:
        obs = parts[0]
        method = str((obs.meta or {}).get("observe_method") or methods[0])
        meta = dict(obs.meta or {})
        meta["observe_method"] = method
        meta["observe_methods"] = methods
        meta["observe_label"] = "、".join(observe_label(m) for m in methods)
        return Observation(
            summary=obs.summary,
            image_path=obs.image_path,
            width=obs.width,
            height=obs.height,
            meta=meta,
        )
    summaries = []
    image_path = None
    width = height = None
    for obs in parts:
        mid = (obs.meta or {}).get("observe_method") or "?"
        summaries.append(f"[{observe_label(str(mid))}]\n{obs.summary}")
        if image_path is None and obs.image_path:
            image_path = obs.image_path
            width, height = obs.width, obs.height
    return Observation(
        summary="\n\n".join(summaries),
        image_path=image_path,
        width=width,
        height=height,
        meta={
            "observe_methods": methods,
            "observe_method": methods[0],
            "observe_label": "、".join(observe_label(m) for m in methods),
        },
    )
