# term:脚本铲子生命周期  tool:shovel_search  doc:doc/功能/脚本铲子生命周期/说明.md
# term:成败经验与检索极性  doc:doc/功能/成败经验与检索极性/说明.md
"""写前搜铲软闸 + 写后提炼软提示 + 差评铲对照软闸（默认不开硬失败）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fawai_avatar.types import Action
from fawai_avatar.workspace.shovel_outcome import (
    extract_shovel_ids_from_actions,
    get_outcome,
)

SEARCH_GATE_MARKER = "【铲子优先】"
PROMOTE_HINT_MARKER = "【铲子提炼】"
OUTCOME_GATE_MARKER = "【成败对照】"

_INVENT_KINDS = frozenset({"py_write", "py_run", "py_pip"})
_SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".zsh")


def shovel_search_gate_enabled() -> bool:
    raw = (os.environ.get("FAWAI_SHOVEL_SEARCH_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def shovel_search_hard_enabled() -> bool:
    raw = (os.environ.get("FAWAI_SHOVEL_SEARCH_HARD") or "0").strip().lower()
    return raw in {"1", "true", "on", "yes"}


def shovel_promote_hint_enabled() -> bool:
    raw = (os.environ.get("FAWAI_SHOVEL_PROMOTE_HINT") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def history_has_shovel_search_ok(history: list[dict[str, Any]] | None) -> bool:
    for msg in history or []:
        if str(msg.get("role") or "") != "user":
            continue
        content = str(msg.get("content") or "")
        if "- shovel_search" in content and "→ ok" in content:
            return True
    return False


def history_has_marker(history: list[dict[str, Any]] | None, marker: str) -> bool:
    for msg in history or []:
        if marker in str(msg.get("content") or ""):
            return True
    return False


def _path_looks_script(path: str) -> bool:
    p = (path or "").strip().lower().replace("\\", "/")
    return any(p.endswith(sfx) for sfx in _SCRIPT_SUFFIXES)


def action_invents_script(action: Action) -> bool:
    kind = (action.kind or "").strip().lower()
    if kind in _INVENT_KINDS:
        return True
    if kind == "fs_write":
        path = str((action.args or {}).get("path") or "")
        if _path_looks_script(path):
            # 产品仓改码走 fs_patch；临时脚本常写 notes/ 或 scripts/
            return True
    if kind == "shell":
        cmd = str(
            (action.args or {}).get("cmd")
            or (action.args or {}).get("command")
            or ""
        ).lower()
        # 明显在跑自己刚写的临时脚本，而非已有铲路径
        if "fawai_shovels/" in cmd or "hipp-tools/" in cmd:
            return False
        if any(tok in cmd for tok in ("python -c", "python3 -c", "bash /tmp", "bash notes/")):
            return True
        if re_search_temp_script(cmd):
            return True
    return False


def re_search_temp_script(cmd: str) -> bool:
    c = cmd or ""
    if "bash " in c or "sh " in c:
        if any(x in c for x in ("notes/", "tmp/", "scripts/", "/tmp/", "session")):
            return True
    return False


def plan_invents_script(actions: list[Action] | None) -> bool:
    return any(action_invents_script(a) for a in (actions or []))


def looks_like_script_goal(goal: str) -> bool:
    g = (goal or "").lower()
    hints = (
        "写脚本",
        "临时脚本",
        "生成脚本",
        "跑一段",
        "python -c",
        "py_write",
        "py_run",
        "写个脚本",
        "自动化脚本",
        "shell 脚本",
        "bash 脚本",
        "可复用工具",
        "沉淀铲子",
        "shovel",
    )
    return any(h in g for h in hints)


def should_inject_search_gate(
    *,
    history: list[dict[str, Any]] | None,
    actions: list[Action] | None,
    goal: str = "",
) -> bool:
    if not shovel_search_gate_enabled():
        return False
    if history_has_shovel_search_ok(history):
        return False
    if history_has_marker(history, SEARCH_GATE_MARKER):
        return False
    # 仅当本步实际要造/跑临时脚本时注入（避免开场空转）
    if plan_invents_script(actions):
        return True
    _ = goal  # 保留签名；goal 启发式留给规划提示
    return False


def format_search_gate_message() -> str:
    return (
        f"{SEARCH_GATE_MARKER}造临时脚本/临时代码前须先 shovel_search（可兼 skill_search）。"
        "能调已有铲或能力清单工具则直接调；没有再 py_write→py_run 或 fs_write+shell。"
        "脚本须参数/env 传需求，禁止明文口令与未公开绝对路径。"
        "通用性自评 ≥3 才可日后写入 fawai_shovels；≤2 留会话临时。"
        "本轮请先 shovel_search，再规划执行。"
    )


def history_had_temp_script_ok(history: list[dict[str, Any]] | None) -> bool:
    for msg in history or []:
        if str(msg.get("role") or "") != "user":
            continue
        content = str(msg.get("content") or "")
        if "→ ok" not in content:
            continue
        if "- py_write" in content or "- py_run" in content:
            return True
        if "- fs_write" in content and any(sfx in content for sfx in _SCRIPT_SUFFIXES):
            return True
    return False


def should_inject_promote_hint(
    *,
    history: list[dict[str, Any]] | None,
    status: str,
) -> bool:
    if not shovel_promote_hint_enabled():
        return False
    if (status or "").strip().lower() != "success":
        return False
    if history_has_marker(history, PROMOTE_HINT_MARKER):
        return False
    return history_had_temp_script_ok(history)


def format_promote_hint_message() -> str:
    return (
        f"{PROMOTE_HINT_MARKER}本任务曾成功写/跑临时脚本。"
        "请自评完备性/通用性/效率（1～5）："
        "通用性≥3 → 用 fs_write 落入 fawai_shovels/<shell|python|media>/（文首 # --- shovel --- meta，"
        "脱敏、参数化）并更新 readme，或 skill_write 写清 when_to_use + invoke；"
        "≤2 → 标明仅本案，勿进公共铲。"
        "此为软提示：可先空 actions success 结束，提炼可另开任务；禁止把口令写进铲。"
    )


def hard_reject_invent_without_search(
    *,
    history: list[dict[str, Any]] | None,
    actions: list[Action] | None,
) -> str | None:
    """若硬闸开启且本步造脚本未搜铲，返回拒因。"""
    if not shovel_search_hard_enabled():
        return None
    if not plan_invents_script(actions):
        return None
    if history_has_shovel_search_ok(history):
        return None
    return (
        "rejected: FAWAI_SHOVEL_SEARCH_HARD — invent script requires prior shovel_search ok"
    )


def shovel_outcome_gate_enabled() -> bool:
    raw = (os.environ.get("FAWAI_SHOVEL_OUTCOME_GATE") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _flagged_shovels_for_actions(
    actions: list[Action] | None,
    *,
    data_dir: str | Path | None,
) -> list[dict[str, Any]]:
    if not data_dir:
        return []
    flagged: list[dict[str, Any]] = []
    for sid in extract_shovel_ids_from_actions(actions):
        outcome = get_outcome(data_dir, sid)
        rate = str(outcome.get("rate") or "unknown")
        if rate == "poor" or (
            rate == "mixed" and str(outcome.get("last_fail_one_liner") or "").strip()
        ):
            flagged.append({"id": sid, "outcome": outcome})
    return flagged


def should_inject_outcome_gate(
    *,
    history: list[dict[str, Any]] | None,
    actions: list[Action] | None,
    data_dir: str | Path | None = None,
    goal: str = "",
) -> bool:
    if not shovel_outcome_gate_enabled():
        return False
    if history_has_marker(history, OUTCOME_GATE_MARKER):
        return False
    flagged = _flagged_shovels_for_actions(actions, data_dir=data_dir)
    _ = goal
    return bool(flagged)


def format_outcome_gate_message(
    *,
    actions: list[Action] | None = None,
    data_dir: str | Path | None = None,
    goal: str = "",
) -> str:
    flagged = _flagged_shovels_for_actions(actions, data_dir=data_dir)
    bits: list[str] = []
    for item in flagged[:5]:
        sid = item.get("id")
        oc = item.get("outcome") or {}
        rate = oc.get("rate")
        fail = str(oc.get("last_fail_one_liner") or "").strip()
        piece = f"{sid} rate={rate}"
        if fail:
            piece += f" last_fail={fail[:120]}"
        bits.append(piece)
    detail = "；".join(bits) if bits else "差评铲"
    goal_hint = (goal or "").strip()
    goal_part = f"当前 goal「{goal_hint[:80]}」" if goal_hint else "当前 goal"
    return (
        f"{OUTCOME_GATE_MARKER}本步拟调用运行态差评/近失败铲子：{detail}。"
        f"请对照{goal_part}：禁止无视成败维原样照抄 invoke；"
        "须换道、改参，或显式说明为何本次不适用该失败模式后再执行。"
        "设计态三维分（完备/通用/效率）≠ 运行成败维。"
    )
