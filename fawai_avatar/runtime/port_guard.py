# term:服务保活  doc:doc/功能/服务保活/说明.md
"""网关端口占用检查与野进程回收（供保活外层在拉起子进程前调用）。"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass


_GATEWAY_MARKERS = (
    "fawai_avatar.channel.lan_gateway",
    "fawai_avatar.runtime.keeper",
)


def gateway_port_from_env() -> int:
    raw = (os.environ.get("FAWAI_GATEWAY_PORT") or "7980").strip()
    try:
        port = int(raw)
    except ValueError:
        return 7980
    return port if 1 <= port <= 65535 else 7980


def port_reclaim_enabled() -> bool:
    """默认开；FAWAI_PORT_RECLAIM=0 关闭。"""
    raw = (os.environ.get("FAWAI_PORT_RECLAIM") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _run(cmd: list[str], *, timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _cmdline(pid: int) -> str:
    out = _run(["ps", "-ww", "-p", str(pid), "-o", "args="])
    return " ".join(out.strip().split())


def _ppid(pid: int) -> int | None:
    out = _run(["ps", "-p", str(pid), "-o", "ppid="]).strip()
    if not out:
        return None
    try:
        return int(out.split()[0])
    except ValueError:
        return None


def is_fawai_gateway_process(pid: int) -> bool:
    cmd = _cmdline(pid)
    if not cmd:
        return False
    return any(m in cmd for m in _GATEWAY_MARKERS)


def pids_listening_on_port(port: int) -> list[int]:
    """本机 TCP LISTEN 占用该端口的 PID（优先 lsof）。"""
    out = _run(
        [
            "lsof",
            "-nP",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ]
    )
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def list_fawai_gateway_pids(*, exclude: set[int] | None = None) -> list[int]:
    """本机疑似会话网关 / 保活入口的进程。"""
    skip = set(exclude or ())
    skip.add(os.getpid())
    out = _run(["pgrep", "-f", "fawai_avatar.channel.lan_gateway"])
    # keeper 直接入口也可能残留
    out2 = _run(["pgrep", "-f", "fawai_avatar.runtime.keeper"])
    pids: list[int] = []
    for chunk in (out, out2):
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid in skip or pid in pids:
                continue
            if is_fawai_gateway_process(pid):
                pids.append(pid)
    return pids


def _signal_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    # 子网关常 start_new_session，顺带打进程组
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


@dataclass
class ReclaimResult:
    port: int
    killed: list[int]
    foreign_holders: list[int]
    still_busy: bool

    def summary(self) -> str:
        parts: list[str] = [f"port={self.port}"]
        if self.killed:
            parts.append(f"已结束野进程 {self.killed}")
        if self.foreign_holders:
            parts.append(f"非本产品占用 PID {self.foreign_holders}（未杀）")
        if self.still_busy:
            parts.append("端口仍忙")
        elif not self.killed and not self.foreign_holders:
            parts.append("空闲")
        return "；".join(parts)


def reclaim_gateway_port(
    *,
    port: int | None = None,
    keep_pids: set[int] | None = None,
    wait_s: float = 3.0,
) -> ReclaimResult:
    """结束占用网关端口的本产品野进程；外来占用只报告不杀。"""
    port = gateway_port_from_env() if port is None else port
    keep = set(keep_pids or ())
    keep.add(os.getpid())

    killed: list[int] = []
    foreign: list[int] = []

    # 1) 端口监听者
    for pid in pids_listening_on_port(port):
        if pid in keep:
            continue
        if is_fawai_gateway_process(pid):
            parent = _ppid(pid)
            _signal_pid(pid, signal.SIGTERM)
            killed.append(pid)
            # 孤儿网关的保活外层若也是本产品且不是自己，一并收掉，避免它立刻又拉起抢端口
            if parent and parent not in keep and parent != 1 and is_fawai_gateway_process(parent):
                _signal_pid(parent, signal.SIGTERM)
                if parent not in killed:
                    killed.append(parent)
        else:
            foreign.append(pid)

    # 2) 未占端口的残留本产品网关（常见：停住的 T 态、或刚退出保活留下的孤儿）
    for pid in list_fawai_gateway_pids(exclude=keep):
        if pid in killed:
            continue
        parent = _ppid(pid)
        # 只动「无主」或「父已非本保活」的残留；正在被本 keeper 管理的会在 keep 里
        if parent == 1 or parent is None:
            _signal_pid(pid, signal.SIGTERM)
            killed.append(pid)

    if killed:
        deadline = time.monotonic() + max(0.2, wait_s)
        while time.monotonic() < deadline:
            alive = []
            for pid in killed:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except OSError:
                    continue
            if not alive:
                break
            for pid in alive:
                _signal_pid(pid, signal.SIGKILL)
            time.sleep(0.15)

    holders = [p for p in pids_listening_on_port(port) if p not in keep]
    return ReclaimResult(
        port=port,
        killed=killed,
        foreign_holders=sorted(set(foreign) | {p for p in holders if not is_fawai_gateway_process(p)}),
        still_busy=bool(holders),
    )
