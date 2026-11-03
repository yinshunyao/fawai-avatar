# term:答复覆盖闸门  doc:doc/功能/完成任务/说明.md
"""问询/状态/配置盘点类目标：success 的 thought 须直接回答用户，禁止过程元叙述与过薄终稿。"""

from __future__ import annotations

import os
import re

from fawai_avatar.brain.prompt_profile import looks_like_fs_web_goal
from fawai_avatar.brain.write_effect import (
    goal_needs_visible_effect_gate,
    thought_is_placeholder,
)

ANSWER_COVERAGE_MARKER = "【答复覆盖】"

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


def answer_coverage_gate_enabled() -> bool:
    raw = (os.environ.get("FAWAI_ANSWER_COVERAGE_GATE") or "1").strip().lower()
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
    if _SUBSTANCE.search(t):
        return False
    if _META_CLOSING.search(t):
        return True
    hits = sum(1 for p in _META_PROCESS_PHRASES if p in t)
    return hits >= 1


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


def thought_ok_for_answer_coverage(thought: str, *, goal: str) -> bool:
    """问询类 success 的 thought 是否足以交给用户。"""
    t = (thought or "").strip()
    if thought_is_placeholder(t):
        return False
    if thought_is_meta_process_narration(t):
        return False
    if len(t) < 8:
        return False
    g = (goal or "").strip()
    if goal_is_inventory_report(g) and not thought_covers_inventory(t, goal=g):
        return False
    if goal_is_lookup(g) and not thought_covers_lookup_source(t):
        # 盘点已含 path 维则一并满足
        if "path" not in _inventory_dim_hits(t):
            return False
    return True


def format_answer_coverage_prompt(*, goal: str) -> str:
    g = (goal or "").strip()[:160] or "(目标)"
    parts = [
        f"{ANSWER_COVERAGE_MARKER}目标偏问询/状态/查找或配置盘点（{g}）。",
        "请给出面向用户的最终自然语言回答：直接写清关键事实"
        "（状态、数字、是否完成、边界、来源路径），",
        "禁止只写「已获取/已汇总/已找到并结束」这类过程元叙述。",
        "禁止粘贴原始 stdout 全文；禁止在 thought 里写出明文口令/密码。",
    ]
    if goal_is_inventory_report(goal) or goal_is_lookup(goal):
        parts.append(
            "若目标是查机器/硬件/配置或账号落点：先用一两句结论，再用分项列表或 Markdown 表"
            "写清证据里已有的字段（如 GPU、CPU、内存、磁盘、系统、配置文件路径）；"
            "证据里有的维度不要省略；材料被压缩时须先用 fs_read 读 observe 落盘全文再总结，"
            "禁止只根据截断预览报一两项交差。"
        )
    parts.extend(
        [
            "本步必须 status=success、done=true、actions 为空数组；",
            "thought 即为给用户看的最终回复。",
            "若材料仍不足以回答，改 status=failed 或 need_input，说明缺什么。",
        ]
    )
    return "".join(parts)
