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
from fawai_avatar.runtime.reload_gate import has_active_executing_tasks
from fawai_avatar.runtime.releases import (
    EXIT_ACTIVATE,
    EXIT_RESTART,
    package_dir,
    package_root,
    releases_from_env,
)

# 热重载延后等候：忙时轮询间隔 / 节流日志间隔（秒）
_RELOAD_DEFER_POLL_S = 1.0
_RELOAD_DEFER_LOG_S = 15.0

# 崩溃后退避（秒）；成功跑过后重置
_MIN_BACKOFF = 1.0
_MAX_BACKOFF = 30.0


def _child_env() -> dict[str, str]:
    """子进程环境：复制外层后用磁盘 ``.env`` 覆盖。

    Web「模型配置 / 系统配置」只热更**子进程**并写盘；外层 ``os.environ``
    仍可能停留在启动旧值。若不覆盖，重启后子进程会继承旧模型等键，
    表现为「设成 qwen3.7 重启又变回 qwen3.8」。
    """
    from fawai_avatar.config import overlay_dotenv

    env = os.environ.copy()
    overlay_dotenv(env)
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
    reclaim_on = port_reclaim_enabled()
    listen_port = gateway_port_from_env()
    cprint("=" * 60)
    cprint("fawai-avatar 服务保活")
    cprint(f"  工程根: {package_root()}")
    cprint(f"  存档目录: {store.releases_dir}")
    cprint(f"  网关端口: {listen_port}（FAWAI_GATEWAY_PORT）")
    cprint(f"  后台日志: {data_root}/runtime/console.log")
    cprint("  子进程崩溃或主动重启后会自动拉起；Ctrl+C 停止外层。")
    if code_reload_enabled():
        cprint(
            f"  代码热重载: 开（监视 {package_dir()} 下 .py/.html/.js/.css；"
            "执行中任务延后；Web「服务与版本」或 FAWAI_CODE_RELOAD 可关）"
        )
    else:
        cprint(
            "  代码热重载: 关（默认；Web「服务与版本」可开，或 FAWAI_CODE_RELOAD=1）"
        )
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

    defer_log_at = 0.0
    reload_stop_sent = False

    def _apply_code_reload_stop(*, reason: str) -> None:
        """空闲时发 SIGTERM；有执行中任务则只记命中、不杀进程。"""
        nonlocal defer_log_at, reload_stop_sent
        if stopping:
            return
        if not code_reload_enabled():
            # 开关关闭：丢弃待生效命中，避免之后再开立刻误重启
            code_reload_hit.clear()
            reload_stop_sent = False
            return
        code_reload_hit.set()
        if has_active_executing_tasks(data_root):
            reload_stop_sent = False
            now = time.monotonic()
            if now - defer_log_at >= _RELOAD_DEFER_LOG_S:
                cprint(
                    f"[keeper] {reason}；有执行中任务（running/blocked），"
                    "热重载延后到任务完成或取消"
                )
                defer_log_at = now
            return
        if reload_stop_sent:
            return
        reload_stop_sent = True
        cprint(f"[keeper] {reason} → 重启网关")
        # 只发信号，不在监视/等候线程里 wait / child=None
        _stop_child(wait=False)

    def _on_code_change(paths: list[str]) -> None:
        if stopping:
            return
        if not code_reload_enabled():
            return
        sample = paths[0] if paths else "?"
        more = f" 等 {len(paths)} 个文件" if len(paths) > 1 else ""
        _apply_code_reload_stop(reason=f"检测到代码变更: {sample}{more}")

    def _deferred_reload_loop() -> None:
        """已记热重载命中且任务仍忙时轮询；空闲后再停子进程。"""
        nonlocal reload_stop_sent
        while not watch_stop.wait(_RELOAD_DEFER_POLL_S):
            if stopping:
                return
            if not code_reload_enabled():
                code_reload_hit.clear()
                reload_stop_sent = False
                continue
            if not code_reload_hit.is_set():
                reload_stop_sent = False
                continue
            _apply_code_reload_stop(
                reason=(
                    "热重载仍待生效"
                    if has_active_executing_tasks(data_root)
                    else "任务已空闲，应用延后的代码热重载"
                )
            )

    def _on_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        watch_stop.set()
        cprint(f"保活收到信号 {signum}，正在停止…")
        _stop_child(wait=True)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    # 始终挂监视；是否真重启由 code_reload_enabled() 实时读 .env / 环境决定
    # （Web「服务与版本」开关即时生效，不必重启保活外层）
    start_code_watch([package_dir()], _on_code_change, stop_event=watch_stop)
    threading.Thread(
        target=_deferred_reload_loop,
        name="fawai-reload-defer",
        daemon=True,
    ).start()

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

    load_dotenv(override=True)
    cfg = RuntimeConfig.from_env()
    run_keeper(data_dir=cfg.data_dir)


if __name__ == "__main__":
    main()
