# term:会话网关  doc:doc/功能/会话网关/说明.md
# term:服务保活  doc:doc/功能/服务保活/说明.md
"""控制台日志：统一时间戳（uvicorn + print + 应用 logging）+ 落盘供 Web 查看。"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_RUNTIME_DIRNAME = "runtime"
_CONSOLE_FILENAME = "console.log"
_CRASH_FILENAME = "last_crash.log"
_MAX_CONSOLE_BYTES = 2 * 1024 * 1024  # 约 2MB，超出截半

_file_lock = threading.RLock()
_log_file_path: str | None = None
_file_handler: logging.Handler | None = None


def ts() -> str:
    return time.strftime(_DATEFMT)


def runtime_dir(data_dir: str | Path) -> Path:
    root = Path(data_dir).expanduser().resolve()
    path = root / _RUNTIME_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def console_log_path(data_dir: str | Path) -> Path:
    return runtime_dir(data_dir) / _CONSOLE_FILENAME


def last_crash_path(data_dir: str | Path) -> Path:
    return runtime_dir(data_dir) / _CRASH_FILENAME


def _trim_if_needed(path: Path) -> None:
    try:
        if not path.is_file():
            return
        size = path.stat().st_size
        if size <= _MAX_CONSOLE_BYTES:
            return
        raw = path.read_bytes()
        keep = raw[len(raw) // 2 :]
        # 从下一整行开始，避免半行
        nl = keep.find(b"\n")
        if nl >= 0:
            keep = keep[nl + 1 :]
        path.write_bytes(keep)
    except OSError:
        return


def append_console_line(text: str, *, data_dir: str | Path | None = None) -> None:
    """追加一行到控制台落盘（无路径时用已配置路径）。"""
    path = _log_file_path
    if data_dir is not None:
        path = str(console_log_path(data_dir))
    if not path:
        return
    line = text if text.endswith("\n") else text + "\n"
    with _file_lock:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line)
            _trim_if_needed(p)
        except OSError:
            return


def cprint(*args: Any, **kwargs: Any) -> None:
    """带时间戳的 stdout（保活 / 启动横幅）；同步落盘。"""
    kwargs.setdefault("flush", True)
    body = " ".join(str(a) for a in args)
    line = f"{ts()} {body}"
    print(line, **{k: v for k, v in kwargs.items() if k != "file"})
    append_console_line(line)


def write_last_crash(text: str, *, data_dir: str | Path) -> Path:
    """子进程异常退出时写入最近一次崩溃摘录。"""
    path = last_crash_path(data_dir)
    body = (text or "").strip()
    if not body:
        body = "(empty crash output)"
    stamp = ts()
    payload = f"===== crash @ {stamp} =====\n{body}\n"
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    append_console_line(f"{stamp} [keeper] 已写入 last_crash.log", data_dir=data_dir)
    return path


def read_last_crash(data_dir: str | Path, *, max_chars: int = 12000) -> str:
    path = last_crash_path(data_dir)
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    max_chars = max(500, min(int(max_chars or 12000), 80000))
    if len(raw) > max_chars:
        return raw[-max_chars:]
    return raw


def read_console_log(
    data_dir: str | Path,
    *,
    max_lines: int = 200,
    max_chars: int = 80000,
) -> dict[str, Any]:
    """读取后台控制台日志尾部，供 Web 查看。"""
    path = console_log_path(data_dir)
    max_lines = max(20, min(int(max_lines or 200), 2000))
    max_chars = max(2000, min(int(max_chars or 80000), 400000))
    crash = read_last_crash(data_dir)
    if not path.is_file():
        return {
            "ok": True,
            "path": str(path),
            "lines": [],
            "text": "",
            "last_crash": crash,
            "has_crash": bool(crash.strip()),
        }
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "ok": False,
            "path": str(path),
            "lines": [],
            "text": "",
            "last_crash": crash,
            "has_crash": bool(crash.strip()),
            "error": str(exc),
        }
    if len(raw) > max_chars:
        raw = raw[-max_chars:]
        nl = raw.find("\n")
        if nl >= 0:
            raw = raw[nl + 1 :]
    lines = raw.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    text = "\n".join(lines)
    return {
        "ok": True,
        "path": str(path),
        "lines": lines,
        "text": text,
        "last_crash": crash,
        "has_crash": bool(crash.strip()),
    }


class _ConsoleFileHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            append_console_line(msg)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def configure_file_logging(data_dir: str | Path) -> Path:
    """把控制台日志落到 data/runtime/console.log；可重复调用（换路径会换 handler）。"""
    global _log_file_path, _file_handler
    path = console_log_path(data_dir)
    with _file_lock:
        _log_file_path = str(path)
        root = logging.getLogger()
        if _file_handler is not None:
            try:
                root.removeHandler(_file_handler)
                _file_handler.close()
            except Exception:  # noqa: BLE001
                pass
            _file_handler = None
        handler = _ConsoleFileHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt=_DATEFMT,
            )
        )
        root.addHandler(handler)
        _file_handler = handler
    return path


def configure_logging(level: str = "INFO", *, data_dir: str | Path | None = None) -> None:
    """应用 logger 走 stderr，格式含时间戳；可选同时落盘。"""
    lvl = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(lvl)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt=_DATEFMT,
    )
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, _ConsoleFileHandler
            ):
                handler.setFormatter(fmt)
    if data_dir is not None:
        configure_file_logging(data_dir)


def uvicorn_log_config(level: str = "info") -> dict[str, Any]:
    """Uvicorn dictConfig：default / access 均带时间戳。"""
    lvl = str(level or "info").upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "datefmt": _DATEFMT,
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": (
                    '%(asctime)s %(levelprefix)s %(client_addr)s - '
                    '"%(request_line)s" %(status_code)s'
                ),
                "datefmt": _DATEFMT,
                "use_colors": None,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": lvl, "propagate": False},
            "uvicorn.error": {"level": lvl},
            "uvicorn.access": {"handlers": ["access"], "level": lvl, "propagate": False},
        },
    }


def open_child_log_file(data_dir: str | Path):
    """保活子进程 stdout/stderr 追加到同一 console.log。调用方负责 close。"""
    path = console_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 行缓冲文本，崩溃 traceback 也能尽快落盘
    return open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115


def extract_crash_excerpt(log_text: str, *, max_chars: int = 8000) -> str:
    """从控制台尾部抽出最近一次 Traceback 段落。"""
    text = (log_text or "").strip()
    if not text:
        return ""
    marker = "Traceback (most recent call last):"
    idx = text.rfind(marker)
    if idx < 0:
        # 无 traceback 则取尾部
        return text[-max_chars:] if len(text) > max_chars else text
    excerpt = text[idx:]
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n…"
    return excerpt
