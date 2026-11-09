# term:编排脑  doc:doc/功能/复杂任务计划模式/说明.md
"""计划模式编排脑：进模判定、出图、追加对话对图/租约的影响分析。"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Literal

from fawai_avatar.brain.plan_graph import PlanGraph, PlanNode

AppendKind = Literal[
    "no_graph_change",
    "reorder",
    "lease_adjust",
    "dispatch_note",
    "new_task",
]

_EXPLICIT_MARKERS = (
    "先规划",
    "计划模式",
    "出个计划",
    "拆成几步",
    "拆成子任务",
    "分步完成",
    "分阶段",
)
_STAGE_RE = re.compile(r"然后|接着|之后|同时|分别|并且要|；")
_SPLIT_RE = re.compile(r"(?:然后|接着|之后|；)")


def plan_mode_enabled() -> bool:
    # 默认关：会话走单 Worker（Cursor 式同聊）；显式 auto/1/on 才自动进图
    raw = os.environ.get("FAWAI_PLAN_MODE", "0").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled", ""}


def coerce_plan_auto(value: Any) -> bool:
    """会话/任务级计划模式偏好；缺省无法识别 → False。"""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    s = str(value or "").strip().lower()
    if not s:
        return False
    if s in {"1", "true", "yes", "on", "auto", "plan_auto"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return False


def plan_auto_from_meta(meta: dict[str, Any] | None) -> bool | None:
    """任务 meta 是否显式带 plan_auto。未带 → None（回退环境变量）。"""
    if not isinstance(meta, dict) or "plan_auto" not in meta:
        return None
    return coerce_plan_auto(meta.get("plan_auto"))


def plan_auto_from_inbound_meta(meta: dict[str, Any] | None) -> bool | None:
    return plan_auto_from_meta(meta)


def resolve_plan_mode_allowed(meta: dict[str, Any] | None = None) -> bool:
    """会话 plan_auto 优先；未声明则看 FAWAI_PLAN_MODE。"""
    flag = plan_auto_from_meta(meta)
    if flag is not None:
        return bool(flag)
    return plan_mode_enabled()


def _mock_enabled() -> bool:
    return os.environ.get("FAWAI_BRAIN_MOCK", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


_EFFORT_MARKERS = (
    "仔细",
    "全力",
    "完整",
    "务必",
    "一定要",
    "别早交",
    "路径齐",
    "彻底",
    "认真",
    "确保完成",
    "做完再",
    "不要敷衍",
)
_QUERY_MARKERS = (
    "查一下",
    "看看",
    "看下",
    "是多少",
    "什么状态",
    "帮我看",
    "告诉我",
    "有没有",
    "是否在跑",
)
_WRITE_MARKERS = (
    "改",
    "修",
    "写",
    "替换",
    "部署",
    "重启",
    "提交",
    "实现",
    "添加",
    "删除",
    "上传",
    "安装",
    "配置",
)


def _coerce_bool_field(value: Any) -> bool | None:
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "y", "on"}:
            return True
        if s in {"0", "false", "no", "n", "off"}:
            return False
    return None


def analyze_task_exec_config(
    goal: str,
    *,
    on_llm_delta: Any | None = None,
) -> dict[str, Any]:
    """
    首轮任务分析：简单度 / 是否全力 / 是否进计划。

    返回 {"simple","want_effort","enter","reason","source"}。
    不看 plan_auto 闸——自动模式据此再回写四开关。
    """
    text = (goal or "").strip()
    if not text:
        return {
            "simple": True,
            "want_effort": False,
            "enter": False,
            "reason": "空目标",
            "source": "fallback",
        }

    if _mock_enabled():
        return _heuristic_exec_config(text) | {"source": "mock"}

    try:
        out = _llm_exec_config(text, on_llm_delta=on_llm_delta)
        if out is not None:
            return out
    except Exception:  # noqa: BLE001 — 判定失败回退启发式
        pass
    return _heuristic_exec_config(text) | {"source": "fallback"}


def decide_complexity(
    goal: str,
    *,
    on_llm_delta: Any | None = None,
    allow_plan: bool | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    编排脑判定是否进入计划模式（无人审）。

    返回 {"enter": bool, "reason": str, "source": ..., 可选 simple/want_effort}。
    ``allow_plan`` 显式时覆盖环境；否则看 ``meta.plan_auto``，再回退 FAWAI_PLAN_MODE。
    ``on_llm_delta`` 与规划同款：流式思考/输出，不改变判定结果。
    """
    if allow_plan is None:
        enabled = resolve_plan_mode_allowed(meta)
    else:
        enabled = bool(allow_plan)
    if not enabled:
        return {"enter": False, "reason": "计划模式关闭", "source": "off"}
    text = (goal or "").strip()
    if not text:
        return {"enter": False, "reason": "空目标", "source": "fallback"}

    analyzed = analyze_task_exec_config(goal, on_llm_delta=on_llm_delta)
    return {
        "enter": bool(analyzed.get("enter")),
        "reason": str(analyzed.get("reason") or ""),
        "source": str(analyzed.get("source") or "fallback"),
        "simple": bool(analyzed.get("simple")),
        "want_effort": bool(analyzed.get("want_effort")),
    }


def should_enter_plan_mode(goal: str) -> bool:
    """复杂度是否进计划模式；真源为编排脑 decide_complexity（无人确认）。"""
    return bool(decide_complexity(goal).get("enter"))


def _heuristic_exec_config(text: str) -> dict[str, Any]:
    force = os.environ.get("FAWAI_PLAN_MODE", "0").strip().lower() in {
        "1",
        "true",
        "on",
        "force",
        "always",
    }
    enter = False
    reason = "单阶段/简单目标"
    if any(m in text for m in _EXPLICIT_MARKERS):
        enter = True
        reason = "显式计划口令"
    else:
        stages = len(_STAGE_RE.findall(text))
        if force and stages >= 1:
            enter = True
            reason = "计划模式强制开启且含阶段连接"
        elif stages >= 2:
            enter = True
            reason = "多阶段连接词"
        elif stages >= 1 and len(text) >= 36:
            enter = True
            reason = "可拆阶段且目标较长"

    # 简单：不进计划、无多阶段，且像单次查询 / 极短只读；含改写动词则至少中档
    stages_n = len(_STAGE_RE.findall(text))
    has_write = any(m in text for m in _WRITE_MARKERS)
    has_query = any(m in text for m in _QUERY_MARKERS)
    simple = False
    if enter:
        simple = False
    elif stages_n == 0 and not has_write and (has_query or len(text) < 18):
        simple = True

    want_effort = False
    if any(m in text for m in _EFFORT_MARKERS):
        want_effort = True
    elif enter:
        want_effort = True
    elif (not simple) and (has_write or len(text) >= 24):
        want_effort = True

    if simple:
        want_effort = False
        enter = False

    return {
        "simple": bool(simple),
        "want_effort": bool(want_effort),
        "enter": bool(enter),
        "reason": reason,
    }


def _heuristic_complexity(text: str) -> dict[str, Any]:
    cfg = _heuristic_exec_config(text)
    return {"enter": bool(cfg["enter"]), "reason": str(cfg["reason"])}


def _llm_exec_config(
    goal: str, *, on_llm_delta: Any | None = None
) -> dict[str, Any] | None:
    from fawai_avatar.llm.client import chat

    system = (
        "你是操作分身的编排脑。对用户目标做一次执行配置分析。"
        "只输出一个 JSON 对象："
        '{"simple":true|false,"want_effort":true|false,"enter":true|false,"reason":"不超过40字"}。'
        "规则："
        "1) simple=true：单次查询答复、已定位窄改动、单文件短读、单一窄 GUI；此时 enter 必须 false，want_effort 通常 false；"
        "2) enter=true：多阶段交付、明确先后/并行、多种互斥资源、或显式要求先规划/拆任务图；"
        "3) want_effort=true：需要围绕目标谨慎做完（必要路径齐、能修则修、不早交）；"
        "若 enter=true 或目标要求完整交付/部署/替换服务，倾向 want_effort=true；"
        "4) 非 simple 的中等任务（改代码、跑一轮脚本）→ simple=false、enter 按是否需拆图；"
        "5) 不确定进模：若拆开后各段可独立验收则 enter=true，否则 false。"
        "不要等人确认；你直接判定。"
    )
    stream_kw: dict[str, Any] = {}
    if on_llm_delta is not None:
        stream_kw["thinking_role"] = "main"
        stream_kw["on_delta"] = on_llm_delta
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": goal[:2000]},
        ],
        tools=None,
        tool_choice=None,
        **stream_kw,
    )
    if not resp.get("ok"):
        return None
    parsed = _parse_json_object(str(resp.get("content") or ""))
    if not parsed:
        return None
    enter = _coerce_bool_field(parsed.get("enter"))
    simple = _coerce_bool_field(parsed.get("simple"))
    want_effort = _coerce_bool_field(parsed.get("want_effort"))
    if enter is None and simple is None:
        return None
    if enter is None:
        enter = False
    if simple is None:
        simple = not enter
    if want_effort is None:
        want_effort = bool(enter) or (not simple)
    if simple:
        enter = False
        want_effort = False
    if enter:
        simple = False
    reason = str(parsed.get("reason") or "").strip() or "编排脑判定"
    return {
        "simple": bool(simple),
        "want_effort": bool(want_effort),
        "enter": bool(enter),
        "reason": reason[:80],
        "source": "llm",
    }


def _llm_complexity(
    goal: str, *, on_llm_delta: Any | None = None
) -> dict[str, Any] | None:
    """兼容旧调用：转成仅 enter 视图。"""
    out = _llm_exec_config(goal, on_llm_delta=on_llm_delta)
    if out is None:
        return None
    return {
        "enter": bool(out.get("enter")),
        "reason": str(out.get("reason") or ""),
        "source": str(out.get("source") or "llm"),
        "simple": bool(out.get("simple")),
        "want_effort": bool(out.get("want_effort")),
    }


def build_plan_graph(
    goal: str,
    *,
    plan_id: str | None = None,
    on_llm_delta: Any | None = None,
) -> PlanGraph:
    """出图：mock/启发式默认；非 mock 尝试 LLM，失败回退启发式。"""
    pid = (plan_id or uuid.uuid4().hex).strip()
    text = (goal or "").strip()
    if _mock_enabled():
        return _heuristic_graph(text, plan_id=pid, source="mock")
    try:
        graph = _llm_graph(text, plan_id=pid, on_llm_delta=on_llm_delta)
        if graph is not None and graph.nodes:
            return graph
    except Exception:  # noqa: BLE001 — 出图失败用启发式
        pass
    return _heuristic_graph(text, plan_id=pid, source="heuristic")


def analyze_plan_append(
    *,
    root_goal: str,
    graph: PlanGraph,
    user_text: str,
    node_id: str | None = None,
) -> dict[str, Any]:
    """
    追加对话：编排脑先分析。

    返回 kind / instruction / reason / source；可选 patch_nodes（reorder 时）。
    """
    text = (user_text or "").strip()
    if not text:
        return {
            "kind": "no_graph_change",
            "instruction": "",
            "reason": "空追加",
            "source": "fallback",
        }
    if _mock_enabled():
        return _mock_append_analyze(text)
    try:
        out = _llm_append_analyze(
            root_goal=root_goal, graph=graph, user_text=text, node_id=node_id
        )
        if out is not None:
            return out
    except Exception:  # noqa: BLE001
        pass
    return _mock_append_analyze(text) | {"source": "fallback"}


def format_supervisor_goal(
    *,
    root_goal: str,
    node: PlanNode,
    artifacts: dict[str, Any],
    shared_facts: list[Any] | None = None,
) -> str:
    """监管脑看到的目标文案：节点 goal + 授权契约 + 上游产物。"""
    from fawai_avatar.brain.shared_facts import inject_for

    lines = [
        f"【监管脑授权】节点 `{node.id}`",
        f"【停条件】{node.stop_when}",
    ]
    if node.produces:
        lines.append(f"【须产出键】{', '.join(node.produces)}")
    if node.consumes:
        bits = []
        for c in node.consumes:
            pinned = inject_for(shared_facts, c)
            if pinned:
                bits.append(f"{c}={pinned}")
            else:
                bits.append(f"{c}={artifacts.get(c, '（缺失）')!r}"[:120])
        lines.append("【上游产物】" + "; ".join(bits))
        if shared_facts:
            lines.append(
                "上游产物里已经给出的共用事实不要再获取一次；"
                "其中没有的事实才按路径定向再读。口令不在产物里，从原配置读进环境。"
            )
    lines.append(f"【用户总目标】{(root_goal or '')[:500]}")
    lines.append(f"【本节点目标】{node.goal}")
    lines.append(
        "你是监管脑：在本节点授权内自行完成任务并驱动执行步骤；"
        "不要改其它节点或全局租约；完成后在结论中兑现产物键含义。"
        "【监管脑授权】/节点租约/产物键由运行时兑现，不是能力清单里的 action_kind；"
        "执行侧用 fs_write 等写出约定产物并 status=success 即可，"
        "禁止因清单无「授权/租约/产物键」词条而 failed。"
        "若本节点生产共用事实，结论里写清事实本身（如 host、端口、日志落点），不要只写已完成。"
    )
    return "\n".join(lines)


def _heuristic_graph(goal: str, *, plan_id: str, source: str) -> PlanGraph:
    text = goal.strip()
    # 去掉显式口令前缀噪声，便于拆句
    cleaned = text
    for m in _EXPLICIT_MARKERS:
        cleaned = cleaned.replace(m, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip() or text

    if "同时" in cleaned and "然后" not in cleaned:
        # 简单并行：按「同时/分别/，」拆
        parts = re.split(r"同时|分别|，|,", cleaned)
        parts = [p.strip(" 。；;") for p in parts if p.strip(" 。；;")]
        if len(parts) < 2:
            parts = [cleaned]
        nodes = []
        for i, part in enumerate(parts):
            nid = f"n{i+1}"
            nodes.append(
                PlanNode(
                    id=nid,
                    goal=part,
                    depends_on=[],
                    parallel_group="g1",
                    produces=[f"out_{nid}"],
                    stop_when=f"完成：{part[:40]}",
                )
            )
        return PlanGraph(
            plan_id=plan_id, root_goal=goal, nodes=nodes, source=source
        )

    parts = [p.strip(" 。；;") for p in _SPLIT_RE.split(cleaned) if p.strip(" 。；;")]
    if len(parts) < 2:
        # 单节点图（仍进计划模式时保底）
        nodes = [
            PlanNode(
                id="n1",
                goal=cleaned or goal,
                produces=["out_n1"],
                stop_when="完成用户目标",
            )
        ]
    else:
        nodes = []
        for i, part in enumerate(parts):
            nid = f"n{i+1}"
            deps = [f"n{i}"] if i > 0 else []
            consumes = [f"out_n{i}"] if i > 0 else []
            nodes.append(
                PlanNode(
                    id=nid,
                    goal=part,
                    depends_on=deps,
                    produces=[f"out_{nid}"],
                    consumes=consumes,
                    stop_when=f"完成：{part[:40]}",
                )
            )
    return PlanGraph(plan_id=plan_id, root_goal=goal, nodes=nodes, source=source)


def _llm_graph(
    goal: str, *, plan_id: str, on_llm_delta: Any | None = None
) -> PlanGraph | None:
    from fawai_avatar.llm.client import chat

    system = (
        "你是操作分身的编排脑。把用户复杂目标拆成任务图。"
        "只输出一个 JSON 对象，字段："
        '{"nodes":[{"id":"n1","goal":"...","depends_on":[],"parallel_group":null,'
        '"modality_hint":"background|foreground|mixed","produces":["out_n1"],'
        '"consumes":[],"stop_when":"..."}]}。'
        "规则：节点 goal 可独立验收；串行用 depends_on；可并行则 depends_on 空并可同 parallel_group；"
        "produces/consumes 用短键；不要 markdown。"
        "若多个节点会用到同一份事实（连接端点、训练任务、日志路径），再输出 "
        '"shared_facts":[{"key":"ssh_endpoint","producer":"n1","consumers":["n2","n3"],'
        '"what":"host 与 port，不含口令"}]。'
        "每个事实只有一个 producer；consumers 是会用到它的节点 id。"
        "不要把口令写成事实。"
    )
    stream_kw: dict[str, Any] = {}
    if on_llm_delta is not None:
        stream_kw["thinking_role"] = "main"
        stream_kw["on_delta"] = on_llm_delta
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": goal[:2000]},
        ],
        tools=None,
        tool_choice=None,
        **stream_kw,
    )
    if not resp.get("ok"):
        return None
    parsed = _parse_json_object(str(resp.get("content") or ""))
    if not parsed or not isinstance(parsed.get("nodes"), list):
        return None
    nodes: list[PlanNode] = []
    for i, raw in enumerate(parsed["nodes"]):
        if not isinstance(raw, dict):
            continue
        nid = str(raw.get("id") or f"n{i+1}").strip()
        g = str(raw.get("goal") or "").strip()
        if not g:
            continue
        produces = [str(x) for x in (raw.get("produces") or []) if str(x).strip()]
        if not produces:
            produces = [f"out_{nid}"]
        nodes.append(
            PlanNode(
                id=nid,
                goal=g,
                depends_on=[
                    str(x) for x in (raw.get("depends_on") or []) if str(x).strip()
                ],
                parallel_group=(
                    str(raw["parallel_group"]).strip()
                    if raw.get("parallel_group")
                    else None
                ),
                modality_hint=str(raw.get("modality_hint") or "background"),
                produces=produces,
                consumes=[
                    str(x) for x in (raw.get("consumes") or []) if str(x).strip()
                ],
                stop_when=str(raw.get("stop_when") or f"完成：{g[:40]}"),
            )
        )
    if not nodes:
        return None
    from fawai_avatar.brain.shared_facts import apply_shared_facts

    graph = PlanGraph(
        plan_id=plan_id, root_goal=goal, nodes=nodes, source="llm"
    )
    apply_shared_facts(graph, parsed.get("shared_facts"))
    return graph


def _llm_append_analyze(
    *,
    root_goal: str,
    graph: PlanGraph,
    user_text: str,
    node_id: str | None,
) -> dict[str, Any] | None:
    from fawai_avatar.llm.client import chat

    system = (
        "你是操作分身的编排脑（追加分析）。任务图已在执行，用户追加了一条消息。"
        "会话默认同线程续写，不要轻易 new_task。"
        "判断对图与租约的影响。只输出 JSON："
        '{"kind":"no_graph_change|reorder|lease_adjust|dispatch_note|new_task",'
        '"instruction":"给当前监管脑的短指令（≤80字）","reason":"≤40字"}。'
        "规则：仅补充当前节点说明、要结果/导出→no_graph_change 或 dispatch_note；"
        "要改未决顺序/增删节点→reorder；要改写资源/前台占用→lease_adjust；"
        "仅用户明示新开或与本图完全无关→new_task；不确定必须 no_graph_change。"
    )
    sketch = "\n".join(graph.summary_lines())[:800]
    user = (
        f"总目标:\n{root_goal[:800]}\n"
        f"当前节点: {node_id or '（父计划/未知）'}\n"
        f"任务图:\n{sketch}\n"
        f"用户追加:\n{user_text[:800]}\n"
    )
    resp = chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tools=None,
        tool_choice=None,
    )
    if not resp.get("ok"):
        return None
    parsed = _parse_json_object(str(resp.get("content") or ""))
    if not parsed:
        return None
    kind = str(parsed.get("kind") or "").strip()
    if kind not in {
        "no_graph_change",
        "reorder",
        "lease_adjust",
        "dispatch_note",
        "new_task",
    }:
        return None
    instruction = str(parsed.get("instruction") or "").strip() or user_text[:80]
    reason = str(parsed.get("reason") or "").strip() or "模型分析"
    return {
        "kind": kind,
        "instruction": instruction[:120],
        "reason": reason[:80],
        "source": "llm",
    }


def _mock_append_analyze(text: str) -> dict[str, Any]:
    if any(m in text for m in ("新任务", "另外一件", "另一件事", "别的事")):
        return {
            "kind": "new_task",
            "instruction": "",
            "reason": "话术明示另开任务",
            "source": "mock",
        }
    if any(m in text for m in ("重排", "换顺序", "先做另一个", "调整顺序")):
        return {
            "kind": "reorder",
            "instruction": text[:80],
            "reason": "用户要求重排",
            "source": "mock",
        }
    if any(m in text for m in ("租约", "别抢前台", "串行写", "资源冲突")):
        return {
            "kind": "lease_adjust",
            "instruction": text[:80],
            "reason": "用户提及租约/资源",
            "source": "mock",
        }
    return {
        "kind": "no_graph_change",
        "instruction": text[:80],
        "reason": "补充当前任务说明",
        "source": "mock",
    }


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
