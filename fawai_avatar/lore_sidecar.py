# term:个人知识库  doc:doc/术语与称呼索引.md
"""Always-on lore sidecar hooks (async thread; optional LLM extract)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fawai_avatar.config import (
    RuntimeConfig,
    lore_auto_p1_kv,
    lore_llm_extract_enabled,
    lore_sidecar_enabled,
    resolve_lore_dir,
)

logger = logging.getLogger(__name__)


def _llm_propose_factory():
    if not lore_llm_extract_enabled():
        return None
    try:
        from fawai_avatar.llm.client import LlmClient
        from fawai_lore.llm_extract import make_openai_compatible_chat_fn, propose_with_llm

        client = LlmClient()
        if not client.config.api_key:
            return None
        chat_fn = make_openai_compatible_chat_fn(client)

        def _propose(text: str, *, goal: str = ""):
            return propose_with_llm(text, chat_fn=chat_fn, goal=goal, merge_rules=False)

        return _propose
    except Exception as exc:  # noqa: BLE001
        logger.warning("lore llm extract unavailable: %s", exc)
        return None


def _safe_process(**kwargs: Any) -> None:
    try:
        from fawai_lore import process_sidecar_event

        cfg = RuntimeConfig.from_env()
        root = resolve_lore_dir(data_dir=cfg.data_dir, lore_dir=cfg.lore_dir)
        kwargs = dict(kwargs)
        kwargs.setdefault("llm_propose", _llm_propose_factory())
        kwargs.setdefault("auto_p1_kv", lore_auto_p1_kv(default=False))
        process_sidecar_event(root, **kwargs)
    except Exception as exc:  # noqa: BLE001 — sidecar must not affect tasks
        logger.warning("lore sidecar failed: %s", exc)


def observe_lore_event(
    *,
    text: str = "",
    goal: str = "",
    final_thought: str = "",
    event_kind: str = "text",
    task_id: str = "",
    blocking: bool = False,
) -> None:
    """Feed an event to fawai_lore sidecar. Default: background thread."""
    if not lore_sidecar_enabled():
        return
    kwargs = {
        "text": text,
        "goal": goal,
        "final_thought": final_thought,
        "event_kind": event_kind,
        "task_id": task_id or "",
    }
    if blocking:
        _safe_process(**kwargs)
        return
    threading.Thread(
        target=_safe_process,
        kwargs=kwargs,
        name="fawai-lore-sidecar",
        daemon=True,
    ).start()
