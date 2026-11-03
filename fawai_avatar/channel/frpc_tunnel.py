# term:会话网关  doc:doc/功能/会话网关/说明.md
"""本机 frpc：把局域网网关 TCP 穿透到云端 frps remote_port。"""
from __future__ import annotations

import atexit
import logging
import os
import platform
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SERVER_PORT = "37000"
_PROXY_NAME = "fawai-avatar-gateway"
_proc: subprocess.Popen[str] | None = None
_ini_path: Path | None = None
_atexit_registered = False


@dataclass(frozen=True)
class FrpcTunnelConfig:
    server_addr: str
    server_port: int
    remote_port: int
    local_port: int
    local_ip: str
    frpc_bin: str
    enabled: bool
    proxy_name: str = _PROXY_NAME

    @property
    def public_url(self) -> str:
        return f"http://{self.server_addr}:{self.remote_port}/"

    @classmethod
    def from_env(cls, *, local_port: int) -> "FrpcTunnelConfig":
        from fawai_avatar.config import load_dotenv

        load_dotenv()
        addr = (os.environ.get("FAWAI_FRP_SERVER_ADDR") or "").strip()
        remote_raw = (os.environ.get("FAWAI_FRP_REMOTE_PORT") or "").strip()
        server_port = int((os.environ.get("FAWAI_FRP_SERVER_PORT") or _DEFAULT_SERVER_PORT).strip())
        local_ip = (os.environ.get("FAWAI_FRP_LOCAL_IP") or "127.0.0.1").strip() or "127.0.0.1"
        proxy_name = (os.environ.get("FAWAI_FRP_PROXY_NAME") or _PROXY_NAME).strip() or _PROXY_NAME
        enable_flag = (os.environ.get("FAWAI_FRP_ENABLE") or "").strip().lower()
        # 配齐公网 IP + remote_port 即启用；FAWAI_FRP_ENABLE=0 可显式关掉
        if enable_flag in {"0", "false", "no", "off"}:
            enabled = False
        elif enable_flag in {"1", "true", "yes", "on"}:
            enabled = bool(addr and remote_raw)
        else:
            enabled = bool(addr and remote_raw)
        remote_port = int(remote_raw) if remote_raw else 0
        return cls(
            server_addr=addr,
            server_port=server_port,
            remote_port=remote_port,
            local_port=local_port,
            local_ip=local_ip,
            frpc_bin=_resolve_frpc_bin(),
            enabled=enabled,
            proxy_name=proxy_name,
        )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _binary_kind(path: Path) -> str:
    """粗判可执行文件格式：elf / macho / other / missing。"""
    try:
        with path.open("rb") as fh:
            magic = fh.read(4)
    except OSError:
        return "missing"
    if magic[:4] == b"\x7fELF":
        return "elf"
    # Mach-O 64-bit LE / fat / BE 常见魔数
    if magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}:
        return "macho"
    return "other"


def _frpc_compatible(path: Path) -> bool:
    kind = _binary_kind(path)
    system = platform.system()
    if kind == "missing":
        return False
    if system == "Linux" and kind == "macho":
        return False
    if system == "Darwin" and kind == "elf":
        return False
    return True


def _resolve_frpc_bin() -> str:
    explicit = (os.environ.get("FAWAI_FRPC_BIN") or "").strip()
    if explicit:
        return explicit
    bundled = _project_root() / "bin" / "frpc"
    if bundled.is_file() and os.access(bundled, os.X_OK) and _frpc_compatible(bundled):
        return str(bundled)
    found = shutil.which("frpc")
    if found and _frpc_compatible(Path(found)):
        return found
    # 仍返回 bundled 路径，便于错误信息；启动前再校验兼容性
    return str(bundled)


def _ini_text(cfg: FrpcTunnelConfig) -> str:
    return (
        "[common]\n"
        f"server_addr = {cfg.server_addr}\n"
        f"server_port = {cfg.server_port}\n"
        "\n"
        f"[{cfg.proxy_name}]\n"
        "type = tcp\n"
        f"local_ip = {cfg.local_ip}\n"
        f"local_port = {cfg.local_port}\n"
        f"remote_port = {cfg.remote_port}\n"
    )


def write_ini(cfg: FrpcTunnelConfig, path: Path | None = None) -> Path:
    """写出 frpc.ini；默认落在 data/frpc.ini（随 FAWAI_DATA_DIR）。"""
    if path is None:
        data_dir = Path((os.environ.get("FAWAI_DATA_DIR") or "data").strip() or "data")
        if not data_dir.is_absolute():
            data_dir = _project_root() / data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "frpc.ini"
    path.write_text(_ini_text(cfg), encoding="utf-8")
    return path


def _pidfile_for(ini: Path) -> Path:
    return ini.with_suffix(".pid")


def _read_pidfile(ini: Path) -> int | None:
    path = _pidfile_for(ini)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def _write_pidfile(ini: Path, pid: int) -> None:
    try:
        _pidfile_for(ini).write_text(f"{pid}\n", encoding="utf-8")
    except OSError:
        pass


def _clear_pidfile(ini: Path | None) -> None:
    if ini is None:
        return
    try:
        _pidfile_for(ini).unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline_of(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.strip()


def _is_our_frpc(pid: int, ini: Path) -> bool:
    cmd = _cmdline_of(pid)
    if not cmd:
        return False
    ini_s = str(ini.resolve())
    return "frpc" in cmd and ini_s in cmd


def _signal_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        logger.debug("signal %s to pid=%s failed", sig, pid, exc_info=True)


def _kill_pid(pid: int, *, wait_s: float = 5.0) -> None:
    if not _pid_alive(pid):
        return
    _signal_pid(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    _signal_pid(pid, signal.SIGKILL)


def _pgrep_frpc_for_ini(ini: Path) -> list[int]:
    """找出仍挂着同一份 ini 的 frpc（含 start_new_session 后的孤儿）。"""
    ini_s = str(ini.resolve())
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", f"frpc.*{ini_s}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if _is_our_frpc(pid, ini):
            pids.append(pid)
    return pids


def kill_frpc_for_ini(ini: Path) -> int:
    """结束占用该 ini 的全部 frpc（含孤儿）；返回杀掉的数量。"""
    targets: set[int] = set(_pgrep_frpc_for_ini(ini))
    stored = _read_pidfile(ini)
    if stored is not None and _is_our_frpc(stored, ini):
        targets.add(stored)
    for pid in sorted(targets):
        _kill_pid(pid)
    _clear_pidfile(ini)
    return len(targets)


def stop_frpc() -> None:
    """结束本进程拉起的 frpc，并清掉同 ini 孤儿（幂等）。"""
    global _proc, _ini_path
    proc = _proc
    _proc = None
    ini = _ini_path
    if proc is not None and proc.poll() is None:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    if ini is not None:
        kill_frpc_for_ini(ini)
    else:
        # 无 ini 记录时仍按默认 data/frpc.ini 扫一遍
        data_dir = Path((os.environ.get("FAWAI_DATA_DIR") or "data").strip() or "data")
        if not data_dir.is_absolute():
            data_dir = _project_root() / data_dir
        default_ini = data_dir / "frpc.ini"
        if default_ini.is_file():
            kill_frpc_for_ini(default_ini)
    _ini_path = None
    logger.info("frpc stopped")


def start_frpc(cfg: FrpcTunnelConfig | None = None, *, local_port: int | None = None) -> FrpcTunnelConfig | None:
    """若配置启用则启动 frpc 子进程；返回实际配置或 None（未启用）。"""
    global _proc, _ini_path, _atexit_registered
    if cfg is None:
        if local_port is None:
            local_port = int((os.environ.get("FAWAI_GATEWAY_PORT") or "7980").strip())
        cfg = FrpcTunnelConfig.from_env(local_port=local_port)
    if not cfg.enabled:
        return None
    if not cfg.server_addr or not cfg.remote_port:
        raise RuntimeError("frpc 已启用但缺少 FAWAI_FRP_SERVER_ADDR / FAWAI_FRP_REMOTE_PORT")

    bin_path = Path(cfg.frpc_bin)
    if not bin_path.is_file():
        raise FileNotFoundError(
            f"找不到 frpc：{bin_path}。"
            "请安装对应平台的 frpc，或设置 FAWAI_FRPC_BIN；"
            "macOS 可将 darwin 版放到仓库 bin/frpc，Linux 请放 linux amd64/arm64 版或 PATH 中的 frpc。"
        )
    if not os.access(bin_path, os.X_OK):
        raise PermissionError(f"frpc 不可执行：{bin_path}")
    if not _frpc_compatible(bin_path):
        kind = _binary_kind(bin_path)
        raise RuntimeError(
            f"frpc 与当前系统不匹配：文件={bin_path} 格式={kind} OS={platform.system()}。"
            "请设置 FAWAI_FRPC_BIN 指向本机架构的 frpc，或关闭穿透（FAWAI_FRP_ENABLE=0）。"
        )

    stop_frpc()
    ini = write_ini(cfg)
    # 保活/热重载会丢掉 _proc 引用，但 frpc 因 start_new_session 成孤儿；启动前清干净
    orphaned = kill_frpc_for_ini(ini)
    if orphaned:
        logger.warning("frpc: cleared %s leftover process(es) for %s", orphaned, ini)
    _ini_path = ini
    log_path = ini.with_suffix(".log")
    log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    try:
        _proc = subprocess.Popen(
            [str(bin_path), "-c", str(ini)],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except Exception:
        log_f.close()
        raise
    else:
        # 子进程已 dup 日志 FD；父进程侧必须关掉，否则每次重启泄漏 1 个 FD
        log_f.close()
    _write_pidfile(ini, _proc.pid)
    if not _atexit_registered:
        atexit.register(stop_frpc)
        _atexit_registered = True
    # 短暂等待，便于启动日志里看出连不上
    try:
        code = _proc.wait(timeout=0.4)
    except subprocess.TimeoutExpired:
        code = None
    if code is not None:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8")[-800:]
        except Exception:  # noqa: BLE001
            pass
        _clear_pidfile(ini)
        raise RuntimeError(f"frpc 立即退出 code={code}；日志 {log_path}\n{tail}")
    logger.info(
        "frpc started pid=%s public=%s local=%s:%s ini=%s",
        _proc.pid,
        cfg.public_url,
        cfg.local_ip,
        cfg.local_port,
        ini,
    )
    return cfg


def ensure_frpc_for_gateway(*, local_port: int) -> FrpcTunnelConfig | None:
    """网关入口：按 env 尝试启动；失败时抛出（调用方可捕获并仅打日志）。"""
    cfg = FrpcTunnelConfig.from_env(local_port=local_port)
    if not cfg.enabled:
        return None
    return start_frpc(cfg)
