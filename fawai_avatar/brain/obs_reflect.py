# term:观察反省  tool:brain_obs_reflect  doc:doc/功能/观察反省/说明.md
"""工具结果观察成功回灌后的轻量思考反省（软建议，非失败裁决）。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
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


# 反省入模超预算时的强制口径：压缩摘录不是原文
REFLECT_DIGEST_RULE = (
    "【反省材料·非原文】以下是工具结果的压缩摘录，不是原文。"
    "只用于分析处置策略（有没有推进、够不够收尾、下一类动作往哪走）。"
    "禁止把摘录当作原文引用，禁止当作已读全文，"
    "禁止为补全摘录去读取落盘或重跑同一命令。"
    "原文在规划 history 的工具观察里；信息是否够用由规划决定。"
    "大文件用头/尾/中间起行取证是正常手段；摘录已含头+尾时不得因未整文件 cat 判关键缺失。"
)


def build_obs_reflect_preview(
    tool_feedback: str,
    *,
    goal: str = "",
    data_dir: str | Path | None = None,
    max_chars: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """为观察反省构造入模材料。未超预算则原文；超预算走工具结果压缩。

    返回 (preview_text, meta)。meta 含 `preview_clipped` / `dump_path` / `chars`。
    压缩摘录不是原文，只用于处置策略。
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

    from fawai_avatar.observe.tool_digest import digest_text

    digested, part = digest_text(
        raw,
        goal=goal,
        label="obs_reflect",
        data_dir=data_dir,
        soft_override=budget,
        lead=REFLECT_DIGEST_RULE,
    )
    if not part.get("compressed"):
        return raw, meta
    meta["preview_clipped"] = True
    meta["digest"] = True
    dump_path = str(part.get("dump_path") or "")
    if dump_path:
        meta["dump_path"] = dump_path
    return digested, meta


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


def obs_reflect_system_prompt(*, effort: bool = False, brief: bool = False) -> str:
    from fawai_avatar.brain.effort import obs_reflect_effort_rule

    effort_line = obs_reflect_effort_rule(effort=effort)
    if brief:
        return f"""你是操作分身的**短观察反省**模块（step_run=brief）。本步工具已成功。
只做轻量核对：有没有跑偏、能否继续。禁止长篇推理。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{{"thought":"≤40字","progress":"advanced|stalled|enough_to_answer","notes":["≤2条短要点"],"hints":["≤2条软建议"]}}
含义：
- advanced：对本步目标有推进
- stalled：成功但对目标几乎无用
- enough_to_answer：已够作答或收尾
{effort_line}
禁止 abort；禁止复述整段 stdout；禁止明文口令。
超预算材料是压缩摘录不是原文；勿要求补读落盘还原。"""

    return f"""你是操作分身的观察反省模块。本步工具动作已成功，工具结果已回灌。
请消化结果，给出进度判断与给下一轮规划的软建议。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{{"thought":"≤80字小结","progress":"advanced|stalled|enough_to_answer","notes":["已证实要点"],"hints":["给下一轮规划的软建议"]}}
含义：
- advanced：相对目标有实质进展
- stalled：结果成功但对目标几乎无推进（空 hits、答非所问等）
- enough_to_answer：证据已够面向用户作答或收尾验收
{effort_line}
禁止 abort/止损；禁止要求原样重复无新意图的同构步。
hints 为软建议，不是失败路径的硬约束；失败时仍以【执行反省】为准。
禁止在 thought/notes 写明文口令；不要粘贴整段 stdout。
关于「反省材料」（强制）：
- 未超预算时，工具结果就是原文。超预算时你看到的是压缩摘录，不是原文。
- 摘录只用于分析处置策略：进展、是否够收尾、下一类动作。
- 禁止把摘录当作原文引用，禁止当作已读全文。
- 禁止因为摘录不全而要求补读落盘、或重跑同一命令来还原原文。原文在规划 history 的工具观察里；够不够由规划决定。
- 大文件取证：查开头、查结尾、按中间起始行读窗是正常手段（head/tail/sed -n、fs_read offset/limit）。本步若已按窗口取到所需字段，不得因「未整文件 cat」判关键缺失或 stalled。
- 压缩摘录若已含「头…行」与「尾…行」，尾部证据视为可见；勿只凭开头片段否定结尾已出现的路径/指标。
- 摘录里已经能判断方向时，按已见证据给 advanced / stalled / enough_to_answer。"""


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
        tool_feedback, goal=goal, data_dir=data_dir
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
        lines.append(
            "preview: 反省看到的是压缩摘录（非原文），只用于分析处置策略，不能作为原文处理。"
            "禁止把摘录当作原文引用，禁止为补全摘录去读取落盘或重跑同一命令。"
            "摘录已含头+尾时不得因未整文件 cat 判关键缺失；大文件用头/尾/中间起行取证属正常。"
            "原文在上一条工具观察；是否再取证由规划决定。"
        )
    lines.append(
        "下一轮规划应参考上述 hints（软建议）；"
        "若随后出现【执行反省】/【步骤重复】，以那些硬约束为准。"
    )
    return "\n".join(lines)


_DIGEST_REREAD = re.compile(
    r"obs_reflect_|preview\.txt|补全摘录|补读落盘|当作原文|已读全文"
)


def _mentions_digest_reread(text: str, dump_path: str) -> bool:
    body = text or ""
    if dump_path and dump_path in body:
        return True
    return _DIGEST_REREAD.search(body) is not None


def _strip_digest_as_source(
    reflection: ObsReflection, dump_path: str
) -> ObsReflection:
    """压缩摘录不能被反省写成「去把落盘当原文读回来」。"""
    reflection.hints = [
        item
        for item in reflection.hints
        if not _mentions_digest_reread(str(item), dump_path)
    ]
    reflection.notes = [
        item
        for item in reflection.notes
        if not _mentions_digest_reread(str(item), dump_path)
    ]
    thought = reflection.thought or ""
    if dump_path and dump_path in thought:
        reflection.thought = thought.replace(dump_path, "压缩摘录")
    return reflection


def _attach_preview_meta(
    reflection: ObsReflection, preview_meta: dict[str, Any]
) -> ObsReflection:
    if not preview_meta:
        return reflection
    raw = dict(reflection.raw or {})
    raw["preview"] = preview_meta
    reflection.raw = raw
    if preview_meta.get("preview_clipped"):
        reflection = _strip_digest_as_source(
            reflection, str(preview_meta.get("dump_path") or "")
        )
    return reflection


def run_obs_reflect(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    tool_feedback: str = "",
    data_dir: str | Path | None = None,
    llm: Any | None = None,
    on_llm_delta: Any | None = None,
    effort: bool = False,
    brief: bool = False,
) -> ObsReflection:
    """观察反省入口（供 Brain.obs_reflect / 闭环调用）。

    brief=True：短观察反省（step_run=brief），短提示且强制关深度思考。
    """
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
        base = mock_obs_reflect(
            goal, actions, results, tool_feedback=tool_feedback
        )
        if brief:
            base.thought = (base.thought or "")[:40]
            base.notes = list(base.notes or [])[:2]
            base.hints = list(base.hints or [])[:2]
            raw = dict(base.raw or {})
            raw["step_run"] = "brief"
            base.raw = raw
        return _attach_preview_meta(base, preview_meta)

    from fawai_avatar.llm.client import LlmClient

    client = llm if isinstance(llm, LlmClient) else LlmClient()
    messages = [
        {
            "role": "system",
            "content": obs_reflect_system_prompt(
                effort=bool(effort), brief=bool(brief)
            ),
        },
        {"role": "user", "content": user_msg},
    ]
    chat_kw: dict[str, Any] = {
        "tools": None,
        "thinking_role": "obs_reflect",
        "on_delta": on_llm_delta,
    }
    if brief:
        # 短观察反省：显式关思考，盖过档案
        chat_kw["enable_thinking"] = False
    result = client.chat(messages, **chat_kw)
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
    if brief:
        reflection.thought = (reflection.thought or "")[:40]
        reflection.notes = list(reflection.notes or [])[:2]
        reflection.hints = list(reflection.hints or [])[:2]
    raw = dict(reflection.raw or {})
    if brief:
        raw["step_run"] = "brief"
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    if usage:
        raw["usage"] = usage
    thinking = result.get("thinking")
    if isinstance(thinking, dict) and thinking:
        raw["thinking"] = thinking
    reasoning = result.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        raw["thinking_text"] = reasoning.strip()
    if result.get("model"):
        raw["model"] = result.get("model")
    if (
        brief
        or usage
        or (isinstance(thinking, dict) and thinking)
        or result.get("model")
        or raw.get("thinking_text")
    ):
        reflection.raw = raw
    return _attach_preview_meta(reflection, preview_meta)
