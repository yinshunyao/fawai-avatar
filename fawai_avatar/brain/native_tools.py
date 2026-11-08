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
        "py_write",
        "py_pip",
        "py_run",
        "shovel_search",
        "task_context_search",
        "task_context_get",
        "version_snapshot",
        "version_list",
        "version_rollback",
        "diff_list",
        "done",
    }
)

# 高频检索/读写工具补实参，降低空 schema 导致的宽搜与误参
_TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "fs_grep": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "必填；内容正则/子串，避免过短过宽",
            },
            "path": {
                "type": "string",
                "description": "检索根路径；已知文件时填单文件 path",
            },
            "glob": {"type": "string", "description": "文件名过滤，如 **/*.py"},
            "context_before": {
                "type": "integer",
                "description": "命中前上下文行数（推荐 1～3）",
            },
            "context_after": {
                "type": "integer",
                "description": "命中后上下文行数（推荐 1～3）",
            },
            "head_limit": {
                "type": "integer",
                "description": "最大命中条数（默认 40，硬顶 100）；达上限自行加大或收窄翻页",
            },
            "case_insensitive": {"type": "boolean"},
        },
        "required": ["pattern"],
        "additionalProperties": True,
    },
    "explore_search": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "并行检索词；默认每条只 grep（不建符号索引）。"
                    "单关键词优先用 fs_grep；也可传单个字符串"
                ),
            },
            "query": {
                "type": "string",
                "description": "单条检索词（兼容；等同 queries=[query]）",
            },
            "pattern": {
                "type": "string",
                "description": "兼容 fs_grep 习惯；并入 queries",
            },
            "path": {
                "type": "string",
                "description": "探仓范围，须尽量收窄子目录；默认 .",
            },
            "glob": {
                "type": "string",
                "description": (
                    "文件过滤；省略或 * 时默认源码/配置后缀。"
                    "要扫全部文件显式传宽 glob"
                ),
            },
            "include_symbols": {
                "type": "boolean",
                "description": "true=每词额外跑符号索引（较慢）；默认 false",
            },
            "symbols_query": {
                "type": "string",
                "description": "仅符号索引（会触发 ensure_index）；可与 queries 并用",
            },
            "context_before": {"type": "integer"},
            "context_after": {"type": "integer"},
            "head_limit": {
                "type": "integer",
                "description": "每通道命中上限，默认 20",
            },
        },
        "additionalProperties": True,
    },
    "fs_read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区相对路径，必填"},
            "offset": {
                "type": "integer",
                "description": "1-based 起始行；定向读必填或默认文件头",
            },
            "limit": {"type": "integer", "description": "读取行数，宜小窗"},
            "allow_repeat": {
                "type": "boolean",
                "description": (
                    "可选。true=本步故意同构（如 patch 后同窗验收），"
                    "闭环跳过步骤重复计数与裁决；无新意图勿滥用"
                ),
            },
        },
        "required": ["path"],
        "additionalProperties": True,
    },
    "fs_glob": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob，支持 ** 与 {a,b}",
            },
            "path": {
                "type": "string",
                "description": (
                    "搜索根：默认 .（work_root）；可为 FAWAI_READ_ROOTS 下绝对路径。"
                    "传入后从该目录扫，不会静默忽略改扫 work_root"
                ),
            },
            "head_limit": {"type": "integer"},
        },
        "additionalProperties": True,
    },
    "fs_patch": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "hunks": {
                "type": "array",
                "items": {"type": "object"},
                "description": "[{old,new}, ...]；old 须与原文一致",
            },
            "old": {"type": "string"},
            "new": {"type": "string"},
            "doc_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path"],
        "additionalProperties": True,
    },
    "fs_replace": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "doc_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path", "old", "new"],
        "additionalProperties": True,
    },
    "code_check": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "单个待检相对路径（推荐）；.py→py_compile",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "多个待检路径；与 path 二选一或并用",
            },
            "command": {
                "type": "string",
                "description": "自定义检查命令（cwd=工作区）；有则不再按后缀检查 path",
            },
            "file": {
                "type": "string",
                "description": "兼容别名 → path",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "兼容别名 → paths",
            },
            "cmd": {
                "type": "string",
                "description": "兼容别名 → command",
            },
            "timeout_sec": {"type": "number"},
        },
        "required": ["path"],
        "additionalProperties": True,
    },
    "py_write": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对会话根 data/sessions/{task_id}/ 的路径",
            },
            "content": {
                "type": "string",
                "description": (
                    "文件全文。新建脚本尽最大努力 stdout 打一行 OK 或 FAIL:原因，"
                    "失败非零退出，便于调用侧 expect（精确或模糊）；非硬闸"
                ),
            },
        },
        "required": ["path", "content"],
        "additionalProperties": True,
    },
    "py_pip": {
        "type": "object",
        "properties": {
            "packages": {
                "description": "包名列表或空格分隔字符串；仅 install",
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ],
            },
            "timeout_sec": {"type": "number"},
        },
        "required": ["packages"],
        "additionalProperties": True,
    },
    "py_run": {
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "会话根内相对脚本路径（与 code/module 三选一）",
            },
            "code": {
                "type": "string",
                "description": "临时代码（python -c）；与 script/module 三选一",
            },
            "module": {
                "type": "string",
                "description": "python -m 模块名；与 script/code 三选一",
            },
            "args": {
                "description": "传给脚本的参数列表或空格串",
                "oneOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "string"},
                ],
            },
            "timeout_sec": {"type": "number", "description": "默认 60"},
            "result_path": {
                "type": "string",
                "description": "脚本写出的结果文件（相对会话根），存在则回传",
            },
        },
        "additionalProperties": True,
    },
    "shovel_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索词（id/title/when_to_use/tags）；造临时脚本前先搜",
            },
            "limit": {"type": "integer", "description": "命中上限，默认 20"},
        },
        "required": ["query"],
        "additionalProperties": True,
    },
    "task_context_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜本任务历史（追问/工具结果细节）；近窗没有时用",
            },
            "limit": {"type": "integer", "description": "命中上限，默认 8"},
            "kind": {
                "type": "string",
                "description": "可选过滤：user|assistant|tool|compact",
            },
        },
        "required": ["query"],
        "additionalProperties": True,
    },
    "task_context_get": {
        "type": "object",
        "properties": {
            "msg_id": {
                "type": "string",
                "description": "search 命中的 msg_id",
            },
            "index": {
                "type": "integer",
                "description": "历史消息下标（与 msg_id 二选一）",
            },
            "observe_path": {
                "type": "string",
                "description": "observe/tool_* dump 路径；读落盘全文切片",
            },
            "max_chars": {
                "type": "integer",
                "description": "回灌字符硬顶，默认约 6000",
            },
        },
        "additionalProperties": True,
    },
}


def _parameters_for_tool(kind: str) -> dict[str, Any]:
    schema = _TOOL_PARAMETER_SCHEMAS.get(kind)
    if schema:
        return dict(schema)
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def openai_tools_for_coding(
    *,
    mutate_prefer: bool = False,
    verify_read: bool = False,
    allow_delegate: bool = True,
) -> list[dict[str, Any]]:
    """编码原生工具表。

    mutate_prefer=True：只暴露写类 / 检查 / done（探索进度闸门注入后），
    避免模型继续选 fs_read 空转。
    verify_read=True：在 mutate_prefer 基础上临时放行定向 fs_read/fs_grep/fs_glob。
    allow_delegate：主脑可调 delegate_to_fast；快模型段关闭。
    """
    from fawai_avatar.brain.delegate import fast_model_from_env
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
                    "parameters": _parameters_for_tool(cap.action_kind),
                },
            }
        )
    if allow_delegate and fast_model_from_env() and not mutate_prefer:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "delegate_to_fast",
                    "description": (
                        "机械/低风险子段优先委派快模型（只读定位、已知路径再读、远程 ls/"
                        "进程/supervisor/端口探查、同构只读链、简单改写）；本步不再给其它工具。"
                        "高风险改写/终答/互斥路线/需澄清勿用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "goal": {
                                "type": "string",
                                "description": "子目标（给快模型）",
                            },
                            "stop_when": {
                                "type": "string",
                                "description": "停条件 / 验收口径",
                            },
                            "max_steps": {
                                "type": "integer",
                                "description": "段内最多步数（默认 5，硬顶 12）",
                            },
                        },
                        "required": ["goal"],
                        "additionalProperties": False,
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
    # 优先识别委派：与其它工具同轮时只取委派
    for call in tool_calls:
        name = str(call.get("name") or "").strip()
        if name != "delegate_to_fast":
            continue
        from fawai_avatar.brain.delegate import parse_delegate, with_delegate

        args_obj = call.get("arguments_obj")
        if not isinstance(args_obj, dict):
            raw = call.get("arguments") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                parsed = {}
            args_obj = parsed if isinstance(parsed, dict) else {}
        spec = parse_delegate(
            {
                "tier": "fast",
                "goal": args_obj.get("goal"),
                "stop_when": args_obj.get("stop_when"),
                "max_steps": args_obj.get("max_steps"),
            }
        )
        if spec is None:
            break
        thought = (content or f"委派快模型：{spec.goal}").strip()
        from fawai_avatar.task.title import resolve_step_label

        plan = Plan(
            thought=thought,
            actions=[],
            done=False,
            status="continue",
            raw={"tool_calls": tool_calls, "native": True, "delegate": spec.to_dict()},
            step_label=resolve_step_label(
                thought=thought,
                actions=[],
            )
            or "委派快模型",
            delegate=spec.to_dict(),
        )
        return with_delegate(plan, spec)

    actions: list[Action] = []
    for call in tool_calls:
        name = str(call.get("name") or "").strip()
        if not name or name == "delegate_to_fast":
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
        expect = None
        if isinstance(flat_args.get("expect"), dict) and flat_args.get("expect"):
            expect = dict(flat_args.pop("expect"))
        elif "expect" in flat_args:
            flat_args.pop("expect", None)
        actions.append(
            apply_capability_defaults(
                Action(
                    kind=name,
                    args=dict(flat_args),
                    write_resources=_paths_from_args(name, flat_args),
                    expect=expect,
                )
            )
        )
    if not actions:
        return None
    done = len(actions) == 1 and actions[0].kind == "done"
    thought = (content or "native tool_calls").strip() or "native tool_calls"
    from fawai_avatar.task.title import resolve_step_label

    plan_actions = [] if done else actions
    plan = Plan(
        thought=thought,
        actions=plan_actions,
        done=done,
        status="success" if done else "continue",
        raw={"tool_calls": tool_calls, "native": True},
        step_label=resolve_step_label(thought=thought, actions=plan_actions),
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
