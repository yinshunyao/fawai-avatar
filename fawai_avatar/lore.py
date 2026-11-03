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
    """Short lore snippet for planner system prompt (zh/en query = goal)."""
    try:
        from fawai_lore import retrieve_prompt_block

        cfg = RuntimeConfig.from_env()
        use_vector = bool(cfg.lore_vector) and lore_vector_enabled()
        embed_fn = None
        if use_vector:
            try:
                from fawai_avatar.lore_embed import make_embed_fn

                embed_fn = make_embed_fn()
            except Exception:
                use_vector = False
        return retrieve_prompt_block(
            cfg.lore_dir,
            goal or "",
            limit=limit,
            max_chars=max_chars,
            embed_fn=embed_fn,
            use_vector=use_vector,
        )
    except Exception:
        return ""
