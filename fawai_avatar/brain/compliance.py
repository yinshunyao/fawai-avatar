# term:守规约束  doc:doc/功能/守规约束/说明.md
# term:规则注册表  doc:doc/功能/守规约束/说明.md
# term:架构校验  doc:doc/功能/守规约束/说明.md
"""规则注册表 + 架构校验（计划后硬门）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fawai_avatar.types import Action, Plan

# 分层路径前缀 → 可接受的功能说明路径（命中任一即可）
LAYER_DOC_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "fawai_avatar/brain/",
        (
            "doc/功能/大脑/说明.md",
            "doc/功能/完成任务/说明.md",
            "doc/功能/守规约束/说明.md",
            "doc/功能/执行反省/说明.md",
            "doc/功能/步骤重复/说明.md",
            "doc/功能/编码闭环/说明.md",
        ),
    ),
    (
        "fawai_avatar/control/",
        (
            "doc/功能/控制/说明.md",
            "doc/功能/编码闭环/说明.md",
            "doc/功能/自主操控/说明.md",
        ),
    ),
    (
        "fawai_avatar/observe/",
        ("doc/功能/观察/说明.md", "doc/功能/自主操控/说明.md"),
    ),
    (
        "fawai_avatar/runtime/",
        (
            "doc/功能/任务与事件管理/说明.md",
            "doc/功能/进程中断续跑/说明.md",
            "doc/功能/服务保活/说明.md",
            "doc/功能/运行时版本/说明.md",
        ),
    ),
    (
        "fawai_avatar/workspace/",
        (
            "doc/功能/工作区文件/说明.md",
            "doc/功能/编码闭环/说明.md",
            "doc/功能/代码索引/说明.md",
            "doc/功能/版本管理/说明.md",
        ),
    ),
    (
        "fawai_avatar/skills/",
        ("doc/功能/自我进化/说明.md", "doc/功能/任务复盘/说明.md"),
    ),
    (
        "fawai_avatar/channel/",
        (
            "doc/功能/会话通道/说明.md",
            "doc/功能/会话网关/说明.md",
            "doc/功能/系统日志/说明.md",
            "doc/功能/云端中继/说明.md",
        ),
    ),
    (
        "fawai_avatar/task/",
        ("doc/功能/任务与事件管理/说明.md",),
    ),
    (
        "fawai_avatar/llm/",
        ("doc/功能/LLM接入/说明.md",),
    ),
)

_WRITE_KINDS = frozenset(
    {"fs_write", "fs_replace", "fs_patch", "git_commit"}
)

_EVOLUTION_HINTS = (
    "自我进化",
    "升版",
    "进化",
    "改产品",
    "skill_write",
    "同步文档",
)


@dataclass(frozen=True)
class RuleEntry:
    id: str
    when: tuple[str, ...]
    constraint: str
    hard: bool = False


# 首期代码常量；与能力清单同发
RULE_REGISTRY: tuple[RuleEntry, ...] = (
    RuleEntry(
        id="coding_fs_patch",
        when=("改代码", "修改代码", "fs_patch", "补丁", "重构", ".py", "修 bug", "修bug"),
        constraint="改已有代码优先 fs_patch；大文件禁止无 force_full_write 的整文件 fs_write。",
        hard=False,
    ),
    RuleEntry(
        id="coding_code_check",
        when=("改代码", "修改代码", "补丁", "重构", ".py", "code_check"),
        constraint="写后须 code_check（或 shell_verify）；未回证不得直接 success。",
        hard=False,
    ),
    RuleEntry(
        id="arch_doc_refs",
        when=(
            "改代码",
            "修改代码",
            "fawai_avatar",
            "自我进化",
            "升版",
            "重构",
            "brain/",
            "control/",
            "observe/",
        ),
        constraint=(
            "写入 fawai_avatar 分层目录时，计划 raw 须含 doc_refs"
            "（对应 doc/功能/*/说明.md）或非空 architecture_skip_reason。"
        ),
        hard=True,
    ),
    RuleEntry(
        id="evolution_forbid",
        when=("自我进化", "升版", "进化", "versioning"),
        constraint=(
            "智能体禁止改最少核："
            "fawai_avatar/versioning/、runtime/{releases,keeper,code_watch,reload_gate,port_guard}.py、"
            "workspace/paths.py、已存在的 doc/版本/v*/ 归档；升版只新建评测目录。"
            "Cursor/人工改盘不受此限。"
        ),
        hard=False,
    ),
    RuleEntry(
        id="file_no_gui",
        when=("写入", "工作区", "fs_write", "notes/", ".txt"),
        constraint="工作区文件目标必须用 fs_* / version_*，禁止 click/type/hotkey/ui_* 代替。",
        hard=False,
    ),
    RuleEntry(
        id="shovel_lifecycle",
        when=(
            "写脚本",
            "临时脚本",
            "生成脚本",
            "py_write",
            "py_run",
            "shovel",
            "可复用工具",
            "自动化脚本",
            "bash 脚本",
        ),
        constraint=(
            "造临时脚本前先 shovel_search；能调 fawai_shovels 则 shell 调 invoke；"
            "需求参数化/env，禁止明文口令与未公开绝对路径；"
            "通用性≥3 升公共铲（文首 meta+台账）或 skill_write；≤2 仅本案。"
        ),
        hard=False,
    ),
    RuleEntry(
        id="gui_ui_verify",
        when=(
            "GUI",
            "gui",
            "点击",
            "鼠标",
            "操作界面",
            "TextEdit",
            "文本编辑",
            "ui_click",
            "ui_type",
            "obs_ax",
            "控件",
        ),
        constraint=(
            "GUI 窄场景：优先 obs_ax + ui_click/ui_type(ref)；"
            "键入后须 ui_verify（或闭环 AX 回证）通过才可 success；"
            "禁止未回证假成功。"
        ),
        hard=False,
    ),
    RuleEntry(
        id="doc_sync",
        when=("改代码", "自我进化", "升版", "功能说明"),
        constraint="改实现时同步功能说明/术语表，或在 architecture_skip_reason 写明延期理由。",
        hard=False,
    ),
)


def looks_like_evolution_goal(goal: str) -> bool:
    g = goal or ""
    return any(h.lower() in g.lower() for h in _EVOLUTION_HINTS)


def match_rules(goal: str, *, limit: int = 5) -> list[RuleEntry]:
    g = goal or ""
    lowered = g.lower()
    hit: list[RuleEntry] = []
    for rule in RULE_REGISTRY:
        if any(w.lower() in lowered for w in rule.when):
            hit.append(rule)
        if len(hit) >= limit:
            break
    return hit


def rules_prompt_block(goal: str, *, limit: int = 5) -> str:
    rules = match_rules(goal, limit=limit)
    if not rules:
        return ""
    lines = ["守规约束（按目标命中的规则；hard 条目计划后硬校验）："]
    for r in rules:
        tag = "hard" if r.hard else "soft"
        lines.append(f"- [{tag}] {r.id}：{r.constraint}")
    lines.append(
        "分层写路径时请在 JSON 中带 doc_refs:[\"doc/功能/…/说明.md\"] "
        "或 architecture_skip_reason:\"…\"。"
    )
    return "\n".join(lines)


def _norm_rel(path: str) -> str:
    """归一相对仓内路径；绝对路径若含 fawai_avatar/ 则截到该前缀。"""
    norm = (path or "").replace("\\", "/").lstrip("./").strip()
    marker = "fawai_avatar/"
    idx = norm.find(marker)
    if idx > 0:
        norm = norm[idx:]
    return norm


def layer_prefix_for(path: str) -> str | None:
    norm = _norm_rel(path)
    for prefix, _docs in LAYER_DOC_MAP:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return prefix
    return None


def architecture_paths_in(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if layer_prefix_for(p):
            out.append(_norm_rel(p))
    return out


def required_docs_for_paths(paths: list[str]) -> list[str]:
    docs: list[str] = []
    seen: set[str] = set()
    for p in paths:
        prefix = layer_prefix_for(p)
        if not prefix:
            continue
        for _pref, allowed in LAYER_DOC_MAP:
            if _pref != prefix:
                continue
            for d in allowed:
                if d not in seen:
                    seen.add(d)
                    docs.append(d)
            break
    return docs


def write_paths_from_plan(plan: Plan) -> list[str]:
    paths: list[str] = []
    for action in plan.actions:
        kind = (action.kind or "").strip().lower()
        if kind not in _WRITE_KINDS:
            continue
        args = action.args or {}
        path = str(args.get("path") or "").strip()
        if path:
            paths.append(path)
        for p in args.get("paths") or []:
            s = str(p).strip()
            if s:
                paths.append(s)
        for p in action.write_resources or []:
            s = str(p).strip()
            if s:
                paths.append(s)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        n = _norm_rel(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_doc_refs(plan: Plan) -> list[str]:
    raw = plan.raw or {}
    refs = raw.get("doc_refs") or raw.get("architecture_docs") or []
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list):
        return []
    out: list[str] = []
    for item in refs:
        s = _norm_rel(str(item or ""))
        if s:
            out.append(s)
    return out


def extract_skip_reason(plan: Plan) -> str:
    raw = plan.raw or {}
    reason = raw.get("architecture_skip_reason") or raw.get("doc_skip_reason") or ""
    return str(reason or "").strip()


def plan_satisfies_architecture(plan: Plan, *, paths: list[str] | None = None) -> bool:
    touched = architecture_paths_in(paths if paths is not None else write_paths_from_plan(plan))
    if not touched:
        return True
    if len(extract_skip_reason(plan)) >= 4:
        return True
    refs = extract_doc_refs(plan)
    if not refs:
        return False
    allowed = set(required_docs_for_paths(touched))
    return any(r in allowed for r in refs)


def validate_architecture(goal: str, plan: Plan) -> str | None:
    """分层写路径须声明文档或跳过理由。"""
    del goal  # 路径触发即可；目标关键字仅用于提示注入
    paths = write_paths_from_plan(plan)
    touched = architecture_paths_in(paths)
    if not touched:
        return None
    if plan_satisfies_architecture(plan, paths=paths):
        return None
    need = required_docs_for_paths(touched)
    sample = "、".join(need[:3]) if need else "doc/功能/对应说明.md"
    return (
        "架构校验失败：写入分层路径 "
        + ", ".join(touched[:3])
        + f" 须在计划中提供 doc_refs（如 {sample}）"
        " 或非空 architecture_skip_reason"
    )


def validate_compliance(goal: str, plan: Plan) -> str | None:
    """守规硬门；返回违规原因或 None。"""
    return validate_architecture(goal, plan)


def annotate_plan_with_docs(plan: Plan, doc_refs: list[str]) -> Plan:
    """给 mock/测试用：写入 doc_refs。"""
    raw = dict(plan.raw or {})
    raw["doc_refs"] = list(doc_refs)
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        delegate=plan.delegate,
    )


def _coerce_doc_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[str] = []
    for item in items:
        s = _norm_rel(str(item or ""))
        if s:
            out.append(s)
    return out


def lift_architecture_decls_from_actions(plan: Plan) -> Plan:
    """把写动作 args 里的 doc_refs / architecture_skip_reason 提升到 plan.raw。"""
    refs = list(extract_doc_refs(plan))
    skip = extract_skip_reason(plan)
    before_refs = list(refs)
    before_skip = skip
    for action in plan.actions:
        args = action.args or {}
        for key in ("doc_refs", "architecture_docs"):
            if key not in args:
                continue
            for item in _coerce_doc_refs(args.get(key)):
                if item not in refs:
                    refs.append(item)
        for key in ("architecture_skip_reason", "doc_skip_reason"):
            if key not in args or skip:
                continue
            candidate = str(args.get(key) or "").strip()
            if len(candidate) >= 4:
                skip = candidate
    if refs == before_refs and skip == before_skip:
        return plan
    raw = dict(plan.raw or {})
    if refs:
        raw["doc_refs"] = refs
    if skip:
        raw["architecture_skip_reason"] = skip
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        delegate=plan.delegate,
    )


def ensure_architecture_decls(plan: Plan, *, auto_default: bool = False) -> Plan:
    """补齐架构声明：先提升动作参数；必要时按分层路径自动绑定默认 doc_refs。

    auto_default=True 用于原生 tool_calls / mock（原生通道无法附带 JSON 顶层字段）。
    JSON 计划默认 auto_default=False，漏声明仍走硬校验。
    """
    plan = lift_architecture_decls_from_actions(plan)
    if plan_satisfies_architecture(plan):
        return plan
    if not auto_default:
        return plan
    paths = write_paths_from_plan(plan)
    if not architecture_paths_in(paths):
        return plan
    if len(extract_skip_reason(plan)) >= 4:
        return plan
    refs = default_doc_refs_for_paths(paths)
    if not refs:
        return plan
    out = annotate_plan_with_docs(plan, refs)
    raw = dict(out.raw or {})
    raw["architecture_auto_bound"] = True
    return Plan(
        thought=out.thought,
        actions=list(out.actions),
        done=out.done,
        status=out.status,
        raw=raw,
        observe_kinds=list(out.observe_kinds),
        observe_args=dict(out.observe_args or {}),
        title=out.title or "",
        step_label=out.step_label or "",
        delegate=out.delegate,
    )


def apply_architecture_skip_fallback(plan: Plan, *, reason: str) -> Plan:
    """二次架构校验仍失败时的软兜底：写入 skip，避免整任务硬 failed。"""
    plan = ensure_architecture_decls(plan, auto_default=True)
    if plan_satisfies_architecture(plan):
        return plan
    if not architecture_paths_in(write_paths_from_plan(plan)):
        return plan
    raw = dict(plan.raw or {})
    skip = str(reason or "").strip()
    if len(skip) < 4:
        skip = "自动兜底：架构声明无法自动补齐，允许本步写入"
    raw["architecture_skip_reason"] = skip
    raw["architecture_soft_fallback"] = True
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        delegate=plan.delegate,
    )


def default_doc_refs_for_paths(paths: list[str]) -> list[str]:
    """每层取第一个推荐说明，供 mock / 原生工具自动补全。"""
    refs: list[str] = []
    seen_prefix: set[str] = set()
    for p in paths:
        prefix = layer_prefix_for(p)
        if not prefix or prefix in seen_prefix:
            continue
        seen_prefix.add(prefix)
        for pref, docs in LAYER_DOC_MAP:
            if pref == prefix and docs:
                refs.append(docs[0])
                break
    return refs
