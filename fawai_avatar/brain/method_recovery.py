# term:执行反省  doc:doc/功能/执行反省/说明.md
# term:完成任务  doc:doc/功能/完成任务/说明.md
"""方法族自我纠错：失败语义→换道约束；远端部署进度闸；远程不在场否决表。"""
from __future__ import annotations

import os
import re
from typing import Any

from fawai_avatar.brain.reflect import Reflection
from fawai_avatar.types import Action, ExecResult

METHOD_SWITCH_MARKER = "【方法换道】"
REMOTE_GATE_MARKER = "【远端进度闸门】"
REMOTE_FORCE_MARKER = "【远端方法强制】"
REMOTE_AWAY_MARKER = "【远程操作约束】"

_SSH_AUTH_HINTS = (
    "permission denied (publickey",
    "permission denied (password",
    "permission denied (publickey,password)",
    "authentication failed",
    "auth fail",
    "too many authentication failures",
)

_REMOTE_GOAL_HINTS = (
    "升级",
    "部署",
    "同步到",
    "同步至",
    "推到",
    "推送到",
    "发布到",
    "传到",
    "上传到",
    "服务器",
    "远端",
    "远程",
    "5080",
    "4090",
    "ssh",
    "rsync",
    "deploy",
    "测试机",
    "线上",
)

_REMOTE_AWAY_HINTS = (
    "没在电脑边",
    "不在电脑边",
    "不在电脑旁",
    "远程操作",
    "手机上",
    "你想办法",
    "你自己读",
    "你自己看",
    "我没法贴",
    "没法复制",
    "不在身边",
    "away from",
    "not at my computer",
    "not at the computer",
)

_SIDE_EFFECT_SHELL_TOKENS = (
    "ssh ",
    "scp ",
    "rsync ",
    "rsync-ssh-deploy",
    "ssh-run-with-pass",
    "queue-remote",
    "deploy_ssh",
    "deploy_remote",
    "sshpass",
)

CONSTRAINT_SSH_AUTH = (
    "SSH 认证失败（需口令/密钥）：禁止再 BatchMode/无口令公钥空探针；"
    "下一步须 fs_read 授权配置取 host/port/口令进 env，"
    "再跑 fawai_shovels/shell/ssh-run-with-pass.sh 或 rsync-ssh-deploy.sh；"
    "也可在 work_root 临时 fs_write 脚本后 shell 执行"
)

CONSTRAINT_PATH_ESCAPE = (
    "路径越界：改用 FAWAI_READ_ROOTS 下绝对路径 fs_read（默认 /）；"
    "禁止 shell cat/cp 偷渡；写仍限 work_root"
)

CONSTRAINT_REMOTE_IDLE = (
    "远端/部署类目标已连续多步无远端副作用：本步必须换方法族——"
    "带密 shovel / work_root 临时脚本+shell，或 status=failed 并在 thought 给出可复制命令；"
    "禁止继续只读同一部署说明"
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def looks_like_remote_ops_goal(goal: str) -> bool:
    g = (goal or "").lower()
    if not g.strip():
        return False
    return any(h.lower() in g for h in _REMOTE_GOAL_HINTS)


def looks_like_remote_away_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(h.lower() in t for h in _REMOTE_AWAY_HINTS)


def format_remote_away_veto(*, answer: str = "") -> str:
    extra = f"用户原话要点：{answer[:120]}。" if answer.strip() else ""
    return (
        f"{REMOTE_AWAY_MARKER}\n"
        f"{extra}"
        "用户不在宿主机边 / 要求自行处理："
        "禁止再要求粘贴配置或拷进工作区；"
        "优先 FAWAI_READ_ROOTS 下 fs_read；"
        "部署走 shovel/临时脚本；"
        "仍不可达则 failed，thought 给出脱敏可复制命令。"
    )


def _shell_cmd_blob(actions: list[Action]) -> str:
    parts: list[str] = []
    for a in actions:
        if (a.kind or "").strip().lower() != "shell":
            continue
        args = a.args or {}
        cmd = args.get("cmd") or args.get("command") or ""
        if isinstance(args.get("argv"), list):
            cmd = " ".join(str(x) for x in args["argv"])
        parts.append(str(cmd))
    return "\n".join(parts).lower()


def _err_blob(results: list[ExecResult]) -> str:
    return " ".join(str(r.message or "") for r in results if not r.ok).lower()


def is_ssh_auth_failure(actions: list[Action], results: list[ExecResult]) -> bool:
    err = _err_blob(results)
    if not err:
        return False
    if any(h in err for h in _SSH_AUTH_HINTS):
        return True
    # 泛 Permission denied + 本步含 ssh
    if "permission denied" in err and "ssh" in _shell_cmd_blob(actions):
        return True
    return False


def is_path_escape_failure(results: list[ExecResult]) -> bool:
    err = _err_blob(results)
    return "escapes work root" in err or "path escapes" in err or "read roots" in err


def method_constraints_for_failure(
    actions: list[Action],
    results: list[ExecResult],
    *,
    goal: str = "",
) -> list[str]:
    """按失败语义返回应注入的换道约束（可空）。"""
    out: list[str] = []
    if is_ssh_auth_failure(actions, results):
        out.append(CONSTRAINT_SSH_AUTH)
    if is_path_escape_failure(results):
        out.append(CONSTRAINT_PATH_ESCAPE)
    if looks_like_remote_ops_goal(goal) and is_ssh_auth_failure(actions, results):
        # 强化：部署目标下认证失败必须换 shovel 族
        if CONSTRAINT_SSH_AUTH not in out:
            out.append(CONSTRAINT_SSH_AUTH)
    return out


def enrich_reflection_method_switch(
    reflection: Reflection,
    *,
    actions: list[Action],
    results: list[ExecResult],
    goal: str = "",
) -> Reflection:
    """把失败→方法族约束并入反省；SSH 认证类禁止直接 abort 成「缺权限」。"""
    extra = method_constraints_for_failure(actions, results, goal=goal)
    if not extra and not is_ssh_auth_failure(actions, results):
        return reflection

    decision = reflection.decision
    cause = reflection.cause
    thought = reflection.thought
    constraints = list(reflection.constraints or [])

    if is_ssh_auth_failure(actions, results):
        # 勿把口令 SSH 失败当成租约/辅助功能类 abort
        if decision == "abort" and not any(
            tok in (cause or "").lower()
            for tok in ("租约", "lease", "辅助功能", "foreground")
        ):
            decision = "alternate"
            cause = "SSH 认证失败，须换带密部署方法族（非缺本机 GUI 权限）"
            thought = "改用 ssh-run-with-pass / rsync-ssh-deploy，禁止再 BatchMode 空探针"
        for c in extra:
            if c not in constraints:
                constraints.insert(0, c)
    else:
        for c in extra:
            if c not in constraints:
                constraints.append(c)

    raw = dict(reflection.raw or {})
    raw["method_switch"] = True
    return Reflection(
        cause=cause,
        decision=decision,
        constraints=constraints[:10],
        thought=thought,
        fingerprint=reflection.fingerprint,
        forced=reflection.forced,
        raw=raw,
    )


def format_method_switch_history(constraints: list[str]) -> str:
    lines = [METHOD_SWITCH_MARKER, "失败语义已映射到方法族，下一轮必须遵守："]
    lines.extend(f"- {c}" for c in constraints[:6])
    return "\n".join(lines)


def remote_idle_gate_threshold(*, goal: str = "") -> int:
    """连续无远端副作用多少步后注入换道；0=关（默认）。

    部署类目标先读材料也算正常取证，默认不因只读步数逼远端副作用。
    仅显式 `FAWAI_REMOTE_IDLE_GATE`>0 时注入。
    """
    _ = goal  # 保留参数兼容；默认关后不再按目标关键词默认 6
    raw = (os.environ.get("FAWAI_REMOTE_IDLE_GATE") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def remote_idle_abort_threshold(*, goal: str = "") -> int:
    gate = remote_idle_gate_threshold(goal=goal)
    if gate <= 0:
        return 0
    raw = (os.environ.get("FAWAI_REMOTE_IDLE_ABORT") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return gate * 2


def _cmd_has_remote_side_effect(cmd: str) -> bool:
    low = (cmd or "").lower()
    return any(tok in low for tok in _SIDE_EFFECT_SHELL_TOKENS)


def is_remote_side_effect_step(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """本步是否产生了远端相关动作（成功与否都算试过方法族；成功则清 streak）。"""
    for i, action in enumerate(actions):
        kind = (action.kind or "").strip().lower()
        if kind != "shell":
            continue
        args = action.args or {}
        cmd = str(args.get("cmd") or args.get("command") or "")
        if isinstance(args.get("argv"), list):
            cmd = " ".join(str(x) for x in args["argv"])
        if not _cmd_has_remote_side_effect(cmd):
            continue
        if i < len(results) and results[i].ok:
            return True
        # 失败的 ssh/rsync 也算进入远端方法族（由反省换道），用于清「纯探索」streak
        return True
    return False


def is_remote_idle_explore_step(
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    """远端目标下：成功只读/检索、无远端副作用 → 计 idle。"""
    if not actions:
        return False
    if is_remote_side_effect_step(actions, results):
        return False
    kinds = [(a.kind or "").strip().lower() for a in actions]
    explore = {
        "fs_glob",
        "fs_grep",
        "fs_read",
        "fs_read_many",
        "explore_search",
        "lore_search",
        "lore_get",
        "code_symbols",
        "code_map",
        "skill_search",
        "skill_get",
    }
    if any(k not in explore for k in kinds):
        # 含 shell 但非远端副作用（如 ls）也算 idle 探索
        if all(
            k in explore or k == "shell"
            for k in kinds
        ):
            # 纯本地 shell 查看
            if not results or not all(r.ok for r in results[: len(actions)]):
                return False
            return True
        return False
    if not results or len(results) < len(actions):
        return False
    return all(r.ok for r in results[: len(actions)])


def update_remote_idle_streak(
    streak: int,
    *,
    goal: str,
    actions: list[Action],
    results: list[ExecResult],
) -> int:
    if not looks_like_remote_ops_goal(goal):
        return 0
    if is_remote_side_effect_step(actions, results):
        # 若远端动作成功，清零；失败仍清探索 idle（已离开只读相位）
        for i, action in enumerate(actions):
            if (action.kind or "").strip().lower() != "shell":
                continue
            args = action.args or {}
            cmd = str(args.get("cmd") or args.get("command") or "")
            if isinstance(args.get("argv"), list):
                cmd = " ".join(str(x) for x in args["argv"])
            if _cmd_has_remote_side_effect(cmd) and i < len(results) and results[i].ok:
                return 0
        return 0
    if is_remote_idle_explore_step(actions, results):
        return int(streak) + 1
    return int(streak)


def remote_gate_should_inject(*, streak: int, gate: int) -> bool:
    return gate > 0 and streak >= gate


def remote_gate_should_force(*, streak: int, abort_at: int, forced: bool) -> bool:
    return abort_at > 0 and streak >= abort_at and not forced


def remote_gate_should_abort_after_force(
    *,
    streak: int,
    abort_at: int,
    forced: bool,
    actions: list[Action],
    results: list[ExecResult],
) -> bool:
    if not forced or abort_at <= 0:
        return False
    if streak < abort_at:
        return False
    # 强制轮之后仍无远端成功副作用
    for i, action in enumerate(actions):
        if (action.kind or "").strip().lower() != "shell":
            continue
        args = action.args or {}
        cmd = str(args.get("cmd") or args.get("command") or "")
        if isinstance(args.get("argv"), list):
            cmd = " ".join(str(x) for x in args["argv"])
        if _cmd_has_remote_side_effect(cmd) and i < len(results) and results[i].ok:
            return False
    return is_remote_idle_explore_step(actions, results) or not is_remote_side_effect_step(
        actions, results
    )


def strip_disabled_remote_gate_history(
    history: list[dict[str, Any]] | None,
    *,
    goal: str = "",
) -> list[dict[str, Any]]:
    """闸门默认关时，已注入的远端闸文案不再送进下一轮规划。"""
    msgs = list(history or [])
    if remote_idle_gate_threshold(goal=goal) > 0:
        return msgs
    markers = (REMOTE_GATE_MARKER, REMOTE_FORCE_MARKER)
    kept: list[dict[str, Any]] = []
    for msg in msgs:
        text = msg.get("content")
        if not isinstance(text, str):
            text = str(text or "")
        if any(marker in text for marker in markers):
            continue
        kept.append(msg)
    return kept


def format_remote_gate_message(*, streak: int, gate: int, abort_at: int) -> str:
    return (
        f"{REMOTE_GATE_MARKER}\n"
        f"远端/部署类目标已连续 {streak} 步无远端副作用"
        f"（闸值 {gate}，中止约 {abort_at}）。\n"
        f"- {CONSTRAINT_REMOTE_IDLE}"
    )


def format_remote_force_message(*, streak: int, gate: int, abort_at: int) -> str:
    return (
        f"{REMOTE_FORCE_MARKER}\n"
        f"已达远端空转中止线（streak={streak}≥{abort_at}）。本步强制换方法族：\n"
        "- 必须 shell 调用 rsync-ssh-deploy.sh 或 ssh-run-with-pass.sh（口令仅 env）；\n"
        "- 或 fs_write 临时脚本后执行；\n"
        "- 否则 status=failed，thought 写出完整脱敏命令供用户远程转发。\n"
        f"禁止再 fs_read 同一部署说明空转（gate={gate}）。"
    )


def strip_paste_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """去掉「请粘贴/拷进工作区」类选项（远程不在场时）。"""
    bad = ("粘贴", "拷贝", "复制到", "拷进", "贴给", "发配置", "paste", "copy into")
    out: list[dict[str, str]] = []
    for opt in options:
        label = str(opt.get("label") or "")
        if any(b in label for b in bad):
            continue
        out.append(opt)
    return out


_BATCHMODE_RE = re.compile(r"batchmode\s*=\s*yes", re.I)
