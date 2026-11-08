# term:个人知识库  doc:doc/术语与称呼索引.md
"""Open fawai_lore + planner retrieve block (code in fawai_lore/, data under FAWAI_LORE_DIR)."""

from __future__ import annotations

from pathlib import Path

from fawai_avatar.config import RuntimeConfig, lore_vector_enabled, resolve_lore_dir


def open_lore_store(lore_dir: str | Path | None = None):
    """Return a ``fawai_lore.LoreStore`` for KV + notes."""
    from fawai_lore import open_lore

    root = Path(lore_dir) if lore_dir is not None else Path(resolve_lore_dir())
    return open_lore(root)


def lore_dir_from_config(cfg: RuntimeConfig | None = None) -> Path:
    cfg = cfg or RuntimeConfig.from_env()
    return Path(cfg.lore_dir).expanduser()


def lore_prompt_block(goal: str = "", *, limit: int = 8, max_chars: int = 1800) -> str:
    """规划注入：工作区索引 ∪ 笔记；不再灌旁路 KV 搜。"""
    try:
        from fawai_avatar.knowledge_search import knowledge_prompt_block

        return knowledge_prompt_block(goal or "", limit=limit, max_chars=max_chars)
    except Exception:
        return ""
