# term:观察反省  tool:brain_obs_reflect  doc:doc/功能/观察反省/说明.md
"""工具结果观察成功回灌后的轻量思考反省（软建议，非失败裁决）。"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fawai_avatar.types import Action, ExecResult

_VALID_PROGRESS = frozenset({"advanced", "stalled", "enough_to_answer"})


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def preview_chars() -> int:
    """观察反省入模预览字符预算。默认 2500；`0`=不裁切（全文入模）。"""
    return _env_int("FAWAI_OBS_REFLECT_PREVIEW_CHARS", 2500)


def _observe_dir(data_dir: str | Path | None) -> Path:
    root = Path(data_dir or os.environ.get("FAWAI_DATA_DIR") or "data")
    path = root / "observe"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dump_preview_full(
    text: str, *, data_dir: str | Path | None = None
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"obs_reflect_{stamp}_{uuid.uuid4().hex[:8]}_preview.txt"
    path = _observe_dir(data_dir) / name
    path.write_text(text, encoding="utf-8", errors="replace")
    return str(path.resolve())


def _rel_hint(dump_path: str) -> str:
    try:
        from fawai_avatar.config import RuntimeConfig

        root = Path(RuntimeConfig.from_env().work_root).expanduser().resolve()
        rel = str(Path(dump_path).resolve().relative_to(root))
        return f"；工作区内相对路径 `{rel}`（优先用此 path 做 fs_read）"
    except Exception:  # noqa: BLE001 — 相对路径提示失败不影响预览
        return ""


def build_obs_reflect_preview(
    tool_feedback: str,
    *,
    data_dir: str | Path | None = None,
    max_chars: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """为观察反省构造快速预览；超预算则头+尾，全文落盘供按需浏览。

    返回 (preview_text, meta)。meta 含 `preview_clipped` / `dump_path` / `chars`。
    裁切是**反省入模预算**，不是工具 stdout 未采到；文案禁止写成工具截断。
    """
    raw = (tool_feedback or "").strip()
    meta: dict[str, Any] = {
        "chars": len(raw),
        "preview_clipped": False,
    }
    if not raw:
        return "", meta

    budget = preview_chars() if max_chars is None else max(0, int(max_chars))
    if budget <= 0 or len(raw) <= budget:
        return raw, meta

    # 预留说明行；正文按 3:2 分头尾，保证尾部命令结果可见
    frame_reserve = 360
    body_budget = max(400, budget - frame_reserve)
    head_n = max(200, body_budget * 3 // 5)
    tail_n = max(160, body_budget - head_n)
    if head_n + tail_n >= len(raw):
        return raw, meta

    dump_path = _dump_preview_full(raw, data_dir=data_dir)
    meta["preview_clipped"] = True
    meta["dump_path"] = dump_path
    rel = _rel_hint(dump_path)

    head = raw[:head_n]
    tail = raw[-tail_n:]
    preview = (
        f"（反省预览：全文 {len(raw)} 字已回灌规划 history；"
        f"此处仅头+尾加速反省；中部未展示。"
        f"全文落盘 `{dump_path}`{rel}；"
        f"预览不够时下一轮用 fs_read 读该文件或直接依据 history 全文。"
        f"禁止把本预览裁切说成「工具 stdout / 输出被截断」。）\n"
        f"--- 头 {len(head)} 字 ---\n"
        f"{head}\n"
        f"--- 尾 {len(tail)} 字 ---\n"
        f"{tail}"
    )
    return preview, meta


@dataclass
class ObsReflection:
    """观察反省：成功工具结果后的进度小结与下一规划软建议。"""

    thought: str = ""
    progress: str = "advanced"  # advanced | stalled | enough_to_answer
    notes: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def obs_reflect_enabled() -> bool:
    """默认开；FAWAI_OBS_REFLECT=0/false/no/off 关闭。"""
    raw = os.environ.get("FAWAI_OBS_REFLECT", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _strip_markdown_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    return cleaned.strip()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_markdown_fences(text)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _str_list(value: Any, *, limit: int = 6) -> list[str]:
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
    elif isinstance(value, str) and value.strip():
        out.append(value.strip()[:240])
    return out


def obs_reflection_from_payload(data: dict[str, Any]) -> ObsReflection:
    progress = str(data.get("progress") or "advanced").strip().lower()
    if progress not in _VALID_PROGRESS:
        progress = "advanced"
    thought = str(data.get("thought") or data.get("summary") or "").strip()
    notes = _str_list(data.get("notes"))
    hints = _str_list(data.get("hints") or data.get("constraints"))
    if not thought:
        thought = "已消化本步工具结果，继续按目标推进。"
    return ObsReflection(
        thought=thought[:200],
        progress=progress,
        notes=notes,
        hints=hints,
        raw=data,
    )


def mock_obs_reflect(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    tool_feedback: str = "",
) -> ObsReflection:
    """无 LLM：按动作族给短进度与软建议。"""
    kinds = [
        str((a.kind or "").strip().lower())
        for a in actions
        if str(a.kind or "").strip()
    ]
    kind_blob = ",".join(kinds[:4]) or "tool"
    preview = (tool_feedback or "").strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:120] + "…"
    notes = [f"本步工具已成功：{kind_blob}"]
    if preview:
        notes.append(f"观察要点：{preview}")
    hints: list[str] = []
    progress = "advanced"
    if any(k in {"fs_grep", "explore_search", "fs_glob", "code_symbols"} for k in kinds):
        hints.append("若已有 path:line，下一轮小窗 fs_read 取证，禁止整文件盲读")
    if any(k in {"fs_read", "fs_read_many"} for k in kinds):
        hints.append("证据够则 fs_patch/作答；不够再定向补读，勿宽搜顶替")
    if any(k in {"fs_patch", "fs_replace", "fs_write"} for k in kinds):
        progress = "enough_to_answer"
        hints.append("已有写入时优先验收/code_check，再决定是否收尾")
    if not hints:
        hints.append("根据目标与已获证据选下一步；勿复述原始 stdout")
    goal_bit = (goal or "").strip()[:40]
    return ObsReflection(
        thought=f"观察反省（mock）：目标「{goal_bit}」本步 {kind_blob} 已成功",
        progress=progress,
        notes=notes[:4],
        hints=hints[:4],
        raw={"mock": True, "kinds": kinds},
    )


def obs_reflect_system_prompt() -> str:
    return """你是操作分身的观察反省模块。本步工具动作已成功，工具结果已回灌。
请消化结果，给出进度判断与给下一轮规划的软建议。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{"thought":"≤80字小结","progress":"advanced|stalled|enough_to_answer","notes":["已证实要点"],"hints":["给下一轮规划的软建议"]}
含义：
- advanced：相对目标有实质进展
- stalled：结果成功但对目标几乎无推进（空 hits、答非所问等）
- enough_to_answer：证据已够面向用户作答或收尾验收
禁止 abort/止损；禁止要求原样重复无新意图的同构步。
hints 为软建议，不是失败路径的硬约束；失败时仍以【执行反省】为准。
禁止在 thought/notes 写明文口令；不要粘贴整段 stdout。
关于「反省预览」：
- 用户消息里的工具结果可能是头+尾预览；中部未展示不等于工具失败或 stdout 被截断。
- 完整观察已在规划 history；若标注了全文落盘 path，hints 可建议下一轮 fs_read 该文件取中部，禁止因此 stalled 并重跑同一远端命令。
- 头或尾已含可验收事实时，按已见证据推进（可 enough_to_answer / advanced），勿只因「预览」宣称证据缺失。"""


def format_obs_reflect_user_message(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    tool_feedback: str = "",
    data_dir: str | Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """构造观察反省 user 消息；返回 (text, preview_meta)。"""
    lines = [
        f"目标：{goal}",
        "本步动作与结果：",
    ]
    for i, result in enumerate(results):
        action = actions[i] if i < len(actions) else None
        kind = action.kind if action else "?"
        args = action.args if action else {}
        lines.append(
            f"- {kind} args={json.dumps(args, ensure_ascii=False)[:300]} "
            f"→ {'ok' if result.ok else 'FAIL'}: {(result.message or '')[:200]}"
        )
    preview, preview_meta = build_obs_reflect_preview(
        tool_feedback, data_dir=data_dir
    )
    if preview:
        lines.append("")
        lines.append("工具结果观察预览：")
        lines.append(preview)
    return "\n".join(lines), preview_meta


def format_obs_reflection_for_history(reflection: ObsReflection) -> str:
    notes = "；".join(reflection.notes) if reflection.notes else "（无）"
    hints = "；".join(reflection.hints) if reflection.hints else "（无）"
    lines = [
        "【观察反省】",
        f"thought: {reflection.thought}",
        f"progress: {reflection.progress}",
        f"notes: {notes}",
        f"hints: {hints}",
    ]
    preview_meta = reflection.raw.get("preview") if isinstance(reflection.raw, dict) else None
    if isinstance(preview_meta, dict) and preview_meta.get("preview_clipped"):
        dump = str(preview_meta.get("dump_path") or "").strip()
        if dump:
            lines.append(
                f"preview: 反省入模为头+尾预览（全文 {preview_meta.get('chars', '?')} 字"
                f"已在上一条工具观察）；中部/全文可 fs_read `{dump}`；"
                "禁止当工具截断而重跑同构命令。"
            )
    lines.append(
        "下一轮规划应参考上述 hints（软建议）；"
        "若随后出现【执行反省】/【步骤重复】，以那些硬约束为准。"
    )
    return "\n".join(lines)


def _attach_preview_meta(
    reflection: ObsReflection, preview_meta: dict[str, Any]
) -> ObsReflection:
    if not preview_meta:
        return reflection
    raw = dict(reflection.raw or {})
    raw["preview"] = preview_meta
    reflection.raw = raw
    return reflection


def run_obs_reflect(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    tool_feedback: str = "",
    data_dir: str | Path | None = None,
    llm: Any | None = None,
) -> ObsReflection:
    """观察反省入口（供 Brain.obs_reflect / 闭环调用）。"""
    mock_on = os.environ.get("FAWAI_BRAIN_MOCK", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    user_msg, preview_meta = format_obs_reflect_user_message(
        goal,
        actions,
        results,
        tool_feedback=tool_feedback,
        data_dir=data_dir,
    )
    if mock_on or llm is None:
        return _attach_preview_meta(
            mock_obs_reflect(
                goal, actions, results, tool_feedback=tool_feedback
            ),
            preview_meta,
        )

    from fawai_avatar.llm.client import LlmClient

    client = llm if isinstance(llm, LlmClient) else LlmClient()
    messages = [
        {"role": "system", "content": obs_reflect_system_prompt()},
        {"role": "user", "content": user_msg},
    ]
    result = client.chat(messages, tools=None, thinking_role="obs_reflect")
    if not result.get("ok"):
        base = mock_obs_reflect(
            goal, actions, results, tool_feedback=tool_feedback
        )
        base.thought = f"观察反省 LLM 失败，回退启发式：{base.thought}"
        base.raw = {**(base.raw or {}), "llm_error": result.get("error")}
        return _attach_preview_meta(base, preview_meta)

    content = str(result.get("content") or "").strip()
    payload = _parse_json_object(content)
    if payload is None:
        base = mock_obs_reflect(
            goal, actions, results, tool_feedback=tool_feedback
        )
        base.thought = f"观察反省 JSON 解析失败，回退启发式：{base.thought}"
        return _attach_preview_meta(base, preview_meta)

    reflection = obs_reflection_from_payload(payload)
    raw = dict(reflection.raw or {})
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    if usage:
        raw["usage"] = usage
    thinking = result.get("thinking")
    if isinstance(thinking, dict) and thinking:
        raw["thinking"] = thinking
    if result.get("model"):
        raw["model"] = result.get("model")
    if usage or (isinstance(thinking, dict) and thinking) or result.get("model"):
        reflection.raw = raw
    return _attach_preview_meta(reflection, preview_meta)
