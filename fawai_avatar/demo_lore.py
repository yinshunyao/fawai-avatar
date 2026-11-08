"""Smoke: lore dir, sidecar, knowledge inject, lore_* tools (hybrid engines)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def main(tmp_data: Path) -> None:
    os.environ["FAWAI_DATA_DIR"] = str(tmp_data)
    os.environ["FAWAI_LORE_SIDECAR"] = "1"
    os.environ["FAWAI_LORE_LLM_EXTRACT"] = "0"
    os.environ.pop("FAWAI_LORE_DIR", None)

    from fawai_avatar.config import RuntimeConfig, resolve_lore_dir
    from fawai_avatar.control.background import BackgroundTools
    from fawai_avatar.lore import lore_prompt_block, open_lore_store
    from fawai_avatar.lore_sidecar import observe_lore_event
    from fawai_avatar.memory_settings import (
        KEY_SIDECAR,
        KEY_WORK_ROOT,
        get_memory_store,
        reset_memory_store_cache,
    )
    from fawai_avatar.types import Action

    reset_memory_store_cache()
    # 冒烟显式打开旁路；产品默认关
    get_memory_store(data_dir=str(tmp_data)).set_many(
        {KEY_WORK_ROOT: str(tmp_data), KEY_SIDECAR: "1"}
    )
    reset_memory_store_cache()

    cfg = RuntimeConfig.from_env()
    assert Path(cfg.lore_dir) == tmp_data / "lore"
    assert cfg.lore_sidecar is True
    assert cfg.lore_llm_extract is False

    custom = tmp_data / "custom-lore"
    os.environ["FAWAI_LORE_DIR"] = str(custom)

    observe_lore_event(
        text="记住 CUB 数据集放在 /Volumes/shunyao-h1/datasets/cub",
        event_kind="user_text",
        blocking=True,
    )
    observe_lore_event(
        text="默认编辑器是 cursor",
        event_kind="user_text",
        blocking=True,
    )

    tools = BackgroundTools(work_root=tmp_data, data_dir=tmp_data, auto_version=False)
    # 笔记进主搜；旁路 KV 仍可写，但不进 lore_search / 注入
    r4 = tools.execute(
        Action(
            kind="lore_note_upsert",
            args={
                "id": "cub-layout",
                "title": "CUB 数据",
                "body": "CUB 数据集放在 /Volumes/shunyao-h1/datasets/cub；训练用 images。",
                "tags": ["dataset", "cub"],
                "related_keys": ["dataset.cub.root"],
            },
        )
    )
    assert r4.ok, r4

    # FTS 为词 AND；勿用库外字（如「哪」）否则空命中
    block = lore_prompt_block("CUB")
    assert "知识检索" in block or "笔记" in block, block
    assert "cub" in block.lower(), block

    r = tools.execute(Action(kind="lore_search", args={"query": "CUB"}))
    assert r.ok and r.data and r.data.get("hits"), r
    assert any(
        (h.get("kind") == "note" and "cub" in str(h.get("id") or "").lower())
        or "cub" in str(h.get("summary") or "").lower()
        for h in (r.data.get("hits") or [])
    ), r.data

    r2 = tools.execute(Action(kind="lore_get", args={"key": "cub-layout"}))
    assert r2.ok and r2.data and r2.data.get("kind") == "note", r2

    # 工作区文件 → 文件名/正文引擎
    sample = tmp_data / "notes" / "cub-readme.md"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("# CUB\nDataset root documented here.\n", encoding="utf-8")
    r_file = tools.execute(Action(kind="lore_search", args={"query": "CUB Dataset"}))
    assert r_file.ok, r_file

    r3 = tools.execute(
        Action(
            kind="lore_set",
            args={"key": "pref.theme", "value": "dark", "aliases": ["暗色"]},
        )
    )
    assert r3.ok, r3
    r5 = tools.execute(Action(kind="lore_buffer_list", args={"limit": 10}))
    assert r5.ok, r5

    lore = open_lore_store(custom)
    assert lore.kv.get("pref.theme") is not None
    assert resolve_lore_dir().endswith("custom-lore") or "custom-lore" in resolve_lore_dir()
    print(f"[PASS] lore hybrid search/get → {custom}")


if __name__ == "__main__":
    TMP = Path(tempfile.mkdtemp(prefix="fawai-lore-"))
    try:
        main(TMP)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
