# term:个人知识库  doc:doc/功能/个人知识库/说明.md
"""记忆配置开关须在搜索时生效（不必重建索引）。"""

from __future__ import annotations

from fawai_avatar.index_policy import (
    METHOD_FTS,
    METHOD_VECTOR,
    default_policy,
    resolve_search_scopes,
    scope_enabled,
)
from fawai_avatar.knowledge_search import _drop_data_dir_file_hits, knowledge_search


def _lore_only_policy() -> dict:
    pol = default_policy()
    pol["work"] = {"methods": []}
    pol["read"] = {"methods": []}
    pol["data"]["businesses"] = {
        "index_cache": {"methods": []},
        "lore": {"methods": [METHOD_FTS, METHOD_VECTOR]},
        "tasks_sessions": {"methods": []},
        "observe": {"methods": []},
        "config": {"methods": []},
        "system_logs": {"methods": []},
        "other": {"methods": []},
    }
    return pol


def main() -> None:
    pol = _lore_only_policy()
    assert scope_enabled(pol, "lore")
    assert not scope_enabled(pol, "work_content")
    assert not scope_enabled(pol, "work_name")
    assert not scope_enabled(pol, "tasks")

    run, skipped = resolve_search_scopes(None, pol)
    assert run == ["lore"], run
    assert skipped == []

    run2, skipped2 = resolve_search_scopes(["work_content", "lore", "tasks"], pol)
    assert run2 == ["lore"], run2
    assert skipped2 == ["work_content", "tasks"], skipped2

    # data_dir 残留命中须丢掉
    from fawai_avatar.memory_index.common import _resolve_data_dir

    dd = _resolve_data_dir()
    assert dd is not None
    rows = _drop_data_dir_file_hits(
        [
            {"id": str(dd / "observe" / "x.txt"), "kind": "file_content"},
            {"id": "/tmp/outside.md", "kind": "file_content"},
        ]
    )
    assert len(rows) == 1 and rows[0]["id"] == "/tmp/outside.md"

    # 用 monkeypatch 政策：仅 lore 时缺省混合不得跑工作区通道
    import fawai_avatar.knowledge_search as ks

    class _Mem:
        def index_policy(self):
            return pol

        def index_policy_payload(self):
            return {"catalog": {"search_menu": []}}

    real_get = None
    try:
        from fawai_avatar import memory_settings as ms

        real_get = ms.get_memory_store
        ms.get_memory_store = lambda **_k: _Mem()  # type: ignore[assignment]
        # 也挡 content/name，避免误开真库
        called: list[str] = []

        def _boom_name(*_a, **_k):
            called.append("name")
            return []

        def _boom_content(*_a, **_k):
            called.append("content")
            return []

        def _notes(query: str, *, limit: int):
            called.append("notes")
            return [
                {
                    "kind": "note",
                    "id": "n1",
                    "score": 2.0,
                    "summary": "note",
                    "source": "lore_fts",
                    "raw": {},
                }
            ]

        ks._name_hits = _boom_name  # type: ignore[assignment]
        ks._content_hits = _boom_content  # type: ignore[assignment]
        ks._note_hits = _notes  # type: ignore[assignment]
        ks._task_hits = lambda *_a, **_k: []  # type: ignore[assignment]
        ks._log_hits = lambda *_a, **_k: []  # type: ignore[assignment]

        out = knowledge_search(query="4090", limit=5, scopes=None)
        assert "name" not in called and "content" not in called, called
        assert "notes" in called
        assert out.get("scopes") == ["lore"]
        assert out.get("count") == 1
        assert (out.get("hits") or [])[0]["kind"] == "note"

        out2 = knowledge_search(query="4090", limit=5, scopes=["work_content"])
        assert out2.get("scopes") == []
        assert out2.get("scopes_skipped_disabled") == ["work_content"]
        assert out2.get("count") == 0
        assert "记忆配置关闭" in str(out2.get("hint") or "")
    finally:
        if real_get is not None:
            from fawai_avatar import memory_settings as ms

            ms.get_memory_store = real_get  # type: ignore[assignment]

    print("demo_knowledge_policy_filter: all ok")


if __name__ == "__main__":
    main()
