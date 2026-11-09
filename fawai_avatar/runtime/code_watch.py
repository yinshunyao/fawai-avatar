# term:服务保活  doc:doc/功能/服务保活/说明.md
"""监视产品代码变更（仅源码后缀），供保活外层自动重启网关。"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

# 仅视为「代码」：Python 与网关静态资源；不含 .env / data / 存档
_CODE_SUFFIXES = frozenset({".py", ".html", ".js", ".css"})
_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        ".pytest_cache",
        "data",
        "node_modules",
    }
)


def _env_file_path() -> Path:
    """仓库根 ``.env``（与 ``system_settings.env_file_path`` 一致）。"""
    return Path(__file__).resolve().parents[2] / ".env"


def _read_dotenv_key(key: str) -> str | None:
    """从磁盘 .env 读单键；无文件/无键返回 None。"""
    path = _env_file_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        return v.strip().strip("'").strip('"')
    return None


def code_reload_enabled() -> bool:
    """代码热重载是否开启。

    默认**关闭**。优先读磁盘 ``.env``（Web「服务与版本」开关写入处），
    以便保活外层进程立即感知，而不依赖启动时一次性读入的环境变量。
    仅当 ``.env`` 无该键时，才回落进程环境；都没有则关。
    """
    disk = _read_dotenv_key("FAWAI_CODE_RELOAD")
    if disk is not None:
        raw = disk
    elif "FAWAI_CODE_RELOAD" in os.environ:
        # 回落环境：便于无 .env 的调试启动；有磁盘键时绝不以旧环境覆盖
        raw = os.environ.get("FAWAI_CODE_RELOAD") or ""
    else:
        raw = "0"
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def is_code_path(path: Path) -> bool:
    """路径是否属于应触发重载的代码文件。"""
    try:
        parts = set(path.parts)
    except Exception:  # noqa: BLE001
        return False
    if parts & _SKIP_DIR_NAMES:
        return False
    return path.suffix.lower() in _CODE_SUFFIXES


def _filter_changes(changes: Iterable[tuple[object, str]]) -> list[str]:
    out: list[str] = []
    for _change, raw in changes:
        p = Path(raw)
        if is_code_path(p):
            out.append(str(p))
    return out


def start_code_watch(
    roots: Iterable[Path | str],
    on_change: Callable[[list[str]], None],
    *,
    debounce_s: float = 0.6,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """后台线程监视 roots；仅代码后缀变更时回调（已防抖）。

    优先 watchfiles；不可用则退回 mtime 轮询。
    """
    stop = stop_event or threading.Event()
    watch_roots = [Path(r).resolve() for r in roots if Path(r).exists()]

    def _run_watchfiles() -> None:
        import logging

        from watchfiles import watch

        logging.getLogger("watchfiles").setLevel(logging.WARNING)

        def _watch_filter(change: object, path: str) -> bool:  # noqa: ARG001
            return is_code_path(Path(path))

        for changes in watch(
            *watch_roots,
            watch_filter=_watch_filter,
            stop_event=stop,
            debounce=int(max(0.1, debounce_s) * 1000),
            step=200,
            raise_interrupt=False,
        ):
            paths = _filter_changes(changes)
            if paths:
                on_change(paths)

    def _run_poll() -> None:
        stamps: dict[str, float] = {}

        def _scan() -> dict[str, float]:
            found: dict[str, float] = {}
            for root in watch_roots:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
                    for name in filenames:
                        p = Path(dirpath) / name
                        if not is_code_path(p):
                            continue
                        try:
                            found[str(p)] = p.stat().st_mtime
                        except OSError:
                            continue
            return found

        stamps = _scan()
        pending: list[str] = []
        first_change_at = 0.0
        while not stop.wait(0.5):
            now = _scan()
            changed = [
                path
                for path, mtime in now.items()
                if stamps.get(path) != mtime
            ]
            for path in stamps:
                if path not in now:
                    changed.append(path)
            stamps = now
            if changed:
                if not pending:
                    first_change_at = time.monotonic()
                pending.extend(changed)
            # 从「本批第一次变更」起算静默期，避免连写时永远重置导致不触发
            if pending and (time.monotonic() - first_change_at) >= debounce_s:
                uniq = sorted(set(pending))
                pending.clear()
                first_change_at = 0.0
                on_change(uniq)

    def _target() -> None:
        if not watch_roots:
            return
        try:
            _run_watchfiles()
        except Exception as exc:  # noqa: BLE001
            from fawai_avatar.runtime.console_log import cprint

            cprint(f"[keeper] watchfiles 不可用，改用轮询: {exc}")
            _run_poll()

    thread = threading.Thread(target=_target, name="fawai-code-watch", daemon=True)
    thread.start()
    return thread
