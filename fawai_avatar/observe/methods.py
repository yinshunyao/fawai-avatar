# term:观察方式  doc:doc/议题/观察选取策略/结论.md
# term:观察选取  doc:doc/功能/观察/说明.md
"""观察方式目录 + 硬闸门裁候选；LLM 经 Plan.observe_kinds 在候选内选取。"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from fawai_avatar.brain.capabilities import looks_like_coding_goal, looks_like_file_goal
from fawai_avatar.config import RuntimeConfig
from fawai_avatar.types import Observation

# (text, *, phase=..., **meta) — 与 Worker._progress 对齐
ObserveProgressEmit = Callable[..., None]

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


# 读文档 / 看图内容：画面在文件或用户附图里，截本机屏幕与任务无关
_DOC_IMAGE_CONTENT_HINTS = (
    "文档",
    "图片",
    "附图",
    "照片",
    "扫描",
    "图中",
    "图里",
    "这张图",
    "看图",
    "读图",
    ".pdf",
    "pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
)

# 明确要看或操作当前桌面时，仍允许截屏（覆盖上面的文档/附图判断）
_DESKTOP_OBSERVE_HINTS = (
    "截屏",
    "截个屏",
    "截图当前",
    "看屏幕",
    "屏幕上",
    "当前屏幕",
    "当前界面",
    "前台窗口",
    "操作界面",
    "操控界面",
    "点击",
    "键入",
    "双击",
    "右键",
    "ui_click",
    "ui_type",
    "obs_screen",
    "obs_ax",
)


def goal_asks_desktop_screen(goal: str) -> bool:
    """目标明确要看或操作当前桌面 / App 界面。"""
    g = goal or ""
    return any(h.lower() in g.lower() for h in _DESKTOP_OBSERVE_HINTS)


def goal_is_doc_or_image_content(goal: str) -> bool:
    """任务内容是文档、图片文件或用户附图，不是本机屏幕。"""
    from fawai_avatar.brain.prompt_profile import goal_has_user_image

    if goal_has_user_image(goal):
        return True
    text = (goal or "").lower()
    return any(h.lower() in text for h in _DOC_IMAGE_CONTENT_HINTS)


def goal_screen_unrelated(goal: str) -> bool:
    """文档/附图内容且未要求看桌面：候选里去掉截屏。"""
    if goal_asks_desktop_screen(goal):
        return False
    return goal_is_doc_or_image_content(goal)


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
    if _looks_like_structured_observe_goal(goal) or goal_screen_unrelated(goal):
        # 结构化，或文档/附图内容：不以截屏/AX 为候选
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
    if goal_screen_unrelated(goal):
        # 文档/附图已在目标里：步 0 不截屏，也不空刷工作区顶层
        return ["none"]
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
    on_progress: ObserveProgressEmit | None = None,
    progress_base: dict[str, Any] | None = None,
) -> Observation:
    """按方式采集并合并为一条 Observation；meta 带 observe_methods / 下一步候选。

    无论何种环境观察方式，都会附加廉价前台窗摘要（可注入 frontmost_probe 便于冒烟）。
    observe_args.obs_screen.screen_target 控制截哪块屏（默认 frontmost）。
    若提供 on_progress：截屏本机完成与 OCR API 分开发射（见 rules-progress-local-vs-api）。
    """
    from fawai_avatar.observe.displays import (
        format_displays_block,
        list_displays,
        screen_target_from_observe_args,
    )

    methods = [m for m in methods if m] or ["none"]
    base = dict(progress_base or {})
    stages: list[str] = []

    def _emit(text: str, *, phase: str, **extra: Any) -> None:
        if on_progress is None:
            return
        payload = {**base, **extra}
        try:
            on_progress(text, phase=phase, **payload)
        except Exception:  # noqa: BLE001 — 进度不得打断观察
            return
        if phase not in stages:
            stages.append(phase)

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
            t_cap = time.perf_counter()
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
            cap_ms = int((time.perf_counter() - t_cap) * 1000)
            meta = dict(obs.meta or {})
            meta["observe_method"] = "obs_screen"
            meta.setdefault("screen_target", screen_target)
            meta["capture_elapsed_ms"] = cap_ms
            if displays and "displays" not in meta:
                meta["displays"] = displays
            screen_obs = Observation(
                summary=obs.summary,
                image_path=obs.image_path,
                width=obs.width,
                height=obs.height,
                meta=meta,
            )
            # 本机截屏完成卡（不含 OCR）
            paths = [screen_obs.image_path] if screen_obs.image_path else []
            _emit(
                progress_observe_text(
                    ["obs_screen"],
                    pending=False,
                    preview=str(screen_obs.summary or ""),
                ),
                phase="observe",
                elapsed_ms=cap_ms,
                observe_methods=["obs_screen"],
                observe_method="obs_screen",
                observe_label=observe_label("obs_screen"),
                paths=paths,
                local_op=True,
            )
            from fawai_avatar.observe.screen_grounding import (
                SCREENSHOT_GROUNDING_OCR,
                apply_screenshot_grounding,
                get_screenshot_grounding,
            )

            mode = get_screenshot_grounding()
            if mode == SCREENSHOT_GROUNDING_OCR:
                ocr_model = ""
                try:
                    from fawai_avatar.llm.settings_store import get_store

                    ocr_model = str(get_store().resolve_route("ocr").model or "").strip()
                except Exception:  # noqa: BLE001
                    ocr_model = ""
                ocr_model = ocr_model or "qwen3.5-ocr"
                wait_text = f"OCR 识别中（{ocr_model}）…"
                _emit(
                    wait_text,
                    phase="observe_ocr",
                    observe_label="OCR",
                    model=ocr_model,
                    screenshot_grounding="ocr",
                    api_wait=True,
                    live=True,
                )
                waiter = None
                if on_progress is not None:
                    from fawai_avatar.runtime.api_wait import ApiWaitProgress

                    def _ocr_emit(text: str, *, phase: str, **extra: Any) -> None:
                        _emit(text, phase=phase, **extra)

                    waiter = ApiWaitProgress(
                        _ocr_emit,
                        phase="observe_ocr",
                        step=int(base.get("step") or 0),
                        waiting_text=wait_text,
                        interval_sec=2.0,
                        observe_label="OCR",
                        model=ocr_model,
                        screenshot_grounding="ocr",
                    )
                    waiter.start()
                t_ocr = time.perf_counter()
                try:
                    screen_obs = apply_screenshot_grounding(screen_obs)
                except Exception as exc:  # noqa: BLE001 — 接地失败不崩闭环
                    m2 = dict(screen_obs.meta or {})
                    m2["screenshot_grounding_error"] = str(exc)
                    m2["ocr_ok"] = False
                    m2["ocr_error"] = str(exc)
                    screen_obs = Observation(
                        summary=f"{screen_obs.summary}\n截屏识别后处理失败：{exc}",
                        image_path=screen_obs.image_path,
                        width=screen_obs.width,
                        height=screen_obs.height,
                        meta=m2,
                    )
                finally:
                    if waiter is not None:
                        waiter.stop()
                ocr_ms = int((time.perf_counter() - t_ocr) * 1000)
                om = dict(screen_obs.meta or {})
                om["ocr_elapsed_ms"] = ocr_ms
                screen_obs = Observation(
                    summary=screen_obs.summary,
                    image_path=screen_obs.image_path,
                    width=screen_obs.width,
                    height=screen_obs.height,
                    meta=om,
                )
                ocr_body = _ocr_progress_body(screen_obs)
                _emit(
                    progress_ocr_text(
                        pending=False,
                        preview=ocr_body,
                        model=str(om.get("ocr_model") or ocr_model),
                        ok=bool(om.get("ocr_ok")),
                    ),
                    phase="observe_ocr",
                    elapsed_ms=ocr_ms,
                    observe_label="OCR",
                    model=om.get("ocr_model") or ocr_model,
                    ocr_ok=om.get("ocr_ok"),
                    ocr_count=om.get("ocr_count"),
                    ocr_error=om.get("ocr_error"),
                    ocr_transport=om.get("ocr_transport"),
                    tokens=om.get("ocr_usage") or {},
                    content_text=ocr_body,
                    screenshot_grounding="ocr",
                    paths=paths,
                    api_call=True,
                )
            else:
                try:
                    screen_obs = apply_screenshot_grounding(screen_obs)
                except Exception as exc:  # noqa: BLE001 — 接地失败不崩闭环
                    m2 = dict(screen_obs.meta or {})
                    m2["screenshot_grounding_error"] = str(exc)
                    screen_obs = Observation(
                        summary=f"{screen_obs.summary}\n截屏识别后处理失败：{exc}",
                        image_path=screen_obs.image_path,
                        width=screen_obs.width,
                        height=screen_obs.height,
                        meta=m2,
                    )
            parts.append(screen_obs)
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
    if stages:
        meta["progress_stages"] = list(stages)
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


def _ocr_progress_body(obs: Observation) -> str:
    """从接地后的观察里抽出 OCR 进度正文（不含本机截屏路径行）。"""
    meta = dict(obs.meta or {})
    lines: list[str] = []
    if meta.get("ocr_ok") is True:
        lines.append(
            f"OCR 成功 · nodes={meta.get('ocr_count') or 0}"
            f" · model={meta.get('ocr_model') or '?'}"
            f" · transport={meta.get('ocr_transport') or '?'}"
        )
    elif meta.get("ocr_ok") is False:
        lines.append(
            f"OCR 失败 · model={meta.get('ocr_model') or '?'}"
            f" · error={meta.get('ocr_error') or meta.get('screenshot_grounding_error') or '?'}"
        )
    summary = str(obs.summary or "")
    # 摘要里 OCR 表通常在「OCR 观察」之后
    marker = "OCR 观察"
    idx = summary.find(marker)
    if idx >= 0:
        lines.append(summary[idx:].strip())
    elif "OCR 失败" in summary:
        for ln in summary.splitlines():
            if "OCR" in ln:
                lines.append(ln.strip())
    return "\n".join(lines).strip() or summary.strip()


def progress_ocr_text(
    *,
    pending: bool = True,
    preview: str = "",
    model: str = "",
    ok: bool | None = None,
    max_preview_chars: int = 12000,
) -> str:
    """OCR API 进度文案；与本机截屏分卡。"""
    model_s = (model or "").strip()
    if pending:
        return f"OCR 识别中（{model_s}）…" if model_s else "OCR 识别中…"
    if ok is True:
        head = f"OCR 完成（{model_s}）" if model_s else "OCR 完成"
    elif ok is False:
        head = f"OCR 失败（{model_s}）" if model_s else "OCR 失败"
    else:
        head = f"OCR 结束（{model_s}）" if model_s else "OCR 结束"
    body = (preview or "").strip()
    if not body:
        return head
    if len(body) > max_preview_chars:
        body = body[: max_preview_chars - 1] + "…"
    return f"{head}\n{body}"


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
    if meta.get("ocr_ok") is True:
        return "观察（截屏+OCR）"
    if meta.get("screenshot_grounding") == "ocr":
        return "观察（截屏+OCR）"
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
