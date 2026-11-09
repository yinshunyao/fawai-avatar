# term:假归因找不到源码  doc:doc/分析/附图后全程VLM与选档缺口.md
"""failed 假归因「找不到样式/源码」回灌：已有成功读/命中时禁止空口结案。"""

from __future__ import annotations

import re
from typing import Any

FALSE_MISSING_MARKER = "【假归因·找不到源】"

# 终稿自称源码/样式不存在（通用；不绑单一 basename）
_MISSING_SOURCE_CLAIM = re.compile(
    r"(?:"
    r"找不到.{0,16}(?:样式|代码|源码|配置|\.css|CSS|定义|落点)"
    r"|未找到.{0,16}(?:样式|\.css|按钮|定义|源码|代码)"
    r"|样式(?:未|不)(?:在|存在|找到)"
    r"|无法确认.{0,32}(?:定义|来源)"
    r"|无对应(?:文件|源码|代码)"
    r"|代码不在"
    r"|仓内无对应"
    r"|可能位于.{0,24}(?:dist|动态注入|未索引)"
    r"|fs_glob\s*未命中任何\s*\.css"
    r")",
    re.I,
)

_SOURCE_PATH_HINT = re.compile(
    r"(?i)(?:\.html?|\.css|\.scss|\.less|\.js|\.jsx|\.ts|\.tsx|\.vue|\.mdc)$"
    r"|/static/"
    r"|index\.html"
    r"|channel/static"
)


def thought_claims_missing_source(thought: str) -> bool:
    """终稿是否在用「找不到样式/源码」类套话。"""
    t = (thought or "").strip()
    if not t:
        return False
    return bool(_MISSING_SOURCE_CLAIM.search(t))


def path_looks_like_source_evidence(path: str) -> bool:
    p = (path or "").strip()
    if not p:
        return False
    return bool(_SOURCE_PATH_HINT.search(p))


def evidence_paths_from_reads(read_ok_paths: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in read_ok_paths or []:
        p = str(raw or "").strip()
        if path_looks_like_source_evidence(p) and p not in out:
            out.append(p)
    return out


def evidence_paths_from_traces(traces: list[Any] | None) -> list[str]:
    """从轨迹收集：成功 fs_read 源文件，或 fs_grep count>0 的 path/hits。"""
    out: list[str] = []

    def _add(path: str, *, require_source_hint: bool) -> None:
        p = (path or "").strip()
        if not p or p in out:
            return
        if require_source_hint and not path_looks_like_source_evidence(p):
            return
        out.append(p)

    for tr in traces or []:
        plan = getattr(tr, "plan", None)
        results = list(getattr(tr, "results", None) or [])
        actions = list(getattr(plan, "actions", None) or []) if plan is not None else []
        for i, action in enumerate(actions):
            kind = str(getattr(action, "kind", "") or "").strip().lower()
            args = getattr(action, "args", None) or {}
            if not isinstance(args, dict):
                args = {}
            res = results[i] if i < len(results) else None
            if res is None or not bool(getattr(res, "ok", False)):
                continue
            data = getattr(res, "data", None) or {}
            if not isinstance(data, dict):
                data = {}
            if kind in {"fs_read", "fs_read_many"}:
                if kind == "fs_read":
                    _add(str(args.get("path") or ""), require_source_hint=True)
                else:
                    for raw in args.get("paths") or []:
                        _add(str(raw or ""), require_source_hint=True)
            elif kind == "fs_grep":
                count = data.get("count")
                if count is None:
                    hits = data.get("hits")
                    count = len(hits) if isinstance(hits, list) else 0
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    n = 0
                if n <= 0:
                    continue
                # 非空 grep 的检索 path / hit path 均可证明「并非仓内无匹配」
                _add(str(args.get("path") or ""), require_source_hint=False)
                for hit in data.get("hits") or []:
                    if isinstance(hit, dict):
                        _add(str(hit.get("path") or ""), require_source_hint=False)
    return out


def collect_source_evidence(
    *,
    read_ok_paths: list[str] | None = None,
    traces: list[Any] | None = None,
) -> list[str]:
    out = evidence_paths_from_reads(read_ok_paths)
    for p in evidence_paths_from_traces(traces):
        if p not in out:
            out.append(p)
    return out


def failed_needs_false_missing_retry(
    *,
    thought: str,
    read_ok_paths: list[str] | None = None,
    traces: list[Any] | None = None,
) -> bool:
    """failed 是否应回灌：自称找不到源，但轨迹已有成功读/命中。"""
    if not thought_claims_missing_source(thought):
        return False
    return bool(
        collect_source_evidence(read_ok_paths=read_ok_paths, traces=traces)
    )


def format_false_missing_fail_prompt(
    *,
    thought: str,
    evidence_paths: list[str],
) -> str:
    paths = [str(p).strip() for p in evidence_paths if str(p).strip()]
    path_line = "、".join(paths[:8]) if paths else "(已有成功读/命中，见近窗工具结果)"
    return (
        f"{FALSE_MISSING_MARKER}\n"
        "上一轮 failed 声称找不到样式/源码，但本任务已有成功 fs_read / 非空 fs_grep 证据："
        f"{path_line}。\n"
        "禁止再空口「找不到 / 可能在 dist」结案。请二选一：\n"
        "1) 对已证据 path 按命中行号小窗 fs_read，再用 fs_patch/fs_replace 改写后 success；\n"
        "2) 若仍 blocked：status=failed 且分项写清「已齐证据 vs 真正缺什么」"
        "（须与工具结果一致；grep 0 且 pattern 含 \\| 时先改用正则或 `|`，勿当成文件无匹配）。\n"
        f"上一轮 thought：{(thought or '')[:240]}"
    )
