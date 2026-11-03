# term:执行反省  tool:brain_reflect  doc:doc/功能/执行反省/说明.md
# term:步骤重复  tool:brain_stagnation  doc:doc/功能/步骤重复/说明.md
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from fawai_avatar.types import Action, ExecResult

_VALID_DECISIONS = frozenset({"retry", "alternate", "abort"})
_STAGNATION_DECISIONS = frozenset({"continue", "alternate", "abort"})

_FINGERPRINT_KEYS = (
    "cmd",
    "command",
    "argv",
    "path",
    "query",
    "kind",
    "pattern",
    "id",
    "keys",
    "text",
    "content",
    # fs_read 行范围：正式名 + 常见别名，避免同 path 不同区间被当成同一步
    "offset",
    "limit",
    "start_line",
    "end_line",
    "line",
)


@dataclass
class Reflection:
    """执行反省：失败后的结构化裁决。"""

    cause: str
    decision: str  # retry | alternate | abort
    constraints: list[str] = field(default_factory=list)
    thought: str = ""
    fingerprint: str = ""
    forced: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def action_fingerprint(action: Action) -> str:
    """动作指纹：kind + 规范化关键 args（内容截断）。"""
    parts: list[str] = [str(action.kind or "").strip().lower()]
    args = action.args or {}
    for key in _FINGERPRINT_KEYS:
        if key not in args:
            continue
        val = args[key]
        if isinstance(val, list):
            rendered = json.dumps(val, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(val).strip()
        if key in {"content", "text"} and len(rendered) > 120:
            rendered = rendered[:120]
        parts.append(f"{key}={rendered}")
    blob = "|".join(parts)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def step_fingerprint(
    actions: list[Action],
    *,
    observe_methods: list[str] | None = None,
) -> str:
    """步骤指纹：本步动作指纹保序串联，可选附带观察方式集合。"""
    if not actions:
        return ""
    parts = [action_fingerprint(a) for a in actions]
    if observe_methods:
        obs = ",".join(
            sorted(str(m).strip().lower() for m in observe_methods if str(m).strip())
        )
        if obs:
            parts.append(f"obs={obs}")
    blob = "|".join(parts)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def step_repeat_enabled() -> bool:
    raw = os.environ.get("FAWAI_STEP_REPEAT", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def step_repeat_threshold() -> int:
    raw = os.environ.get("FAWAI_STEP_REPEAT_N", "2").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 2
    return max(2, n)


def failed_action_fingerprints(
    actions: list[Action], results: list[ExecResult]
) -> list[str]:
    fps: list[str] = []
    for i, result in enumerate(results):
        if result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        if action is None:
            continue
        fps.append(action_fingerprint(action))
    return fps


_ARGS_FORMAT_FAIL_MARKERS = (
    "requires path",
    "requires paths",
    "requires content",
    "requires path, old, new",
    "fs_patch requires path",
    "fs_write requires path",
    "fs_replace requires path",
    "requires hunks or old+new",
    "hunks 须为",
    "hunks JSON",
    "each hunk must be",
)

_VERIFY_READ_HINTS = (
    "fs_read",
    "核对原文",
    "old not found",
    "未读",
    "精确提取",
    "先读取",
    "省略号",
    "截断",
    "target missing",
    "臆造",
)


def is_tool_args_format_failure(results: list[ExecResult]) -> bool:
    """是否像「参数结构/缺 path」类格式失败（非语义/业务失败）。"""
    for result in results:
        if result.ok:
            continue
        msg = str(result.message or "")
        low = msg.lower()
        if any(marker in low for marker in _ARGS_FORMAT_FAIL_MARKERS):
            return True
        if "参数" in msg and ("结构" in msg or "嵌套" in msg):
            return True
    return False


def soften_abort_when_mutated_format_error(
    reflection: Reflection,
    *,
    write_set: list[str],
    results: list[ExecResult],
) -> Reflection:
    """已有成功写入时：纯参数格式失败不得整任务 abort，降为 alternate 再给一轮。"""
    if reflection.decision != "abort":
        return reflection
    if not write_set:
        return reflection
    if not is_tool_args_format_failure(results):
        return reflection
    constraints = [
        "工具参数须扁平：path/old/new/content 放在顶层，禁止再包一层 args JSON 字符串",
        "本任务已有成功写入；优先验收或补必要补丁，禁止因格式噪声整任务 failed",
    ]
    for item in reflection.constraints:
        text = str(item or "").strip()
        if text and text not in constraints:
            constraints.append(text)
    return Reflection(
        cause=(
            f"{reflection.cause}；但本任务已写入 {', '.join(write_set[:3])}，"
            "参数格式失败不整任务止损"
        ),
        decision="alternate",
        constraints=constraints[:6],
        thought=(
            "已有 mutate 副作用时，格式类失败应展平参数后继续验收/补丁，"
            "而非整任务 failed"
        ),
        fingerprint=reflection.fingerprint,
        forced=False,
        raw={
            **dict(reflection.raw or {}),
            "softened_format_abort": True,
            "softened_write_set": list(write_set)[:8],
        },
    )


def apply_fingerprint_guard(
    reflection: Reflection,
    *,
    fingerprint: str,
    fail_counts: dict[str, int],
    prior_alternate_fps: set[str] | None = None,
) -> Reflection:
    """同指纹失败次数覆盖 LLM 裁决。"""
    count = int(fail_counts.get(fingerprint) or 0)
    prior_alt = prior_alternate_fps or set()
    decision = reflection.decision
    forced = False
    constraints = list(reflection.constraints)
    # alternate 后仍同指纹，或同指纹 ≥3 → 强制 abort
    if fingerprint and (count >= 3 or fingerprint in prior_alt):
        decision = "abort"
        forced = True
        if fingerprint in prior_alt:
            constraints.append(
                f"已对指纹 {fingerprint} 做过 alternate，仍失败 → 强制止损"
            )
        else:
            constraints.append(f"同指纹 {fingerprint} 已失败 {count} 次，强制止损")
    elif count >= 2:
        if decision == "retry":
            decision = "alternate"
            forced = True
        constraints.append(f"同指纹 {fingerprint} 已失败 {count} 次，禁止原样重试")
    if decision in {"retry", "alternate"}:
        ban = f"禁止复用失败动作指纹 {fingerprint}"
        if ban not in constraints:
            constraints.append(ban)
    if decision == "retry":
        soft = "允许同工具族微调参数，但不得与上次完全同指纹"
        if soft not in constraints:
            constraints.append(soft)

    return Reflection(
        cause=reflection.cause,
        decision=decision,
        constraints=constraints,
        thought=reflection.thought or reflection.cause,
        fingerprint=fingerprint or reflection.fingerprint,
        forced=forced or reflection.forced,
        raw=dict(reflection.raw),
    )


def mock_reflect(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str = "",
) -> Reflection:
    """无 LLM：按错误文案启发式裁决。"""
    err = next((r.message for r in results if not r.ok), "action failed")
    low = err.lower()
    kind = (actions[0].kind if actions else "?") or "?"
    kind_l = kind.strip().lower()
    # 步骤运行时崩溃：默认可换道重规划（同指纹守卫仍会止损）
    if kind_l == "step_runtime" or "步骤运行时异常" in err:
        if any(
            x in low
            for x in (
                "permission",
                "not allowed",
                "lease",
                "denied",
                "forbidden",
                "unauthorized",
            )
        ):
            return Reflection(
                cause=f"步骤异常且像权限问题：{err}",
                decision="abort",
                constraints=["权限类步骤异常勿空转"],
                thought=f"止损：{err}",
                fingerprint=fingerprint,
            )
        return Reflection(
            cause=f"步骤运行时异常：{err}",
            decision="alternate",
            constraints=[
                f"不要重复触发相同崩溃指纹 {fingerprint}",
                "换工具/路径/观察方式后继续完成目标",
            ],
            thought=f"换方案继续：{err}",
            fingerprint=fingerprint,
        )
    if any(
        x in low
        for x in (
            "permission",
            "not allowed",
            "lease",
            "denied",
            "forbidden",
            "cannot claim",
            "unauthorized",
        )
    ):
        return Reflection(
            cause=f"权限或租约阻断：{err}",
            decision="abort",
            constraints=["缺权限/租约无解时勿反复重试同一写操作"],
            thought=f"止损：{err}",
            fingerprint=fingerprint,
        )
    if any(
        x in low
        for x in (
            "not found",
            "no such file",
            "does not exist",
            "missing",
            "command not found",
            "errno 2",
        )
    ):
        return Reflection(
            cause=f"目标或命令不存在：{err}",
            decision="alternate",
            constraints=[
                f"不要再执行相同的 {kind}（指纹 {fingerprint}）",
                "改用其它路径/工具或先定位资源",
            ],
            thought=f"换方案：{err}",
            fingerprint=fingerprint,
        )
    if "verify failed" in low:
        return Reflection(
            cause=f"回证未通过：{err}",
            decision="alternate",
            constraints=["回证失败说明动作未达预期，换验证方式或重写"],
            thought=f"换方案：{err}",
            fingerprint=fingerprint,
        )
    return Reflection(
        cause=f"动作失败：{err}",
        decision="retry",
        constraints=[f"可微调后重试，禁止原样复用指纹 {fingerprint}"],
        thought=f"可微调重试：{err}",
        fingerprint=fingerprint,
    )


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


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


def reflection_from_payload(data: dict[str, Any], *, fingerprint: str = "") -> Reflection:
    decision = str(data.get("decision") or "alternate").strip().lower()
    if decision not in _VALID_DECISIONS:
        decision = "alternate"
    constraints_raw = data.get("constraints")
    constraints: list[str] = []
    if isinstance(constraints_raw, list):
        constraints = [str(x) for x in constraints_raw if str(x).strip()]
    elif isinstance(constraints_raw, str) and constraints_raw.strip():
        constraints = [constraints_raw.strip()]
    cause = str(data.get("cause") or data.get("thought") or "动作失败")
    thought = str(data.get("thought") or cause)
    return Reflection(
        cause=cause,
        decision=decision,
        constraints=constraints,
        thought=thought,
        fingerprint=fingerprint,
        raw=data,
    )


def reflect_system_prompt() -> str:
    return """你是操作分身的执行反省模块。动作刚失败，请分析原因并裁决下一步策略。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{"cause":"一两句根因","decision":"retry|alternate|abort","constraints":["给下一轮规划的硬约束"],"thought":"简短说明"}
裁决含义：
- retry：瞬态或参数可微调，允许同工具族再试，但禁止与失败动作完全相同
- alternate：选型/命令族错误，必须换工具或换命令结构
- abort：缺权限、租约无解、超能力边界、或重复无效——应止损
禁止假装已成功；禁止建议原样重复同一命令。"""


def format_reflect_user_message(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str,
    fail_count: int,
    history_tail: str = "",
) -> str:
    lines = [
        f"目标：{goal}",
        f"失败动作指纹：{fingerprint}（本 run 同指纹已失败 {fail_count} 次，含本次）",
        "失败动作与结果：",
    ]
    for i, result in enumerate(results):
        action = actions[i] if i < len(actions) else None
        kind = action.kind if action else "?"
        args = action.args if action else {}
        lines.append(
            f"- {kind} args={json.dumps(args, ensure_ascii=False)[:400]} "
            f"→ {'ok' if result.ok else 'FAIL'}: {result.message}"
        )
    if history_tail:
        lines.append("")
        lines.append("近期上下文摘要：")
        lines.append(history_tail[:1500])
    return "\n".join(lines)


def reflection_needs_verify_read(reflection: Reflection) -> bool:
    """alternate/retry 且约束/原因要求核对原文 → 下一轮应临时放开定向只读。"""
    if reflection.decision not in {"alternate", "retry"}:
        return False
    blob = " ".join(
        [
            str(reflection.cause or ""),
            str(reflection.thought or ""),
            *[str(c or "") for c in (reflection.constraints or [])],
        ]
    )
    return any(hint in blob for hint in _VERIFY_READ_HINTS)


def failed_write_paths(actions: list[Action], results: list[ExecResult]) -> list[str]:
    """失败的写类动作 path（去重保序），供反省开读提示。"""
    out: list[str] = []
    write_kinds = {"fs_write", "fs_replace", "fs_patch"}
    for i, result in enumerate(results):
        if result.ok:
            continue
        action = actions[i] if i < len(actions) else None
        if action is None:
            continue
        kind = (action.kind or "").strip().lower()
        if kind not in write_kinds:
            continue
        path = str((action.args or {}).get("path") or "").strip()
        if path and path not in out:
            out.append(path)
    return out


def format_reflection_for_history(reflection: Reflection) -> str:
    constraints = "；".join(reflection.constraints) if reflection.constraints else "（无）"
    forced = "（指纹守卫覆盖）" if reflection.forced else ""
    return (
        "【执行反省】"
        f"{forced}\n"
        f"cause: {reflection.cause}\n"
        f"decision: {reflection.decision}\n"
        f"fingerprint: {reflection.fingerprint}\n"
        f"constraints: {constraints}\n"
        "下一轮规划必须遵守上述 constraints；"
        "若 decision=alternate 禁止复用该指纹对应的 kind+关键参数；"
        "若无法换道请 status=failed。"
        "注意：old not found / 参数类型错误 ≠ 根因终态；先查 hunks 类型、path 是否存在、"
        "mutate 工具面是否挡住补读，再决定是否 failed。"
    )


def _mock_enabled() -> bool:
    return os.environ.get("FAWAI_BRAIN_MOCK", "").strip() in ("1", "true", "yes")


def run_reflect(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str,
    fail_count: int,
    history: list[dict[str, Any]] | None = None,
    prior_alternate_fps: set[str] | None = None,
    llm: Any | None = None,
) -> Reflection:
    """执行反省入口（供 Brain.reflect / 闭环调用）。"""
    if _mock_enabled() or llm is None:
        base = mock_reflect(goal, actions, results, fingerprint=fingerprint)
    else:
        from fawai_avatar.llm.client import LlmClient

        client = llm if isinstance(llm, LlmClient) else LlmClient()
        tail = ""
        if history:
            bits = []
            for msg in history[-4:]:
                role = msg.get("role")
                content = str(msg.get("content") or "")[:400]
                bits.append(f"{role}: {content}")
            tail = "\n".join(bits)
        messages = [
            {"role": "system", "content": reflect_system_prompt()},
            {
                "role": "user",
                "content": format_reflect_user_message(
                    goal,
                    actions,
                    results,
                    fingerprint=fingerprint,
                    fail_count=fail_count,
                    history_tail=tail,
                ),
            },
        ]
        result = client.chat(messages, tools=None)
        if not result.get("ok"):
            base = mock_reflect(goal, actions, results, fingerprint=fingerprint)
            base.cause = f"反省 LLM 失败，回退启发式：{base.cause}"
            base.raw = {"llm_error": result.get("error")}
        else:
            content = str(result.get("content") or "").strip()
            payload = _parse_json_object(content)
            if payload is None:
                base = mock_reflect(goal, actions, results, fingerprint=fingerprint)
                base.cause = f"反省 JSON 解析失败，回退启发式：{base.cause}"
            else:
                base = reflection_from_payload(payload, fingerprint=fingerprint)
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
            if usage:
                raw = dict(base.raw or {})
                raw["usage"] = usage
                base.raw = raw

    counts = {fingerprint: fail_count}
    return apply_fingerprint_guard(
        base,
        fingerprint=fingerprint,
        fail_counts=counts,
        prior_alternate_fps=prior_alternate_fps,
    )


def format_stagnation_for_history(reflection: Reflection) -> str:
    constraints = "；".join(reflection.constraints) if reflection.constraints else "（无）"
    forced = "（步骤重复硬守卫）" if reflection.forced else ""
    return (
        "【步骤重复】"
        f"{forced}\n"
        f"cause: {reflection.cause}\n"
        f"decision: {reflection.decision}\n"
        f"step_fingerprint: {reflection.fingerprint}\n"
        f"constraints: {constraints}\n"
        "下一轮规划必须遵守上述 constraints；"
        "禁止再产出与该步骤指纹相同的动作串；"
        "须换工具、换关键参数或换观察方式；若无法换道请 status=failed。"
    )


def stagnation_from_payload(
    data: dict[str, Any], *, fingerprint: str = ""
) -> Reflection:
    decision = str(data.get("decision") or "alternate").strip().lower()
    if decision not in _STAGNATION_DECISIONS:
        decision = "alternate"
    constraints_raw = data.get("constraints")
    constraints: list[str] = []
    if isinstance(constraints_raw, list):
        constraints = [str(x) for x in constraints_raw if str(x).strip()]
    elif isinstance(constraints_raw, str) and constraints_raw.strip():
        constraints = [constraints_raw.strip()]
    cause = str(data.get("cause") or data.get("thought") or "步骤重复")
    thought = str(data.get("thought") or cause)
    return Reflection(
        cause=cause,
        decision=decision,
        constraints=constraints,
        thought=thought,
        fingerprint=fingerprint,
        raw=data,
    )


def mock_stagnation(
    goal: str,
    actions: list[Action],
    *,
    fingerprint: str = "",
    hit_count: int = 2,
) -> Reflection:
    """无 LLM：成功同构步默认换道。"""
    kinds = ",".join((a.kind or "?") for a in actions) or "?"
    explore_kinds = {
        "fs_read",
        "fs_grep",
        "fs_glob",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
    }
    only_explore = bool(actions) and all(
        (a.kind or "").strip().lower() in explore_kinds for a in actions
    )
    paths = []
    for a in actions:
        p = str((a.args or {}).get("path") or "").strip()
        if p and p not in paths:
            paths.append(p)
    constraints = [
        f"禁止复用步骤指纹 {fingerprint}",
        f"目标仍是：{goal[:200]}",
    ]
    if only_explore and paths:
        constraints.insert(
            1,
            "已定位 path（"
            + ", ".join(paths[:3])
            + "）时优先 fs_patch/fs_replace，禁止无新问题继续盲读；constraints 须 grounded 于已观察路径",
        )
    else:
        constraints.insert(1, "换工具、换关键参数，或改观察方式后再规划")
    return Reflection(
        cause=f"步骤指纹重复（第 {hit_count} 次）：{kinds}",
        decision="alternate",
        constraints=constraints,
        thought=f"同构步空转，换方案（指纹 {fingerprint}）",
        fingerprint=fingerprint,
    )


def stagnation_system_prompt() -> str:
    return """你是操作分身的步骤重复裁决模块。代码已判定本步动作串与此前成功步同构（空转风险）。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{"cause":"一两句为何算空转","decision":"continue|alternate|abort","constraints":["给下一轮规划的硬约束"],"thought":"简短说明"}
裁决含义：
- continue：确有必要再确认一次，但下一轮必须与当前步骤指纹有实质差异；禁止原样再跑
- alternate：必须换工具、换关键参数或换观察方式；若本步已是只读且 path 已知，优先改写而非换仓搜索
- abort：已无进展空间，应止损
禁止建议原样重复同一动作串。
constraints 必须 grounded：只引用历史/本步已出现的 path 与工具结果；禁止臆造未出现的目录或框架（如未观察到却写 Vue/React/src/components）。
禁止写出「禁止再读已定位到的入口文件」这类与证据矛盾的约束。
禁止写出「只能再读 / 继续读完整文件 / 再扩大 offset 扫读」却不要求随即改写的约束。
若已定位到明确改点（已知 path + 渲染/修改位置），alternate 的 constraints 必须含优先 fs_patch/fs_replace/fs_write；
「请先 fs_read」只允许写成「最多一次定向核对 exact old，随即 patch」——不是无限只读许可证。
alternate 裁决 ≠ 已进入改写相位：下一轮若仍只成功只读，仍属空转。
禁止「换仓瞎搜」或无新问题继续盲读同一文件。
改码探索注意：若近期工具结果只有「grep N」计数而看不到命中正文，应约束下一轮先拿到 path:line 正文（fs_grep 带 context，或按命中行号附近定向 fs_read）；
禁止把「按 grep 行号附近定向读取」写成盲目无效而一律禁止 fs_read。"""


def format_stagnation_user_message(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str,
    hit_count: int,
    history_tail: str = "",
) -> str:
    lines = [
        f"目标：{goal}",
        f"步骤指纹：{fingerprint}（本 run 已出现 {hit_count} 次，含本次）",
        "本步动作与结果（均成功但仍被判定重复）：",
    ]
    for i, result in enumerate(results):
        action = actions[i] if i < len(actions) else None
        kind = action.kind if action else "?"
        args = action.args if action else {}
        lines.append(
            f"- {kind} args={json.dumps(args, ensure_ascii=False)[:400]} "
            f"→ ok: {result.message}"
        )
    if history_tail:
        lines.append("")
        lines.append("近期上下文摘要：")
        lines.append(history_tail[:1500])
    return "\n".join(lines)


def run_stagnation(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str,
    hit_count: int,
    history: list[dict[str, Any]] | None = None,
    llm: Any | None = None,
) -> Reflection:
    """步骤重复命中后的 LLM/mock 裁决。"""
    if _mock_enabled() or llm is None:
        base = mock_stagnation(
            goal, actions, fingerprint=fingerprint, hit_count=hit_count
        )
    else:
        from fawai_avatar.llm.client import LlmClient

        client = llm if isinstance(llm, LlmClient) else LlmClient()
        tail = ""
        if history:
            bits = []
            for msg in history[-4:]:
                role = msg.get("role")
                content = str(msg.get("content") or "")[:400]
                bits.append(f"{role}: {content}")
            tail = "\n".join(bits)
        messages = [
            {"role": "system", "content": stagnation_system_prompt()},
            {
                "role": "user",
                "content": format_stagnation_user_message(
                    goal,
                    actions,
                    results,
                    fingerprint=fingerprint,
                    hit_count=hit_count,
                    history_tail=tail,
                ),
            },
        ]
        result = client.chat(messages, tools=None)
        if not result.get("ok"):
            base = mock_stagnation(
                goal, actions, fingerprint=fingerprint, hit_count=hit_count
            )
            base.cause = f"步骤重复 LLM 失败，回退启发式：{base.cause}"
            base.raw = {"llm_error": result.get("error")}
        else:
            content = str(result.get("content") or "").strip()
            payload = _parse_json_object(content)
            if payload is None:
                base = mock_stagnation(
                    goal, actions, fingerprint=fingerprint, hit_count=hit_count
                )
                base.cause = f"步骤重复 JSON 解析失败，回退启发式：{base.cause}"
            else:
                base = stagnation_from_payload(payload, fingerprint=fingerprint)
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
            if usage:
                raw = dict(base.raw or {})
                raw["usage"] = usage
                base.raw = raw

    ban = f"禁止复用步骤指纹 {fingerprint}"
    constraints = _ground_stagnation_constraints(
        list(base.constraints),
        actions=actions,
        decision=base.decision,
    )
    if ban not in constraints:
        constraints.append(ban)
    return Reflection(
        cause=base.cause,
        decision=base.decision,
        constraints=constraints,
        thought=base.thought or base.cause,
        fingerprint=fingerprint or base.fingerprint,
        forced=base.forced,
        raw=dict(base.raw),
    )


def _constraint_mentions_mutate(text: str) -> bool:
    return any(
        token in text
        for token in (
            "fs_patch",
            "fs_replace",
            "fs_write",
            "改写",
            "patch",
            "替换",
            "写入",
        )
    )


def _is_read_only_force_constraint(text: str) -> bool:
    """只逼再读、未要求随后改写 → 视为反进展约束。"""
    if _constraint_mentions_mutate(text):
        return False
    readish = any(token in text for token in ("读", "读取", "fs_read", "扫读", "offset"))
    if not readish:
        return False
    force_hints = (
        "只能再读",
        "只能读",
        "继续读",
        "继续读取",
        "再读",
        "再读完整",
        "读完整",
        "整文件",
        "扩大 offset",
        "扩大窗口",
        "先读",
        "必须先",
        "必须使用 fs_read",
        "必须先使用 fs_read",
        "请先 fs_read",
        "请先读取",
    )
    return any(h in text for h in force_hints)


def _rewrite_verify_then_mutate_constraint(text: str, *, paths: list[str]) -> str | None:
    """把「必须先读再改」收成「最多一次定向核对后立即 mutate」。"""
    if not _constraint_mentions_mutate(text):
        return None
    readish = any(token in text for token in ("fs_read", "读取", "先读", "核对"))
    if not readish:
        return None
    shown = ", ".join(paths[:3]) if paths else "已定位 path"
    return (
        f"已定位 path（{shown}）：最多一次定向 fs_read/fs_grep 核对 exact old，"
        "随即 fs_patch/fs_replace/fs_write；禁止仅再读不改写"
        "（请先 fs_read ≠ 无限只读许可证；alternate ≠ 已进入改写相位）"
    )


def _ground_stagnation_constraints(
    constraints: list[str],
    *,
    actions: list[Action],
    decision: str,
) -> list[str]:
    """去掉与已观察证据矛盾的约束；已定位 path 时补改写优先。"""
    paths: list[str] = []
    for a in actions:
        p = str((a.args or {}).get("path") or "").strip()
        if p and p not in paths:
            paths.append(p)
        for raw in (a.args or {}).get("paths") or []:
            s = str(raw or "").strip()
            if s and s not in paths:
                paths.append(s)
    explore_kinds = {
        "fs_read",
        "fs_grep",
        "fs_glob",
        "fs_read_many",
        "explore_search",
        "code_symbols",
        "code_map",
        "code_index_refresh",
    }
    only_explore = bool(actions) and all(
        (a.kind or "").strip().lower() in explore_kinds for a in actions
    )
    cleaned: list[str] = []
    saw_mutate_prefer = False
    for raw in constraints:
        c = str(raw or "").strip()
        if not c:
            continue
        # 禁止「不许读已定位文件」
        if paths and ("禁止" in c) and any(
            token in c for token in ("读", "读取", "fs_read")
        ):
            if any(p in c for p in paths) or "入口" in c or "index.html" in c:
                continue
        # 已定位后丢掉「只能再读 / 继续盲读」类
        if only_explore and paths and _is_read_only_force_constraint(c):
            continue
        rewritten = None
        if decision == "alternate" and only_explore and paths:
            rewritten = _rewrite_verify_then_mutate_constraint(c, paths=paths)
        if rewritten:
            if rewritten not in cleaned:
                cleaned.append(rewritten)
            saw_mutate_prefer = True
            continue
        # 禁止臆造未出现的前端框架目录
        hallucinated = (
            ("Vue" in c or "React" in c or ".vue" in c or ".jsx" in c or ".tsx" in c)
            and not any(
                tip in " ".join(paths)
                for tip in (".vue", ".jsx", ".tsx", "components/")
            )
        )
        if hallucinated and ("src/" in c or "组件" in c or "框架" in c):
            continue
        if "优先 fs_patch" in c or "优先 fs_replace" in c or "优先 fs_write" in c:
            saw_mutate_prefer = True
        cleaned.append(c)
    if decision == "alternate" and only_explore and paths and not saw_mutate_prefer:
        prefer = (
            "已定位 path（"
            + ", ".join(paths[:3])
            + "）时优先 fs_patch/fs_replace/fs_write，禁止无新问题继续盲读"
            "（alternate ≠ 已进入改写相位）"
        )
        cleaned.insert(0, prefer)
    return cleaned


def hard_abort_stagnation(fingerprint: str, *, hit_count: int) -> Reflection:
    """判决后仍同步骤指纹 → 代码强制止损（不再调 LLM）。"""
    return Reflection(
        cause=f"步骤指纹 {fingerprint} 判决后仍重复（第 {hit_count} 次）→ 强制止损",
        decision="abort",
        constraints=[f"禁止复用步骤指纹 {fingerprint}"],
        thought="同构成功步反复空转，止损",
        fingerprint=fingerprint,
        forced=True,
    )
