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

# 反省/步骤重复不再裁决 abort：止损由规划 status=failed|success 收口
_VALID_DECISIONS = frozenset({"retry", "alternate"})
_STAGNATION_DECISIONS = frozenset({"continue", "alternate"})
_LEGACY_ABORT = "abort"

_PLAN_FAIL_HINT = (
    "若确认无换道空间或权限/租约无解：下一轮规划须 status=failed（可附可复制命令要点），"
    "禁止再复用失败指纹空转；任务终态只允许 completed 或 failed，反省不再 abort"
)

SHELL_BYPASS_FS_REPLACEMENT = (
    "本地文件优先 fs_read（绝对路径须在 FAWAI_READ_ROOTS 内，默认 `/`）；"
    "禁止用 shell cat/cp/type/Get-Content 偷渡仓外文件；"
    "读 observe 落盘用相对 path；写仍限 work_root"
)

NOT_FOUND_FS_REPLACEMENT = (
    "not found（文件不存在）≠ 沙箱越界/禁读；禁止因此禁读绝对路径或只许 fs_glob。"
    "若报错已写「父目录存在/仅文件名缺失」：勿纠路径拼接，换其它落点或 fs_glob 看父目录实有文件，禁止同 path 空读。"
    "仅当「目录也不存在」或相对路径可疑时：再核对拼接 / fs_grep basename / hits 绝对 path；"
    "fs_glob 可传 path=只读根下目录（勿假定只会扫 work_root）"
)

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
    # fs_patch / fs_replace：正文必须进指纹，否则同 path 不同 hunk 撞同一指纹
    "old",
    "new",
    "hunks",
    # fs_read 行范围：正式名 + 常见别名，避免同 path 不同区间被当成同一步
    "offset",
    "limit",
    "start_line",
    "end_line",
    "line",
)

# 补丁正文用全文哈希进指纹（禁止截断撞车）；其它长文本仍可截断
_FINGERPRINT_HASH_KEYS = frozenset({"old", "new", "hunks"})

# 规划方声明本步故意同构（如 patch 后同窗验收）→ 闭环跳过步骤重复计数与裁决
# 不进指纹键：有/无标识的同读仍同指纹；带标识的步直接豁免
_STEP_REPEAT_SKIP_ARG_KEYS = (
    "allow_repeat",
    "skip_step_repeat",
    "ignore_step_repeat",
)
_STEP_REPEAT_SKIP_PLAN_KEYS = (
    "allow_repeat",
    "skip_step_repeat",
    "ignore_step_repeat",
)


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def action_skips_step_repeat(action: Action | None) -> bool:
    """单动作 args 是否声明跳过步骤重复。"""
    if action is None:
        return False
    args = action.args or {}
    for key in _STEP_REPEAT_SKIP_ARG_KEYS:
        if key in args and _truthy_flag(args.get(key)):
            return True
    return False


def plan_skips_step_repeat(
    actions: list[Action] | None,
    *,
    plan_raw: dict[str, Any] | None = None,
) -> bool:
    """本步是否跳过步骤重复守卫（规划顶层或任一动作声明即可）。

    典型：patch 后故意同窗 fs_read 验收 → args.allow_repeat=true。
    豁免步不累计指纹次数、不调 stagnation 裁决。
    """
    raw = plan_raw or {}
    for key in _STEP_REPEAT_SKIP_PLAN_KEYS:
        if key in raw and _truthy_flag(raw.get(key)):
            return True
    for action in actions or []:
        if action_skips_step_repeat(action):
            return True
    return False


@dataclass
class Reflection:
    """执行反省：失败后的结构化裁决。"""

    cause: str
    decision: str  # retry | alternate（legacy abort 会被收成 alternate）
    constraints: list[str] = field(default_factory=list)
    thought: str = ""
    fingerprint: str = ""
    forced: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coerce_reflection_no_abort(reflection: Reflection) -> Reflection:
    """把 legacy abort 收成 alternate，并提示下一轮规划用 status=failed 止损。"""
    decision = (reflection.decision or "").strip().lower()
    allowed = _VALID_DECISIONS | _STAGNATION_DECISIONS
    if decision == _LEGACY_ABORT:
        constraints = list(reflection.constraints)
        if not any("status=failed" in str(c) for c in constraints):
            constraints.append(_PLAN_FAIL_HINT)
        raw = dict(reflection.raw or {})
        raw["coerced_from_abort"] = True
        return Reflection(
            cause=reflection.cause,
            decision="alternate",
            constraints=constraints[:8],
            thought=(
                f"{reflection.thought or reflection.cause}；"
                "止损改由下一轮规划 status=failed（反省不再 abort）"
            ),
            fingerprint=reflection.fingerprint,
            forced=reflection.forced,
            raw=raw,
        )
    if decision not in allowed:
        return Reflection(
            cause=reflection.cause,
            decision="alternate",
            constraints=list(reflection.constraints),
            thought=reflection.thought or reflection.cause,
            fingerprint=reflection.fingerprint,
            forced=reflection.forced,
            raw=dict(reflection.raw or {}),
        )
    return reflection


def _fingerprint_arg_value(key: str, val: Any) -> str:
    """规范化单个指纹字段；补丁正文全文哈希，避免截断导致同 path 撞车。"""
    if isinstance(val, (list, dict)):
        rendered = json.dumps(val, ensure_ascii=False, sort_keys=True)
    else:
        rendered = str(val).strip()
    if key in _FINGERPRINT_HASH_KEYS:
        digest = hashlib.sha1(rendered.encode("utf-8")).hexdigest()[:16]
        return f"h:{digest}"
    if key in {"content", "text"} and len(rendered) > 120:
        rendered = rendered[:120]
    return rendered


def action_fingerprint(action: Action) -> str:
    """动作指纹：kind + 规范化关键 args（内容截断；补丁正文哈希）。"""
    parts: list[str] = [str(action.kind or "").strip().lower()]
    args = action.args or {}
    for key in _FINGERPRINT_KEYS:
        if key not in args:
            continue
        parts.append(f"{key}={_fingerprint_arg_value(key, args[key])}")
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
    "补丁锚点未接地",
    "原文小窗",
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
    """同指纹失败次数覆盖 LLM 裁决（只升到 alternate，不 abort）。"""
    base = coerce_reflection_no_abort(reflection)
    count = int(fail_counts.get(fingerprint) or 0)
    prior_alt = prior_alternate_fps or set()
    decision = base.decision
    forced = False
    constraints = list(base.constraints)
    # alternate 后仍同指纹，或同指纹 ≥3 → 强制换道并提示规划 failed（不由反省 abort）
    if fingerprint and (count >= 3 or fingerprint in prior_alt):
        decision = "alternate"
        forced = True
        if fingerprint in prior_alt:
            constraints.append(
                f"已对指纹 {fingerprint} 做过 alternate，仍失败 → 必须换方法族，"
                "或下一轮规划 status=failed 说明阻塞"
            )
        else:
            constraints.append(
                f"同指纹 {fingerprint} 已失败 {count} 次 → 禁止原样重试；"
                "换道或下一轮 status=failed"
            )
        if not any("status=failed" in str(c) for c in constraints):
            constraints.append(_PLAN_FAIL_HINT)
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
        cause=base.cause,
        decision=decision,
        constraints=constraints[:8],
        thought=base.thought or base.cause,
        fingerprint=fingerprint or base.fingerprint,
        forced=forced or base.forced,
        raw=dict(base.raw),
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
                decision="alternate",
                constraints=["权限类步骤异常勿空转", _PLAN_FAIL_HINT],
                thought=f"换道或由规划 failed：{err}",
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
    # SSH 口令/公钥认证失败：换方法族，勿当成租约/GUI 权限 abort
    from fawai_avatar.brain.method_recovery import (
        CONSTRAINT_SSH_AUTH,
        is_ssh_auth_failure,
    )

    if is_ssh_auth_failure(actions, results):
        return Reflection(
            cause=f"SSH 认证失败，须换带密部署方法：{err}",
            decision="alternate",
            constraints=[
                f"不要再执行相同的 {kind}（指纹 {fingerprint}）",
                CONSTRAINT_SSH_AUTH,
            ],
            thought="改用 ssh-run-with-pass / rsync-ssh-deploy，禁止 BatchMode 空探针",
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
            decision="alternate",
            constraints=[
                "缺权限/租约无解时勿反复重试同一写操作",
                _PLAN_FAIL_HINT,
            ],
            thought=f"换道或由规划 failed：{err}",
            fingerprint=fingerprint,
        )
    if "escapes work root" in low or "path escapes" in low or "read roots" in low:
        return Reflection(
            cause=f"路径越出工作区沙箱：{err}",
            decision="alternate",
            constraints=[
                f"不要再执行相同的 {kind}（指纹 {fingerprint}）",
                SHELL_BYPASS_FS_REPLACEMENT,
            ],
            thought="改用 FAWAI_READ_ROOTS 绝对路径 fs_read，禁止 shell 绕过",
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
        file_missing = (
            "file missing" in low
            or (kind or "").startswith("fs_")
            or "no such file" in low
        ) and "command not found" not in low
        return Reflection(
            cause=(
                f"目标文件不存在（非沙箱越界）：{err}"
                if file_missing
                else f"目标或命令不存在：{err}"
            ),
            decision="alternate",
            constraints=[
                f"不要再执行相同的 {kind}（指纹 {fingerprint}）",
                NOT_FOUND_FS_REPLACEMENT if file_missing else "改用其它路径/工具或先定位资源",
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
    constraints_raw = data.get("constraints")
    constraints: list[str] = []
    if isinstance(constraints_raw, list):
        constraints = [str(x) for x in constraints_raw if str(x).strip()]
    elif isinstance(constraints_raw, str) and constraints_raw.strip():
        constraints = [constraints_raw.strip()]
    cause = str(data.get("cause") or data.get("thought") or "动作失败")
    thought = str(data.get("thought") or cause)
    return coerce_reflection_no_abort(
        Reflection(
            cause=cause,
            decision=decision if decision in _VALID_DECISIONS | {_LEGACY_ABORT} else "alternate",
            constraints=constraints,
            thought=thought,
            fingerprint=fingerprint,
            raw=data,
        )
    )


def reflect_system_prompt() -> str:
    return """你是操作分身的执行反省模块。动作刚失败，请分析原因并裁决下一步策略。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{"cause":"一两句根因","decision":"retry|alternate","constraints":["给下一轮规划的硬约束"],"thought":"简短说明"}
裁决含义：
- retry：瞬态或参数可微调，允许同工具族再试，但禁止与失败动作完全相同
- alternate：选型/命令族错误，必须换工具或换命令结构；若确认无进展，constraints 写明下一轮规划须 status=failed
禁止输出 decision=abort（已废除）；任务终态只由规划 status=success|failed 决定。
禁止假装已成功；禁止建议原样重复同一命令。
constraints 必须 grounded：只依据失败结果与已观察路径。
若失败是 fs_read 路径越界：改用 FAWAI_READ_ROOTS 下绝对路径 fs_read，或 work_root 相对路径 / data/observe；
禁止写「用 shell cat/cp 绕过沙箱读绝对路径」。
若需用户本机执行才能继续：constraints 须含可复制命令要点，供规划 failed 终稿交付。
多必要路径目标：constraints 须兼顾台账——一条路径阻塞时不得要求只攻该条而丢掉已齐路径；
failed 交付须按路径分项（已齐/阻塞），声称交命令则须含可复制命令体。"""



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
    """【反省开读】硬闸已退役：恒为 False。

    旧逻辑用 cause/constraints 含 ``fs_read`` 等 hint 就注入禁定位闸，
    找配置/纠错路径任务极易误触；闭环不再 accordingly 注入。
    """
    _ = reflection
    _ = _VERIFY_READ_HINTS
    return False


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
        "遵守【必要路径台账】：勿偏科只攻一条必要路径而遗忘已齐的其他必要路径。"
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
    on_llm_delta: Any | None = None,
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
        # 执行反省：裁决质量优先，按主脑角色开思考（可预算）
        result = client.chat(
            messages,
            tools=None,
            thinking_role="main",
            on_delta=on_llm_delta,
        )
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
            raw = dict(base.raw or {})
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
            if usage:
                raw["usage"] = usage
            thinking = result.get("thinking")
            if isinstance(thinking, dict) and thinking:
                raw["thinking"] = thinking
            reasoning = result.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                raw["thinking_text"] = reasoning.strip()
            if usage or (isinstance(thinking, dict) and thinking) or raw.get(
                "thinking_text"
            ):
                base.raw = raw

    counts = {fingerprint: fail_count}
    guarded = apply_fingerprint_guard(
        base,
        fingerprint=fingerprint,
        fail_counts=counts,
        prior_alternate_fps=prior_alternate_fps,
    )
    grounded = ground_reflect_constraints(guarded, actions=actions, results=results)
    from fawai_avatar.brain.method_recovery import enrich_reflection_method_switch
    from fawai_avatar.brain.necessary_paths import enrich_reflection_path_ledger

    switched = enrich_reflection_method_switch(
        grounded, actions=actions, results=results, goal=goal
    )
    return enrich_reflection_path_ledger(switched)


def format_stagnation_for_history(reflection: Reflection) -> str:
    constraints = "；".join(reflection.constraints) if reflection.constraints else "（无）"
    return (
        "【步骤重复】\n"
        f"cause: {reflection.cause}\n"
        f"decision: {reflection.decision}\n"
        f"step_fingerprint: {reflection.fingerprint}\n"
        f"constraints: {constraints}\n"
        "下一轮规划必须遵守上述 constraints；"
        "禁止再产出与该步骤指纹相同的动作串；"
        "须换工具、换关键参数或换观察方式；"
        "若确认无法换道或无进展空间，由规划 status=failed 中止任务"
        "（步骤重复裁决不再 abort）。"
    )


def stagnation_from_payload(
    data: dict[str, Any], *, fingerprint: str = ""
) -> Reflection:
    decision = str(data.get("decision") or "alternate").strip().lower()
    constraints_raw = data.get("constraints")
    constraints: list[str] = []
    if isinstance(constraints_raw, list):
        constraints = [str(x) for x in constraints_raw if str(x).strip()]
    elif isinstance(constraints_raw, str) and constraints_raw.strip():
        constraints = [constraints_raw.strip()]
    cause = str(data.get("cause") or data.get("thought") or "步骤重复")
    thought = str(data.get("thought") or cause)
    return coerce_reflection_no_abort(
        Reflection(
            cause=cause,
            decision=(
                decision
                if decision in _STAGNATION_DECISIONS | {_LEGACY_ABORT}
                else "alternate"
            ),
            constraints=constraints,
            thought=thought,
            fingerprint=fingerprint,
            raw=data,
        )
    )


def mock_stagnation(
    goal: str,
    actions: list[Action],
    *,
    fingerprint: str = "",
    hit_count: int = 2,
    results: list[ExecResult] | None = None,
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
            _locate_alternate_constraint(paths, actions=actions, results=results),
        )
    else:
        constraints.insert(1, "换工具、换关键参数，或改观察方式后再规划")
    return Reflection(
        cause=f"步骤指纹重复（第 {hit_count} 次）：{kinds}",
        decision="alternate",
        constraints=constraints,
        thought=f"同构步空转，换指纹（指纹 {fingerprint}）",
        fingerprint=fingerprint,
    )


def stagnation_system_prompt() -> str:
    return """你是操作分身的步骤重复裁决模块。代码已判定本步动作串与此前成功步同构（空转风险）。
只输出一个 JSON 对象，不要 markdown 或其它前后缀。
字段：
{"cause":"一两句为何算空转","decision":"continue|alternate","constraints":["给下一轮规划的硬约束"],"thought":"简短说明"}
裁决含义：
- continue：确有必要再确认一次，但下一轮必须与当前步骤指纹有实质差异；禁止原样再跑
- alternate：必须换指纹——换工具、换关键参数（path/pattern/offset）或换观察方式；禁止原样同构；若确认无进展，constraints 写明下一轮规划须 status=failed
禁止输出 decision=abort（已废除）；止损只由规划 status=failed 决定。
禁止建议原样重复同一动作串。
若提示写明「此前已对本指纹做过步骤重复裁决」：说明规划在裁决后仍产出同构步——须更强 alternate，或确认无路可走时约束下一轮 status=failed；禁止假装未见过裁决。
constraints 必须 grounded：只引用历史/本步已出现的 path 与工具结果；禁止臆造未出现的目录或框架（如未观察到却写 Vue/React/src/components）。
禁止写出「禁止再读已定位到的入口文件」这类与证据矛盾的约束。
禁止写出「只能再读 / 继续读完整文件 / 再扩大 offset 扫读」却不要求随后落地（patch 或 need_input/failed）的约束。
若已定位 path：constraints 写成「换指纹：命中行附近小窗核对并逐字抄 exact old → 立即 fs_patch/fs_replace，或换 pattern/工具」；
若本步结果只有「grep N」计数而无命中正文：先要求带 context 的 grep 或小窗 fs_read 拿到可复制正文，禁止直接逼写。
禁止写出「必须进入改写相位 / 强制改写 / 强制写状态 / 切换为写工具面」——alternate 只换指纹，不是 mutate_prefer 开关；
「请先 fs_read」只允许写成「最多一次定向核对 exact old，随即 patch」——不是无限只读许可证，也不是禁读逼写。
禁止「换仓瞎搜」或无新问题继续盲读同一文件。
禁止把「按 grep 行号附近定向读取」写成盲目无效而一律禁止 fs_read。"""


def format_stagnation_user_message(
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
    *,
    fingerprint: str,
    hit_count: int,
    history_tail: str = "",
    prior_judged: bool = False,
) -> str:
    lines = [
        f"目标：{goal}",
        f"步骤指纹：{fingerprint}（本 run 已出现 {hit_count} 次，含本次）",
    ]
    if prior_judged:
        lines.append(
            "此前已对本指纹做过步骤重复裁决，规划仍产出同构步；"
            "请加强换道约束，或确认无进展时 abort。"
        )
    lines.append("本步动作与结果（均成功但仍被判定重复）：")
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
    prior_judged: bool = False,
    locate_anchors: list[str] | None = None,
) -> Reflection:
    """步骤重复命中后的 LLM/mock 裁决（不含代码硬 abort）。"""
    if _mock_enabled() or llm is None:
        base = mock_stagnation(
            goal,
            actions,
            fingerprint=fingerprint,
            hit_count=hit_count,
            results=results,
        )
        if prior_judged:
            base.cause = f"判决后仍重复（第 {hit_count} 次）：{base.cause}"
            thought = (base.thought or "").strip()
            base.thought = (
                f"此前已裁决仍同指纹，须换道或由规划中止。{thought}".strip()
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
                    prior_judged=prior_judged,
                ),
            },
        ]
        result = client.chat(messages, tools=None, thinking_role="main")
        if not result.get("ok"):
            base = mock_stagnation(
                goal,
                actions,
                fingerprint=fingerprint,
                hit_count=hit_count,
                results=results,
            )
            base.cause = f"步骤重复 LLM 失败，回退启发式：{base.cause}"
            base.raw = {"llm_error": result.get("error")}
        else:
            content = str(result.get("content") or "").strip()
            payload = _parse_json_object(content)
            if payload is None:
                base = mock_stagnation(
                    goal,
                    actions,
                    fingerprint=fingerprint,
                    hit_count=hit_count,
                    results=results,
                )
                base.cause = f"步骤重复 JSON 解析失败，回退启发式：{base.cause}"
            else:
                base = stagnation_from_payload(payload, fingerprint=fingerprint)
            raw = dict(base.raw or {})
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
            if usage:
                raw["usage"] = usage
            thinking = result.get("thinking")
            if isinstance(thinking, dict) and thinking:
                raw["thinking"] = thinking
            if usage or (isinstance(thinking, dict) and thinking):
                base.raw = raw

    ban = f"禁止复用步骤指纹 {fingerprint}"
    constraints = _ground_stagnation_constraints(
        list(base.constraints),
        actions=actions,
        decision=base.decision,
        locate_anchors=locate_anchors,
        results=results,
    )
    if ban not in constraints:
        constraints.append(ban)
    thought = _scrub_force_mutate_phase_text(base.thought or base.cause)
    cause = _scrub_force_mutate_phase_text(base.cause)
    grounded = Reflection(
        cause=cause,
        decision=base.decision,
        constraints=constraints,
        thought=thought,
        fingerprint=fingerprint or base.fingerprint,
        forced=base.forced,
        raw=dict(base.raw),
    )
    from fawai_avatar.brain.necessary_paths import enrich_reflection_path_ledger

    return enrich_reflection_path_ledger(grounded)


def is_shell_bypass_fs_constraint(text: str) -> bool:
    """是否在教「用 shell 读本地文件」绕过 fs 沙箱。"""
    c = str(text or "").strip()
    if not c:
        return False
    # 正道替换文案本身含「禁止用 shell cat」，勿误判为教唆
    if c == SHELL_BYPASS_FS_REPLACEMENT or c.startswith("fs_read 越界时"):
        return False
    if c == NOT_FOUND_FS_REPLACEMENT or c.startswith("not found（文件不存在）"):
        return False
    low = c.lower()
    if "禁止用 shell" in low or "禁止 shell cat" in low:
        return False
    teaches_shell = any(
        tok in low
        for tok in (
            "改用 shell",
            "使用 shell",
            "用 shell 工具",
            "shell 工具执行",
            "执行 cat",
            "用 cat",
            "改用 cat",
            "get-content",
        )
    )
    if not teaches_shell:
        return False
    sandboxish = any(
        tok in c
        for tok in (
            "fs_read",
            "绝对路径",
            "越权",
            "沙箱",
            "work root",
            "work_root",
            "越界",
            "绕过",
        )
    )
    readish = any(tok in c for tok in ("读", "读取", "cat", "查看文件", "打开文件"))
    return bool(sandboxish or readish)


def is_ban_absolute_fs_read_constraint(text: str) -> bool:
    """是否在禁读绝对路径 / FAWAI_READ_ROOTS 下的 fs_read（反证据，须丢掉）。"""
    c = str(text or "").strip()
    if not c or "禁止" not in c:
        return False
    if c == SHELL_BYPASS_FS_REPLACEMENT or c == NOT_FOUND_FS_REPLACEMENT:
        return False
    readish = any(tok in c for tok in ("fs_read", "读取", "再读", "直接读"))
    if not readish:
        return False
    return any(
        tok in c
        for tok in (
            "绝对路径",
            "/Users/",
            "/home/",
            "FAWAI_READ_ROOTS",
            "再次直接 fs_read",
            "禁止再次直接",
            "禁止再次 fs_read",
            "禁止再 fs_read",
        )
    )


def is_force_glob_only_constraint(text: str) -> bool:
    """是否把恢复收成「必须/只能 fs_glob」（在 not found 后常堵死纠路径）。"""
    c = str(text or "").strip()
    if not c or "fs_glob" not in c:
        return False
    if c == NOT_FOUND_FS_REPLACEMENT:
        return False
    return any(
        tok in c
        for tok in (
            "必须使用 fs_glob",
            "必须用 fs_glob",
            "只能用 fs_glob",
            "必须 fs_glob",
            "只能 fs_glob",
            "须使用 fs_glob",
        )
    )


def ground_reflect_constraints(
    reflection: Reflection,
    *,
    actions: list[Action] | None = None,
    results: list[ExecResult] | None = None,
) -> Reflection:
    """清洗执行反省约束：禁止 shell 绕过；禁读绝对路径；not found 假归因。"""
    del actions  # 预留：可按失败 path 注入更具体相对路径提示
    cleaned: list[str] = []
    dropped_bypass = False
    dropped_ban_abs = False
    dropped_force_glob = False
    err_blob = " ".join(str(r.message or "") for r in (results or []) if not r.ok)
    err_low = err_blob.lower()
    escaped = "escapes work root" in err_low or "path escapes" in err_low
    file_missing = (
        ("file missing" in err_low or "not found" in err_low)
        and "command not found" not in err_low
        and not escaped
    )
    for raw in reflection.constraints or []:
        c = str(raw or "").strip()
        if not c:
            continue
        if is_shell_bypass_fs_constraint(c):
            dropped_bypass = True
            continue
        if is_ban_absolute_fs_read_constraint(c):
            dropped_ban_abs = True
            continue
        if file_missing and is_force_glob_only_constraint(c):
            dropped_force_glob = True
            continue
        if c not in cleaned:
            cleaned.append(c)
    if dropped_bypass or escaped:
        if SHELL_BYPASS_FS_REPLACEMENT not in cleaned:
            cleaned.insert(0, SHELL_BYPASS_FS_REPLACEMENT)
    if file_missing or dropped_ban_abs or dropped_force_glob:
        if NOT_FOUND_FS_REPLACEMENT not in cleaned:
            cleaned.insert(0, NOT_FOUND_FS_REPLACEMENT)
    cause = str(reflection.cause or "")
    if file_missing and any(tok in cause for tok in ("越界", "沙箱", "被屏蔽", "越权")):
        cause = f"目标路径不存在（文件缺失，非沙箱越界）：{err_blob[:240] or cause}"
    return Reflection(
        cause=cause or reflection.cause,
        decision=reflection.decision,
        constraints=cleaned[:8],
        thought=reflection.thought,
        fingerprint=reflection.fingerprint,
        forced=reflection.forced,
        raw={
            **dict(reflection.raw or {}),
            **({"shell_bypass_fs_stripped": True} if dropped_bypass else {}),
            **({"ban_absolute_fs_read_stripped": True} if dropped_ban_abs else {}),
            **({"force_glob_only_stripped": True} if dropped_force_glob else {}),
            **({"not_found_grounded": True} if file_missing else {}),
        },
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


_FORCE_MUTATE_PHASE_HINTS = (
    "进入改写相位",
    "必须进入改写",
    "强制改写",
    "强制切换",
    "强制转向",
    "强制写状态",
    "写工具面",
    "改写相位",
    "写相位",
    "mutate 相位",
    "mutate相位",
    "切换为写",
    "切换至改写",
    "转入代码改写阶段",
    "直接进入 patch 改写阶段",
)


def _is_force_mutate_phase_text(text: str) -> bool:
    c = text or ""
    return any(h in c for h in _FORCE_MUTATE_PHASE_HINTS)


def _scrub_force_mutate_phase_text(text: str) -> str:
    """进度/cause/thought：去掉「强制进写相位」措辞，保留换指纹语义。"""
    raw = str(text or "").strip()
    if not raw or not _is_force_mutate_phase_text(raw):
        return raw
    return (
        "同构只读空转，须换指纹：小窗抄 exact old 后立即 patch，或换 pattern/工具"
        "（alternate ≠ 强制写状态）"
    )


def _results_look_thin_for_patch(
    actions: list[Action],
    results: list[ExecResult] | None,
) -> bool:
    """本步像「只有 grep 计数 / 无正文」→ 不应直接逼写。"""
    if not actions:
        return False
    kinds = [(a.kind or "").strip().lower() for a in actions]
    if any(k in {"fs_read", "fs_read_many"} for k in kinds):
        return False
    if not all(k in {"fs_grep", "explore_search", "code_symbols"} for k in kinds):
        return False
    if not results:
        return True
    for r in results:
        msg = str(getattr(r, "message", "") or "").strip().lower()
        # 典型：ok: grep 12 — 无 path:line 正文
        if "grep" in msg and ":" not in msg.replace("grep", "", 1):
            # message 仅 "grep N"
            rest = msg.replace("grep", "").strip()
            if rest.isdigit() or rest == "":
                continue
        blob = msg
        data = getattr(r, "data", None)
        if isinstance(data, dict):
            blob += " " + json.dumps(data, ensure_ascii=False)[:1200].lower()
        # 有 path:line 或多行源码痕迹则不算 thin
        if re.search(r"\.\w+:\d+", blob) or "hits" in blob or "\n" in str(
            getattr(r, "message", "") or ""
        ):
            return False
    return True


def _locate_alternate_constraint(
    paths: list[str],
    *,
    actions: list[Action] | None = None,
    results: list[ExecResult] | None = None,
) -> str:
    """已定位 path 时的换指纹约束：不把 alternate 收成强制写相位。"""
    shown = ", ".join(paths[:3]) if paths else "已定位 path"
    if _results_look_thin_for_patch(actions or [], results):
        return (
            f"已定位 path（{shown}）：换指纹——用带 context 的 fs_grep 或命中行附近"
            "小窗 fs_read 拿到可复制正文；禁止同构再 grep；拿到 exact old 后立即 "
            "fs_patch/fs_replace。alternate ≠ 强制写状态/改写相位开关"
        )
    return (
        f"已定位 path（{shown}）：换指纹——对命中行附近小窗核对并逐字抄 exact old，"
        "随即 fs_patch/fs_replace，或换 pattern/工具；禁止同构只读空转。"
        "alternate ≠ 强制写状态/改写相位开关"
    )


def _rewrite_verify_then_mutate_constraint(
    text: str,
    *,
    paths: list[str],
    actions: list[Action] | None = None,
    results: list[ExecResult] | None = None,
) -> str | None:
    """把「必须先读再改 / 强制进写相位」收成换指纹 + exact old → patch。"""
    force_phase = _is_force_mutate_phase_text(text)
    mentions_mutate = _constraint_mentions_mutate(text)
    readish = any(token in text for token in ("fs_read", "读取", "先读", "核对"))
    if not force_phase and not (mentions_mutate and readish):
        # 纯「优先 fs_patch / 必须改写」且无核对 → 也收成换指纹公式
        if mentions_mutate and any(
            tok in text for tok in ("优先", "必须", "直接修改", "立即改写")
        ):
            return _locate_alternate_constraint(
                paths, actions=actions, results=results
            )
        return None
    return _locate_alternate_constraint(paths, actions=actions, results=results)


def _anchor_paths_from_locate(anchors: list[str] | None) -> list[str]:
    """locate_anchors（path 或 path:line）→ 去重 path 列表。"""
    out: list[str] = []
    for raw in anchors or []:
        s = str(raw or "").strip()
        if not s:
            continue
        path = s
        if ":" in s:
            left, right = s.rsplit(":", 1)
            if right.isdigit():
                path = left
        if path and path not in out:
            out.append(path)
    return out


def _is_wide_search_force_constraint(text: str) -> bool:
    """已有锚点时丢掉「必须再宽搜 / 继续盲 grep」类互斥约束。"""
    c = text or ""
    if _constraint_mentions_mutate(c) and any(
        tok in c for tok in ("核对", "exact", "定向", "小窗")
    ):
        return False
    hints = (
        "宽搜",
        "宽泛",
        "全局搜索",
        "再 grep",
        "继续 grep",
        "继续搜",
        "再搜",
        "必须使用 fs_grep",
        "必须再 fs_grep",
        "必须用 fs_grep",
        "必须 grep",
        "扩大检索",
        "换关键词再搜",
    )
    return any(h in c for h in hints)


def _has_fingerprint_progress_constraint(text: str) -> bool:
    """是否已给出换指纹 / exact-old→patch 类进展约束。"""
    c = text or ""
    if "换指纹" in c:
        return True
    if "exact old" in c and _constraint_mentions_mutate(c):
        return True
    if "可复制正文" in c:
        return True
    return False


def _ground_stagnation_constraints(
    constraints: list[str],
    *,
    actions: list[Action],
    decision: str,
    locate_anchors: list[str] | None = None,
    results: list[ExecResult] | None = None,
) -> list[str]:
    """去掉与已观察证据矛盾的约束；已定位 path 时补「换指纹」而非强制写相位。"""
    paths: list[str] = []
    for a in actions:
        p = str((a.args or {}).get("path") or "").strip()
        if p and p not in paths:
            paths.append(p)
        for raw in (a.args or {}).get("paths") or []:
            s = str(raw or "").strip()
            if s and s not in paths:
                paths.append(s)
    for p in _anchor_paths_from_locate(locate_anchors):
        if p not in paths:
            paths.append(p)
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
    saw_progress = False
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
        # 禁止「禁读绝对路径 / 只许 fs_glob」类反证据（与执行反省清洗对齐）
        if is_ban_absolute_fs_read_constraint(c):
            continue
        if is_force_glob_only_constraint(c):
            continue
        # 已定位后丢掉「只能再读 / 继续盲读」类
        if only_explore and paths and _is_read_only_force_constraint(c):
            continue
        # 已有锚点时丢掉与 mutate 互斥的「必须再宽搜」
        if paths and _is_wide_search_force_constraint(c):
            continue
        rewritten = None
        if decision == "alternate" and only_explore and paths:
            rewritten = _rewrite_verify_then_mutate_constraint(
                c, paths=paths, actions=actions, results=results
            )
        if rewritten is None and decision == "alternate" and _is_force_mutate_phase_text(c):
            if paths:
                rewritten = _locate_alternate_constraint(
                    paths, actions=actions, results=results
                )
            else:
                continue
        if rewritten:
            if rewritten not in cleaned:
                cleaned.append(rewritten)
            saw_progress = True
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
        if _has_fingerprint_progress_constraint(c):
            saw_progress = True
        cleaned.append(c)
    if decision == "alternate" and only_explore and paths and not saw_progress:
        cleaned.insert(
            0,
            _locate_alternate_constraint(
                paths, actions=actions, results=results
            ),
        )
    return cleaned


def abort_should_deliver_commands(reflection: Reflection | None) -> bool:
    """abort 约束若要求「输出命令/本地执行」→ 终稿须带上可交付要点。"""
    if reflection is None:
        return False
    blob = " ".join(
        [str(reflection.cause or ""), str(reflection.thought or ""), *(reflection.constraints or [])]
    )
    keys = ("输出", "命令", "本地", "终端", "供用户", "宿主机", "请在", "复制")
    return any(k in blob for k in keys)


def format_abort_delivery(reflection: Reflection, *, fallback: str = "") -> str:
    """把 abort 的 cause + 可执行约束拼成用户可见终稿。"""
    parts: list[str] = []
    cause = (reflection.cause or "").strip()
    if cause:
        parts.append(cause)
    thought = (reflection.thought or "").strip()
    if thought and thought not in parts:
        parts.append(thought)
    cmds = [c.strip() for c in (reflection.constraints or []) if str(c).strip()]
    if cmds:
        parts.append("建议下一步：")
        parts.extend(f"- {c}" for c in cmds[:6])
    if not parts and fallback:
        return fallback
    return "\n".join(parts)


