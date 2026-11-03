# term:控制 tool:shell doc:doc/功能/控制/说明.md
"""shell 本地文件读硬闸：禁止用 cat/cp 等绕过 fs_* 读本机文件。"""
from __future__ import annotations

import re
from pathlib import Path

from fawai_avatar.workspace.paths import parse_read_roots, path_under_any, resolve_for_read

# 纯本地读文件族（不含 ssh/rsync/管道复杂场景的宽松放行见下方）
_LOCAL_READ_HEAD = re.compile(
    r"(?:^|[;&|\n]\s*)(?:sudo\s+)?(?:cat|head|tail|less|more|bat|type|Get-Content)\b",
    re.IGNORECASE,
)
_CP_ABS = re.compile(
    r"(?:^|[;&|\n]\s*)(?:sudo\s+)?cp\s+(?:-[a-zA-Z]+\s+)*[\"']?(/[^\"'\s;|&]+|~/[^\"'\s;|&]+)",
    re.IGNORECASE,
)
_QUOTED_ABS = re.compile(r"[\"'](/[^\"']+|~/[^\"']+)[\"']")
_BARE_ABS = re.compile(r"(?<![\w/])(/(?:Users|home|Volumes|data|etc|opt|tmp)/[^\s;|&\"']+)")


def shell_local_fs_bypass_error(
    cmd: str,
    *,
    work_root: str | Path,
    read_roots: list[str | Path] | None = None,
) -> str | None:
    """若命令是「用 shell 读/偷渡本机文件」则返回拒因；ssh/rsync/部署脚本返回 None。"""
    text = (cmd or "").strip()
    if not text:
        return None
    low = text.lower()
    # 远端/部署主路径放行
    if any(
        tok in low
        for tok in (
            "ssh ",
            "scp ",
            "rsync ",
            "deploy_",
            "sshpass",
            "rsync-ssh-deploy",
            "ssh-run-with-pass",
            "queue-remote",
        )
    ):
        return None

    root = Path(work_root).expanduser().resolve()
    roots = (
        [Path(p).expanduser().resolve() for p in read_roots]
        if read_roots is not None
        else parse_read_roots(None)
    )

    is_local_read = bool(_LOCAL_READ_HEAD.search(text))
    is_cp_in = bool(_CP_ABS.search(text))
    if not is_local_read and not is_cp_in:
        return None

    candidates: list[str] = []
    candidates.extend(m.group(1) for m in _QUOTED_ABS.finditer(text))
    candidates.extend(m.group(1) for m in _BARE_ABS.finditer(text))
    if is_cp_in:
        m = _CP_ABS.search(text)
        if m:
            candidates.append(m.group(1))

    for raw in candidates:
        try:
            resolved = resolve_for_read(root, raw, read_roots=roots)
        except Exception:  # noqa: BLE001 — 越界/坏路径仍可能是绕过企图
            # 绝对路径却不在可读根：仍提示走 fs；相对路径留给 shell
            if raw.startswith("/") or raw.startswith("~"):
                return (
                    "禁止用 shell cat/cp 读本机绝对路径绕过 fs_*；"
                    f"请改用 fs_read path={raw!r}（FAWAI_READ_ROOTS 已授权则可直接读）"
                )
            continue
        # 能被 fs_read 解析的本机文件 → 硬拦
        if resolved.is_file() or path_under_any(resolved, [root, *roots]):
            return (
                "禁止用 shell cat/cp/head 读本机文件绕过 fs_*；"
                f"请改用 fs_read path={raw!r}（写仍限 work_root）"
            )
    if is_local_read or is_cp_in:
        # 未能抽出路径，但命令形态仍是本地读/偷渡
        return (
            "禁止用 shell cat/cp 把仓外文件偷渡进工作区再读；"
            "请直接 fs_read 绝对路径（须在 FAWAI_READ_ROOTS 内）"
        )
    return None
