# term:逐步工具调用  doc:doc/功能/编码闭环/说明.md
# term:架构校验  doc:doc/功能/守规约束/说明.md
"""OpenAI 兼容 tools schema ↔ Action。"""

from __future__ import annotations

import json
from typing import Any

from fawai_avatar.brain.capabilities import (
    CAPABILITIES,
    apply_capability_defaults,
    normalize_tool_args,
)
from fawai_avatar.brain.compliance import ensure_architecture_decls
from fawai_avatar.types import Action, Plan

# 编码闭环常用工具（原生 tool_calls）
_CODING_TOOL_KINDS = frozenset(
    {
        "fs_glob",
        "fs_grep",
        "fs_read",
        "fs_read_many",
        "fs_write",
        "fs_replace",
        "fs_patch",
        "code_symbols",
        "code_map",
        "code_index_refresh",
        "code_check",
        "explore_search",
        "git_status",
        "git_diff",
        "git_commit",
        "shell_verify",
        "version_snapshot",
        "version_list",
        "version_rollback",
        "diff_list",
        "done",
    }
)


def openai_tools_for_coding(
    *,
    mutate_prefer: bool = False,
    verify_read: bool = False,
) -> list[dict[str, Any]]:
    """编码原生工具表。

    mutate_prefer=True：只暴露写类 / 检查 / done（探索进度闸门注入后），
    避免模型继续选 fs_read 空转。
    verify_read=True：在 mutate_prefer 基础上临时放行定向 fs_read/fs_grep/fs_glob。
    """
    from fawai_avatar.brain.explore_progress import (
        mutate_prefer_tool_kinds,
        mutate_prefer_with_verify_read_tool_kinds,
    )

    if mutate_prefer and verify_read:
        allow = mutate_prefer_with_verify_read_tool_kinds()
    elif mutate_prefer:
        allow = mutate_prefer_tool_kinds()
    else:
        allow = _CODING_TOOL_KINDS
    tools: list[dict[str, Any]] = []
    for cap in CAPABILITIES:
        if not cap.available or cap.action_kind not in allow:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": cap.action_kind,
                    "description": cap.when_to_use,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": True,
                    },
                },
            }
        )
    return tools


def plan_from_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    content: str = "",
) -> Plan | None:
    if not tool_calls:
        return None
    actions: list[Action] = []
    for call in tool_calls:
        name = str(call.get("name") or "").strip()
        if not name:
            continue
        args_obj = call.get("arguments_obj")
        if not isinstance(args_obj, dict):
            raw = call.get("arguments") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                parsed = {}
            args_obj = parsed if isinstance(parsed, dict) else {}
        # 先展平嵌套 args，再算 write_resources，避免 path 埋在 JSON 字符串里导致租约/架构漏绑
        flat_args = normalize_tool_args(args_obj if isinstance(args_obj, dict) else {})
        actions.append(
            apply_capability_defaults(
                Action(
                    kind=name,
                    args=dict(flat_args),
                    write_resources=_paths_from_args(name, flat_args),
                )
            )
        )
    if not actions:
        return None
    done = len(actions) == 1 and actions[0].kind == "done"
    plan = Plan(
        thought=(content or "native tool_calls").strip() or "native tool_calls",
        actions=[] if done else actions,
        done=done,
        status="success" if done else "continue",
        raw={"tool_calls": tool_calls, "native": True},
    )
    # 原生通道无 JSON 顶层字段：提升 args 声明，否则按分层路径自动绑定默认 doc_refs
    return ensure_architecture_decls(plan, auto_default=True)


def _paths_from_args(kind: str, args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if args.get("path"):
        out.append(str(args["path"]))
    for p in args.get("paths") or []:
        out.append(str(p))
    if kind in {"fs_write", "fs_replace", "fs_patch", "git_commit", "version_rollback"}:
        return out
    return []
