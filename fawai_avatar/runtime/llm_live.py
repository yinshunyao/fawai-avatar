# term:会话通道  doc:doc/功能/会话通道/说明.md
"""LLM 规划/反省等待时的节流直播进度（只广播、不落历史）。"""
from __future__ import annotations

import time
from typing import Any, Callable


ProgressEmit = Callable[..., None]


class LlmLiveProgress:
    """把 ``LlmClient.chat(on_delta=…)`` 增量节流成 ``phase`` 同族 progress。"""

    def __init__(
        self,
        emit: ProgressEmit,
        *,
        phase: str,
        step: int,
        min_interval_ms: int = 80,
        min_chars: int = 12,
        preview_limit: int = 4000,
        cancelled: Callable[[], bool] | None = None,
        **base_extra: Any,
    ) -> None:
        self._emit = emit
        self._phase = phase
        self._step = step
        self._min_interval_s = max(0.05, float(min_interval_ms) / 1000.0)
        self._min_chars = max(1, int(min_chars))
        self._preview_limit = max(200, int(preview_limit))
        self._cancelled = cancelled
        self._base = dict(base_extra)
        self._thinking = ""
        self._content = ""
        self._model: str | None = None
        self._thinking_meta: dict[str, Any] | None = None
        self._last_emit = 0.0
        self._last_len = 0
        self._emitted = False

    def on_delta(self, delta: dict[str, Any]) -> None:
        if self._cancelled is not None and self._cancelled():
            return
        if not isinstance(delta, dict):
            return
        thinking = delta.get("thinking")
        content = delta.get("content")
        if isinstance(thinking, str):
            self._thinking = thinking
        if isinstance(content, str):
            self._content = content
        model = delta.get("model")
        if isinstance(model, str) and model.strip():
            self._model = model.strip()
        tmeta = delta.get("thinking_meta")
        if isinstance(tmeta, dict) and tmeta:
            self._thinking_meta = tmeta
        total_len = len(self._thinking) + len(self._content)
        now = time.monotonic()
        grew = total_len - self._last_len
        due = (now - self._last_emit) >= self._min_interval_s
        if self._emitted and grew < self._min_chars and not due:
            return
        self._push(force=False)

    def flush(self) -> None:
        if self._cancelled is not None and self._cancelled():
            return
        if not self._thinking and not self._content and not self._emitted:
            return
        self._push(force=True)

    def preview(self) -> dict[str, str]:
        """收尾进度要带走的思考/输出（已截断），便于刷新后仍能看到。"""
        out: dict[str, str] = {}
        thinking = self._clip(self._thinking).strip()
        content = self._clip(self._content).strip()
        if thinking:
            out["thinking_text"] = thinking
        if content:
            out["content_text"] = content
        if self._model:
            out["model"] = self._model
        return out

    def _push(self, *, force: bool) -> None:
        thinking = self._clip(self._thinking)
        content = self._clip(self._content)
        if not thinking and not content and not force:
            return
        text = "模型输出中…"
        if thinking and not content:
            text = "深度思考中…"
        elif thinking and content:
            text = "深度思考 / 输出中…"
        extra: dict[str, Any] = {
            **self._base,
            "live": True,
            "stream": True,
            "thinking_text": thinking,
            "content_text": content,
        }
        if self._model:
            extra["model"] = self._model
        if self._thinking_meta:
            extra["thinking"] = self._thinking_meta
        try:
            self._emit(text, phase=self._phase, step=self._step, **extra)
        except Exception:  # noqa: BLE001 — 直播失败不得打断规划
            return
        self._last_emit = time.monotonic()
        self._last_len = len(self._thinking) + len(self._content)
        self._emitted = True

    def _clip(self, text: str) -> str:
        raw = text or ""
        if len(raw) <= self._preview_limit:
            return raw
        return raw[: self._preview_limit] + "…"

