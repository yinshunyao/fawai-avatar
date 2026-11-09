# term:假归因超出能力  doc:doc/分析/假归因超出能力与节点失败不接管.md
"""failed 假归因「超出能力清单」回灌：文案/脚本属生成+fs_write，勿空口越界。"""

from __future__ import annotations

import re

from fawai_avatar.brain.capabilities import is_beyond_capability

FALSE_BEYOND_MARKER = "【假归因·超出能力】"

# 模型把「无创意 skill / 编排元概念不在清单」写成越界的常见套话（通用；不绑 PPT 专名）
_FALSE_BEYOND_CLAIM = re.compile(
    r"(?:"
    r"超出(?:了)?(?:当前)?(?:执行)?(?:大脑)?(?:的)?能力(?:清单)?(?:边界)?"
    r"|能力清单(?:边界|之外|外|不支持|无法|没有|仅含|只含)"
    r"|不在能力清单"
    r"|不属于.{0,12}能力清单"
    r"|清单无法覆盖"
    r"|清单外目标"
    r"|工具(?:清单|能力)边界"
    r"|不包含创意内容生成"
    r"|缺乏.{0,24}(?:创意|文案|内容生成|故事|脚本).{0,16}skill"
    r"|无.{0,12}(?:创意|文案).{0,12}(?:专用\s*)?skill"
    r"|专用\s*(?:Agent|skill).{0,24}(?:处理|生成|文案)"
    r"|纯创意文案"
    r"|开放式创意"
    r"|无法保证创意"
    r"|编排元概念"
    r"|元概念(?:交付|阻塞|超出)?"
    r"|元层面\s*failed"
    r"|监管脑授权"
    r"|节点租约"
    r"|产物键(?:绑定|交付)?"
    r")",
    re.I,
)

# 终稿已承认产物内容齐（可与「仍因元概念 failed」同场）
_ARTIFACT_DONE_CLAIM = re.compile(
    r"(?:"
    r"已完整落盘"
    r"|脚本(?:文件)?已(?:完整)?落盘"
    r"|产物已(?:齐|就绪|落盘|写入)"
    r"|已齐"
    r"|已包含"
    r"|停条件(?:已)?满足"
    r"|write_set"
    r")",
    re.I,
)


def thought_claims_false_beyond(thought: str) -> bool:
    """终稿是否在用「超出能力清单 / 无创意 skill / 编排元概念」类套话。"""
    t = (thought or "").strip()
    if not t:
        return False
    return bool(_FALSE_BEYOND_CLAIM.search(t))


def thought_admits_artifact_done(thought: str) -> bool:
    """终稿是否承认约定产物/脚本内容已齐。"""
    t = (thought or "").strip()
    if not t:
        return False
    return bool(_ARTIFACT_DONE_CLAIM.search(t))


def failed_needs_false_beyond_retry(
    *,
    goal: str,
    thought: str,
    write_set: list[str] | None = None,
) -> bool:
    """failed 是否应回灌一轮（至多一次，由闭环计数）。

    触发：自称能力越界 / 编排元概念越界，且 goal 未命中真硬闸。
    已有写入时仍可回灌（文案引导改 success）；真硬闸仍不拦。
    """
    del write_set  # 签名留给调用方；触发条件不依赖
    if not thought_claims_false_beyond(thought):
        return False
    if is_beyond_capability(goal or ""):
        return False
    return True


def should_promote_false_beyond_success(
    *,
    goal: str,
    thought: str,
    write_set: list[str] | None = None,
) -> bool:
    """产物已写入且终稿自认内容齐，却仍用假归因 failed → 应直接升 success。

    真硬闸仍不升。避免回灌耗尽后再次同构假失败（如「元概念不在清单」）。
    """
    paths = [str(p).strip() for p in (write_set or []) if str(p).strip()]
    if not paths:
        return False
    if is_beyond_capability(goal or ""):
        return False
    if not thought_claims_false_beyond(thought):
        return False
    if not thought_admits_artifact_done(thought):
        return False
    return True


def format_false_beyond_fail_prompt(
    *,
    goal: str,
    thought: str,
    write_set: list[str] | None = None,
) -> str:
    """假归因超出能力时回灌硬约束。"""
    paths = [str(p).strip() for p in (write_set or []) if str(p).strip()]
    if paths:
        path_line = "、".join(paths[:8])
        body = (
            f"本任务已写入：{path_line}。\n"
            "开放式文案/大纲/脚本/故事/互动设计属模型生成 + fs_write 落盘，"
            "不因「无创意专用 skill / 超出能力清单 / 编排元概念不在清单」而 failed。\n"
            "监管脑授权、节点租约、产物键绑定由运行时兑现，不是 action_kind，"
            "勿当作缺工具边界。\n"
            "若已写产物已满足停条件或用户目标：请 status=success，thought 摘要交付；"
            "若仍缺内容：继续补写后 success。禁止再次空口宣称能力边界。"
        )
    else:
        body = (
            "本目标未命中真硬闸（实名/支付/软件破解等）。\n"
            "文案/大纲/脚本/故事须由模型直接生成，并用 fs_write（或会话产物路径）落盘；"
            "禁止因「无创意 skill / 超出能力清单 / 编排元概念」空动作 failed。\n"
            "监管脑授权/租约/产物键不是须调用的工具；写出约定文件并 success 即兑现。\n"
            "请立即撰写并写入约定路径，再 status=success；"
            "仅当确实需要清单没有且无法等价完成的动作族时才可 failed 并写清缺哪一类动作。"
        )
    return (
        f"{FALSE_BEYOND_MARKER}\n"
        f"{body}\n"
        f"目标摘要：{(goal or '')[:200]}\n"
        f"上一轮 thought：{(thought or '')[:280]}"
    )
