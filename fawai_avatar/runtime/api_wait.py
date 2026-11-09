# term:会话通道  doc:doc/功能/会话通道/说明.md
"""阻塞等待时的心跳进度（远端 API 或本机长工具；只广播、不落业务结果）。"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


ProgressEmit = Callable[..., None]


class ApiWaitProgress:
    """阻塞调用期间按固定间隔刷新「已等待 Ns」，便于时间线看到未卡死。"""

    def __init__(
        self,
        emit: ProgressEmit,
        *,
        phase: str,
        step: int,
        waiting_text: str,
        interval_sec: float = 2.0,
        cancelled: Callable[[], bool] | None = None,
        mark_api_wait: bool = True,
        **base_extra: Any,
    ) -> None:
        self._emit = emit
        self._phase = phase
        self._step = step
        self._waiting_text = (waiting_text or "等待中…").strip()
        self._interval = max(0.5, float(interval_sec))
        self._cancelled = cancelled
        self._mark_api_wait = bool(mark_api_wait)
        self._base = dict(base_extra)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._push(elapsed_s=0)
        self._thread = threading.Thread(
            target=self._run, name="api-wait-progress", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=self._interval + 0.5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self._cancelled is not None and self._cancelled():
                return
            elapsed = max(0, int(time.monotonic() - self._t0))
            self._push(elapsed_s=elapsed)

    def _push(self, *, elapsed_s: int) -> None:
        text = self._waiting_text
        if elapsed_s > 0:
            text = f"{self._waiting_text} 已等待 {elapsed_s}s"
        extra: dict[str, Any] = {
            **self._base,
            "live": True,
            "wait_elapsed_sec": elapsed_s,
        }
        if self._mark_api_wait:
            extra["api_wait"] = True
        try:
            self._emit(text, phase=self._phase, step=self._step, **extra)
        except Exception:  # noqa: BLE001 — 心跳失败不得打断主流程
            return
