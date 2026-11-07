# term:答复覆盖闸门  doc:doc/功能/完成任务/说明.md
"""问询/状态/配置盘点类目标：success 的 thought 须直接回答用户，禁止过程元叙述与过薄终稿。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fawai_avatar.brain.prompt_profile import looks_like_fs_web_goal, report_delivery_rule
from fawai_avatar.brain.write_effect import (
    goal_needs_visible_effect_gate,
    thought_is_placeholder,
)

ANSWER_COVERAGE_MARKER = "【答复覆盖】"
UNREAD_DUMP_MARKER = "【压缩未读】"

# 过程元叙述：描述「做了什么」而非「答了什么」
_META_PROCESS_PHRASES = (
    "已获取",
    "已汇总",
    "已查看",
    "已读取",
    "已收集",
    "已分析",
    "已检查",
    "已确认",
    "已连接",
    "已登录",
    "已找到",
    "已完成查询",
    "已拉取",
    "并结束",
    "到此结束",
    "任务结束",
    "可以结束",
    "查询完毕",
    "处理完毕",
)

_META_CLOSING = re.compile(
    r"已.{2,80}(并结束|到此结束|任务结束|可以结束|查询完毕|处理完毕)\s*[。.!！]?\s*$"
)

_META_WRAP_UP = (
    "汇总结果并完成",
    "汇总并完成",
    "获取并汇总",
    "并完成任务",
    "已成功获取",
)

# 可验收事实线索（通用；不绑具体业务）
_SUBSTANCE = re.compile(
    r"(?:"
    r"\d"
    r"|%"
    r"|运行中|未运行|空闲|忙碌|在线|离线"
    r"|失败|成功|完成|未开始|排队|中断|超时"
    r"|偏高|偏低|几乎|满载|正常|异常|不足|充足"
    r"|没有|尚未|已经是|约为|大约"
    r"|epoch|loss|error|oom|cuda|gpu|cpu|mem(?:ory)?"
    r")",
    re.I,
)

# 有过程套话时，仅有型号数字不算事实载荷
_HARD_FACT = re.compile(
    r"(?:运行中|未运行|空闲|忙碌|在线|离线|排队|中断|"
    r"epoch|loss|\d+\s*%|\d+\s*MiB|\d+\s*GiB|"
    r"显存占|利用率|自\s*\d|Nov\d|Oct\d)",
    re.I,
)

_ASK = re.compile(
    r"(如何|怎样|怎么样|多少|是否|有没有|吗|呢|进度|状态|结果如何|现在怎样|还好吗)"
)

# 查找落点 / 凭据：goal 常无「吗」但仍须答清路径
_LOOKUP = re.compile(
    r"(在哪里|在哪儿|哪儿|哪裡|找一下|找到|忘了|忘记|账号|口令|密码|地址|路径|磁盘里|文件在)"
)

# 多字段盘点：机器/硬件类须分项写清，禁止只报一两项（勿把「看业务配置文件」误判进来）
_INVENTORY = re.compile(
    r"("
    r"硬件(?:配置|规格|信息)?"
    r"|服务器(?:硬件|配置|规格|信息)"
    r"|机器(?:硬件|配置|规格|信息)"
    r"|主机(?:硬件|配置|规格)"
    r"|云服务器"
    r"|查看.{0,24}(硬件|gpu|cpu|内存|磁盘|显存)"
    r"|(gpu|cpu).{0,12}(内存|磁盘|配置)"
    r")",
    re.I,
)

_MUTATE_HINTS = (
    "改一下",
    "修改",
    "修复",
    "实现",
    "加上",
    "去掉",
    "写一个",
    "创建文件",
    "优化布局",
    "优化界面",
    "优化样式",
)

# 盘点维度（通用锚点；不绑单一厂商/机型）
_DIM_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gpu", re.compile(r"rtx|gtx|gpu|显[存卡]|nvidia|cuda|4090|a100|h100", re.I)),
    ("cpu", re.compile(r"\bcpu\b|xeon|epyc|ryzen|核(?:心)?|core\(s\)|intel|amd", re.I)),
    ("mem", re.compile(r"内存|memory|\bmem\b|\d+\s*gi(?:b)?|\d+\s*gb\s*(?:ram|内存)?", re.I)),
    (
        "disk",
        re.compile(
            r"磁盘|硬盘|系统盘|数据盘|\bdisk\b|\bssd\b|\bdf\b|/root/autodl|\d+\s*g(?:i)?b?\s*(?:盘|可用|已用)",
            re.I,
        ),
    ),
    ("os", re.compile(r"ubuntu|centos|debian|linux|系统|driver|驱动|cuda\s*\d", re.I)),
    (
        "path",
        re.compile(
            r"路径|文件|文档|\.md|配置信息|落点|账号在|见\s*`|/Users/|study/|重要信息",
            re.I,
        ),
    ),
)

_DIM_LABELS = {
    "gpu": "GPU/显存",
    "cpu": "CPU",
    "mem": "内存",
    "disk": "磁盘",
    "os": "系统/驱动",
    "path": "来源路径",
}

_REJECT_FAIL_MSG = {
    "placeholder": "最终回复为空或占位，拒绝假成功",
    "too_short": "最终回复过短，未覆盖目标问点，拒绝假成功",
    "meta_narration": "最终回复是过程元叙述，未直接回答目标问点，拒绝假成功",
    "inventory_thin": "最终回复配置/状态盘点过薄，未覆盖证据维度，拒绝假成功",
    "lookup_no_path": "最终回复未写明账号/配置来源路径，拒绝假成功",
    "unread_dump": "压缩观察全文未读就收尾，拒绝假成功",
}


def answer_coverage_gate_enabled() -> bool:
    # 默认关：启发式易误伤动作节点终稿；需要时设 FAWAI_ANSWER_COVERAGE_GATE=1
    raw = (os.environ.get("FAWAI_ANSWER_COVERAGE_GATE") or "0").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def goal_is_lookup(goal: str) -> bool:
    return bool(_LOOKUP.search((goal or "").strip()))


def goal_is_inventory_report(goal: str) -> bool:
    return bool(_INVENTORY.search((goal or "").strip()))


def goal_needs_answer_coverage_gate(goal: str) -> bool:
    """问询/状态/查找/配置盘点：收尾 thought 须含面向用户的可验收答复。"""
    if not answer_coverage_gate_enabled():
        return False
    text = (goal or "").strip()
    if not text:
        return False
    # 页面改动走可见副作用闸，不叠本闸
    if goal_needs_visible_effect_gate(text):
        return False
    if looks_like_fs_web_goal(text) and any(h in text for h in _MUTATE_HINTS):
        return False
    if any(h in text for h in _MUTATE_HINTS) and not _ASK.search(text):
        return False
    if "?" in text or "？" in text:
        return True
    if goal_is_lookup(text) or goal_is_inventory_report(text):
        return True
    return bool(_ASK.search(text))


def thought_is_meta_process_narration(thought: str) -> bool:
    """只陈述查询/汇总过程、无事实载荷时视为元叙述假收尾。"""
    t = (thought or "").strip()
    if not t:
        return True
    if _META_CLOSING.search(t):
        return True
    if any(p in t for p in _META_WRAP_UP):
        return True
    hits = sum(1 for p in _META_PROCESS_PHRASES if p in t)
    if hits >= 1:
        if not _SUBSTANCE.search(t):
            return True
        # 有过程套话但缺少硬事实（仅型号数字等）→ 仍算元叙述
        if not _HARD_FACT.search(t):
            return True
        return False
    return False


def _inventory_dim_hits(thought: str) -> set[str]:
    t = thought or ""
    return {name for name, pat in _DIM_BUCKETS if pat.search(t)}


def _thought_looks_structured(thought: str) -> bool:
    t = (thought or "").strip()
    if not t:
        return False
    if "|" in t and t.count("|") >= 4:
        return True
    if t.count("\n") >= 2:
        return True
    # 分号/顿号串起的多字段一句
    if t.count("；") + t.count(";") >= 2:
        return True
    if t.count("、") >= 3 and len(t) >= 40:
        return True
    return False


def thought_covers_inventory(thought: str, *, goal: str) -> bool:
    """配置/硬件盘点：终稿须覆盖足够多维度，或结构化写出多项事实。"""
    del goal
    hits = _inventory_dim_hits(thought)
    if len(hits) >= 3:
        return True
    if len(hits) >= 2 and _thought_looks_structured(thought):
        return True
    return False


def thought_covers_lookup_source(thought: str) -> bool:
    """查找落点类：须点明来源路径/文档，禁止只说「已找到账号」。"""
    return "path" in _inventory_dim_hits(thought)


def missing_inventory_dims(thought: str) -> list[str]:
    """返回尚未点到的盘点维度名（稳定短码）。"""
    hits = _inventory_dim_hits(thought)
    return [name for name, _ in _DIM_BUCKETS if name not in hits]


def answer_coverage_reject_reason(thought: str, *, goal: str) -> str | None:
    """None 表示可通过；否则返回稳定拒因短码。"""
    t = (thought or "").strip()
    if thought_is_placeholder(t):
        return "placeholder"
    if thought_is_meta_process_narration(t):
        return "meta_narration"
    if len(t) < 8:
        return "too_short"
    g = (goal or "").strip()
    if goal_is_inventory_report(g) and not thought_covers_inventory(t, goal=g):
        return "inventory_thin"
    if goal_is_lookup(g) and not thought_covers_lookup_source(t):
        if "path" not in _inventory_dim_hits(t):
            return "lookup_no_path"
    return None


def thought_ok_for_answer_coverage(thought: str, *, goal: str) -> bool:
    """问询类 success 的 thought 是否足以交给用户。"""
    return answer_coverage_reject_reason(thought, goal=goal) is None


def fail_message_for_answer_coverage(reason: str | None) -> str:
    """面向任务终态的拒因文案（细分，不再一律「过程元叙述」）。"""
    key = (reason or "").strip() or "meta_narration"
    return _REJECT_FAIL_MSG.get(key, _REJECT_FAIL_MSG["meta_narration"])


def format_unread_dump_prompt(*, paths: list[str]) -> str:
    """压缩观察全文尚未 fs_read 时，强制先读再总结。"""
    shown: list[str] = []
    for raw in paths:
        p = str(raw or "").strip()
        if p and p not in shown:
            shown.append(p)
        if len(shown) >= 3:
            break
    joined = "、".join(f"`{p}`" for p in shown) or "（observe 落盘）"
    return (
        f"{UNREAD_DUMP_MARKER}工具结果已压缩，全文落盘于 {joined}。"
        "本步必须先用 fs_read 读取上述 path（优先工作区内相对路径；"
        "绝对路径若在 work_root 内亦可），读完后再写面向用户的分项结论；"
        "禁止只凭截断预览 status=success；禁止用 shell cat 代替 fs_read。"
        "若读后材料仍不足，status=failed 或 need_input 说明缺什么。"
    )


def format_answer_coverage_prompt(
    *,
    goal: str,
    thought: str = "",
    reason: str | None = None,
    unread_dumps: list[str] | None = None,
) -> str:
    g = (goal or "").strip()[:160] or "(目标)"
    why = (reason or "").strip() or answer_coverage_reject_reason(thought, goal=goal)
    parts = [
        f"{ANSWER_COVERAGE_MARKER}目标偏问询/状态/查找或配置盘点（{g}）。",
    ]
    if why == "meta_narration":
        parts.append("上一轮 thought 是过程元叙述，未直接回答问点。")
    elif why == "inventory_thin":
        missing = missing_inventory_dims(thought)
        labels = "、".join(_DIM_LABELS.get(m, m) for m in missing[:6]) or "多维字段"
        parts.append(f"上一轮盘点过薄；请补齐证据里已有、但未写到的维度（缺：{labels}）。")
    elif why == "lookup_no_path":
        parts.append("上一轮未写明账号/配置来源路径；须点明文档或文件落点。")
    elif why == "too_short" or why == "placeholder":
        parts.append("上一轮终稿为空、占位或过短。")
    parts.extend(
        [
            "请给出面向用户的最终自然语言回答：直接写清关键事实"
            "（状态、数字、是否完成、边界、来源路径），",
            "禁止只写「已获取/已汇总/已找到并结束」这类过程元叙述。",
            "禁止粘贴原始 stdout 全文；禁止在 thought 里写出明文口令/密码。",
        ]
    )
    if goal_is_inventory_report(goal) or goal_is_lookup(goal):
        parts.append(
            "若目标是查机器/硬件/配置或账号落点：先用一两句结论，再用分项列表或 Markdown 表"
            "写清证据里已有的字段（如 GPU、CPU、内存、磁盘、系统、配置文件路径）；"
            "证据里有的维度不要省略；材料被压缩时须先用 fs_read 读 observe 落盘全文再总结，"
            "禁止只根据截断预览报一两项交差。"
        )
    if unread_dumps:
        parts.append(format_unread_dump_prompt(paths=list(unread_dumps)))
    parts.extend(
        [
            "本步必须 status=success、done=true、actions 为空数组；",
            "thought 即为给用户看的最终回复。",
            report_delivery_rule(goal).strip(),
            "若材料仍不足以回答，改 status=failed 或 need_input，说明缺什么。",
        ]
    )
    return "".join(parts)


def normalize_path_key(path: str) -> str:
    """路径比对键：解析失败则退回规范化斜杠串。"""
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return raw.replace("\\", "/").rstrip("/")


def prefer_fs_read_path(path: str, *, work_root: str | Path | None = None) -> str:
    """优先给出 work_root 相对路径，便于 fs_read 通过沙箱。"""
    raw = str(path or "").strip()
    if not raw or work_root is None:
        return raw
    try:
        root = Path(work_root).expanduser().resolve()
        abs_p = Path(raw).expanduser().resolve()
        return str(abs_p.relative_to(root))
    except (OSError, RuntimeError, ValueError):
        return raw


def path_was_read(path: str, read_ok_paths: list[str] | set[str]) -> bool:
    """判断 dump/文件是否已被成功 fs_read（绝对/相对等价）。"""
    key = normalize_path_key(path)
    if not key:
        return False
    keys = {normalize_path_key(p) for p in read_ok_paths if str(p).strip()}
    if key in keys:
        return True
    # observe 落盘：同 basename 且两侧都带 observe 也算读过
    name = Path(key).name
    if name.startswith("tool_") and "observe" in key.replace("\\", "/"):
        for other in keys:
            if Path(other).name == name and "observe" in other.replace("\\", "/"):
                return True
    return False


def unread_compressed_dumps(
    dump_paths: list[str],
    read_ok_paths: list[str] | set[str],
) -> list[str]:
    """返回尚未成功读取的压缩全文落盘路径（保序）。"""
    out: list[str] = []
    for raw in dump_paths:
        p = str(raw or "").strip()
        if not p or path_was_read(p, read_ok_paths):
            continue
        if p not in out:
            out.append(p)
    return out


def is_tool_observe_dump_path(path: str) -> bool:
    """是否为 obs_tool 压缩落盘（tool_*_hits/stdout 等）。"""
    key = normalize_path_key(path)
    if not key:
        return False
    name = Path(key).name
    if not name.startswith("tool_"):
        return False
    return "observe" in key.replace("\\", "/") or name.endswith(
        ("_hits.txt", "_stdout.txt", "_stderr.txt", "_content.txt", "_lines.txt")
    )


def is_hits_observe_dump_path(path: str) -> bool:
    """检索 hits 压缩落盘（误压时勿用未读闸绑死 mutate）。"""
    name = Path(normalize_path_key(path) or path).name
    return name.startswith("tool_") and name.endswith("_hits.txt")


def has_source_window_read(read_ok_paths: list[str] | set[str]) -> bool:
    """是否已成功 fs_read 过非 observe dump 的源码/材料 path（小窗取证）。"""
    for raw in read_ok_paths or []:
        p = str(raw or "").strip()
        if p and not is_tool_observe_dump_path(p):
            return True
    return False


def should_suppress_compressed_unread_ban(
    *,
    write_set: list[str] | None = None,
    read_ok_paths: list[str] | set[str] | None = None,
) -> bool:
    """改写已开始，或已对源码 path 小窗取证时：不再因未读 hits dump 注入禁宽搜。

    问询收尾的【压缩未读】答复闸仍可单独用 unread_compressed_dumps；本函数只管
    编码闭环里的【压缩未读·禁宽搜】误拦 mutate。
    """
    if any(str(p or "").strip() for p in (write_set or [])):
        return True
    return has_source_window_read(read_ok_paths or [])


def prune_hits_dumps_after_source_evidence(
    dump_paths: list[str],
    *,
    write_set: list[str] | None = None,
    read_ok_paths: list[str] | set[str] | None = None,
) -> list[str]:
    """源码小窗/mutate 后丢弃未读 hits dump，避免禁宽搜与答复闸被检索摘要拖死。"""
    if not should_suppress_compressed_unread_ban(
        write_set=write_set, read_ok_paths=read_ok_paths
    ):
        return list(dump_paths or [])
    return [p for p in (dump_paths or []) if not is_hits_observe_dump_path(str(p))]
