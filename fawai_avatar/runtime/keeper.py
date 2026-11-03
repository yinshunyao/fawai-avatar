# term:服务保活  doc:doc/功能/服务保活/说明.md
"""外层保活：崩溃自动拉起；响应 Web 下发的重启 / 激活存档；可选代码变更自动重启。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Sequence

from fawai_avatar.runtime.code_watch import code_reload_enabled, start_code_watch
from fawai_avatar.runtime.console_log import (
    configure_logging,
    cprint,
    extract_crash_excerpt,
    open_child_log_file,
    read_console_log,
    write_last_crash,
)
from fawai_avatar.runtime.port_guard import (
    gateway_port_from_env,
    port_reclaim_enabled,
    reclaim_gateway_port,
)
from fawai_avatar.runtime.releases import (
    EXIT_ACTIVATE,
    EXIT_RESTART,
    package_dir,
    package_root,
    releases_from_env,
)

# 崩溃后退避（秒）；成功跑过后重置
_MIN_BACKOFF = 1.0
_MAX_BACKOFF = 30.0


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["FAWAI_KEEPER"] = "1"
    # 确保工程根在 path
    root = str(package_root())
    py_path = env.get("PYTHONPATH", "")
    parts = [p for p in py_path.split(os.pathsep) if p]
    if root not in parts:
        env["PYTHONPATH"] = os.pathsep.join([root, *parts]) if parts else root
    return env


def _spawn_gateway(
    extra_args: Sequence[str] | None = None,
    *,
    log_fh: IO[str] | None = None,
) -> subprocess.Popen:
    cmd = [sys.executable, "-u", "-m", "fawai_avatar.channel.lan_gateway", *(extra_args or ())]
    kwargs: dict = {
        "cwd": str(package_root()),
        "env": _child_env(),
        "start_new_session": True,
    }
    if log_fh is not None:
        # PIPE + tee：终端仍可见，同时落盘供 Web 后台日志
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["bufsize"] = 1
        kwargs["text"] = True
    return subprocess.Popen(cmd, **kwargs)


def _start_child_tee(proc: subprocess.Popen, log_fh: IO[str] | None) -> None:
    """把子进程 stdout 同步到 stderr 与 console.log。"""

    def _pump() -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            while True:
                line = stream.readline()
                if line == "" and proc.poll() is not None:
                    break
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                try:
                    sys.stderr.write(line if line.endswith("\n") else line + "\n")
                    sys.stderr.flush()
                except OSError:
                    pass
                if log_fh is not None:
                    try:
                        log_fh.write(line if line.endswith("\n") else line + "\n")
                        log_fh.flush()
                    except OSError:
                        pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    threading.Thread(target=_pump, name="fawai-keeper-tee", daemon=True).start()


def run_keeper(
    *,
    data_dir: str | None = None,
    extra_args: Sequence[str] | None = None,
) -> None:
    """阻塞运行保活循环。Ctrl+C / SIGTERM 结束外层并带走子进程。"""
    from fawai_avatar.config import RuntimeConfig

    cfg_data = data_dir or RuntimeConfig.from_env().data_dir
    data_root = str(Path(cfg_data).expanduser().resolve())
    configure_logging(data_dir=data_root)
    store = releases_from_env(data_root)
    reload_on = code_reload_enabled()
    reclaim_on = port_reclaim_enabled()
    listen_port = gateway_port_from_env()
    cprint("=" * 60)
    cprint("fawai-avatar 服务保活")
    cprint(f"  工程根: {package_root()}")
    cprint(f"  存档目录: {store.releases_dir}")
    cprint(f"  网关端口: {listen_port}（FAWAI_GATEWAY_PORT）")
    cprint(f"  后台日志: {data_root}/runtime/console.log")
    cprint("  子进程崩溃或主动重启后会自动拉起；Ctrl+C 停止外层。")
    if reload_on:
        cprint(f"  代码热重载: 开（监视 {package_dir()} 下 .py/.html/.js/.css；关：FAWAI_CODE_RELOAD=0）")
    else:
        cprint("  代码热重载: 关（FAWAI_CODE_RELOAD=0）")
    if reclaim_on:
        cprint("  端口回收: 开（拉起前清理占用端口的本产品野进程；关：FAWAI_PORT_RECLAIM=0）")
    else:
        cprint("  端口回收: 关（FAWAI_PORT_RECLAIM=0）")
    cprint("=" * 60)

    child: subprocess.Popen | None = None
    child_lock = threading.Lock()
    backoff = _MIN_BACKOFF
    stopping = False
    code_reload_hit = threading.Event()
    watch_stop = threading.Event()
    child_log_fh: IO[str] | None = None
    try:
        child_log_fh = open_child_log_file(data_root)
    except OSError as exc:
        cprint(f"[keeper] 无法打开后台日志文件: {exc}")
        child_log_fh = None

    def _signal_proc(proc: subprocess.Popen, sig: signal.Signals) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                if sig == signal.SIGKILL:
                    proc.kill()
                else:
                    proc.terminate()
            except OSError:
                pass

    def _stop_child(*, wait: bool = True) -> None:
        """停掉当前网关子进程。

        监视线程只负责发信号（wait=False），由主循环的 proc.wait() 收尸，
        避免与主循环抢着把 ``child`` 置空，导致后续热重载变成空操作。
        """
        nonlocal child
        with child_lock:
            proc = child
        if proc is None:
            return
        if proc.poll() is not None:
            with child_lock:
                if child is proc:
                    child = None
            return
        _signal_proc(proc, signal.SIGTERM)
        if not wait:
            return
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _signal_proc(proc, signal.SIGKILL)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        with child_lock:
            if child is proc:
                child = None

    def _on_code_change(paths: list[str]) -> None:
        if stopping:
            return
        sample = paths[0] if paths else "?"
        more = f" 等 {len(paths)} 个文件" if len(paths) > 1 else ""
        cprint(f"[keeper] 检测到代码变更: {sample}{more} → 重启网关")
        code_reload_hit.set()
        # 只发信号，不在监视线程里 wait / child=None
        _stop_child(wait=False)

    def _on_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        watch_stop.set()
        cprint(f"保活收到信号 {signum}，正在停止…")
        _stop_child(wait=True)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if reload_on:
        start_code_watch([package_dir()], _on_code_change, stop_event=watch_stop)

    while not stopping:
        started = time.monotonic()
        if reclaim_on:
            result = reclaim_gateway_port(port=listen_port, keep_pids={os.getpid()})
            if result.killed or result.foreign_holders or result.still_busy:
                cprint(f"[keeper] 端口检查: {result.summary()}")
            if result.still_busy:
                cprint(
                    f"[keeper] 端口 {listen_port} 仍被占用，"
                    f"{backoff:.0f}s 后重试（外来进程请手动处理或改 FAWAI_GATEWAY_PORT）"
                )
                deadline = time.monotonic() + backoff
                while time.monotonic() < deadline and not stopping:
                    time.sleep(0.2)
                backoff = min(_MAX_BACKOFF, backoff * 2)
                continue

        cprint("[keeper] 启动会话网关…")
        with child_lock:
            child = _spawn_gateway(extra_args, log_fh=child_log_fh)
            proc = child
        if child_log_fh is not None:
            _start_child_tee(proc, child_log_fh)
        # 拉起窗口若已有代码变更：立刻停掉本轮，避免带着旧命中标记空跑
        if code_reload_hit.is_set() and not stopping:
            _stop_child(wait=False)
        code = proc.wait()
        with child_lock:
            if child is proc:
                child = None
        if stopping:
            break

        ran = time.monotonic() - started
        cmd = store.read_command()
        action = (cmd or {}).get("action") if cmd else None

        if code_reload_hit.is_set() and action not in {"activate", "restart"}:
            cprint("[keeper] 代码变更重启，立即拉起")
            code_reload_hit.clear()
            backoff = _MIN_BACKOFF
            continue

        if code == EXIT_ACTIVATE or action == "activate":
            release_id = str((cmd or {}).get("release_id") or "")
            if not release_id:
                cprint("[keeper] 激活请求缺少 release_id，忽略并重启")
                store.clear_command()
            else:
                try:
                    cprint(f"[keeper] 激活存档 {release_id} …")
                    active = store.apply_activate(release_id)
                    cprint(f"[keeper] 已激活；自动备份={active.get('backup_release_id')}")
                except Exception as exc:  # noqa: BLE001
                    cprint(f"[keeper] 激活失败: {exc}")
                    store.clear_command()
            code_reload_hit.clear()
            backoff = _MIN_BACKOFF
            continue

        if code == EXIT_RESTART or action == "restart":
            cprint("[keeper] 收到重启请求，立即拉起")
            store.clear_command()
            code_reload_hit.clear()
            backoff = _MIN_BACKOFF
            continue

        # 非热重载退出才清标记，避免「拉起瞬间的变更」被开局 clear 吃掉
        code_reload_hit.clear()
        if ran >= 20:
            backoff = _MIN_BACKOFF
        # 异常退出：摘 traceback 写入 last_crash，供 Web 一键修复
        if code not in (0, EXIT_RESTART, EXIT_ACTIVATE):
            try:
                if child_log_fh is not None:
                    child_log_fh.flush()
                snap = read_console_log(data_root, max_lines=400, max_chars=120000)
                excerpt = extract_crash_excerpt(snap.get("text") or "")
                if excerpt:
                    write_last_crash(excerpt, data_dir=data_root)
            except Exception as exc:  # noqa: BLE001
                cprint(f"[keeper] 写入崩溃日志失败: {exc}")
        cprint(f"[keeper] 子进程退出 code={code}，{backoff:.0f}s 后重拉")
        deadline = time.monotonic() + backoff
        while time.monotonic() < deadline and not stopping:
            time.sleep(0.2)
        backoff = min(_MAX_BACKOFF, backoff * 2)

    watch_stop.set()
    if child_log_fh is not None:
        try:
            child_log_fh.close()
        except OSError:
            pass
    cprint("[keeper] 已退出")


def main() -> None:
    from fawai_avatar.config import RuntimeConfig, load_dotenv

    load_dotenv()
    cfg = RuntimeConfig.from_env()
    run_keeper(data_dir=cfg.data_dir)


if __name__ == "__main__":
    main()
