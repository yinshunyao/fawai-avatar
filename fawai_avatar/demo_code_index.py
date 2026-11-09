# term:代码索引  doc:doc/功能/代码索引/说明.md
"""代码索引冒烟：多语言 symbols / map / 落盘 / explore 借用 / 跳过目录。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from fawai_avatar.brain.capabilities import mock_plan_for_goal, validate_plan
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action
from fawai_avatar.workspace.code_index import CodeIndex
from fawai_avatar.workspace.explore import explore_search
from fawai_avatar.workspace.paths import WorkspacePathError


def _fail(name: str, detail: str) -> None:
    raise AssertionError(f"{name}: {detail}")


def _seed_tree(work: Path) -> None:
    mod = work / "pkg" / "mod.py"
    mod.parent.mkdir(parents=True, exist_ok=True)
    mod.write_text(
        "class Foo:\n"
        "    def bar(self, x: int) -> str:\n"
        "        return str(x)\n"
        "\n"
        "async def top(a):\n"
        "    return a\n",
        encoding="utf-8",
    )
    ts = work / "pkg" / "util.ts"
    ts.write_text(
        "export class Widget {}\n"
        "export function helper(x: number): number {\n"
        "  return x\n"
        "}\n",
        encoding="utf-8",
    )
    ui = work / "pkg" / "ui.html"
    ui.write_text(
        "<!doctype html><html><head><style>\n"
        "button.send { color: red; }\n"
        "@media (max-width: 640px) { button.send { font-size: 16px; } }\n"
        "</style></head><body>\n"
        "<button id=\"composer\" class=\"send primary\">Go</button>\n"
        "<script>\n"
        "function wireSend() { return 1 }\n"
        "</script>\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    css = work / "pkg" / "theme.css"
    css.write_text(
        "button.stop {\n"
        "  display: grid;\n"
        "}\n",
        encoding="utf-8",
    )
    skipped = work / ".venv" / "lib" / "hidden.py"
    skipped.parent.mkdir(parents=True, exist_ok=True)
    skipped.write_text("def should_not_appear():\n    pass\n", encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fawai-code-index-") as tmp:
        work = Path(tmp) / "work"
        data = Path(tmp) / "data"
        work.mkdir()
        data.mkdir()
        _seed_tree(work)

        print("== CodeIndex direct ==")
        idx = CodeIndex(work, data_dir=data)
        hits = idx.symbols("pkg", query="bar")
        assert any(h["name"] == "bar" and h["kind"] == "method" for h in hits), hits
        ts_hits = idx.symbols("pkg", query="helper")
        assert any(h["name"] == "helper" and h["path"].endswith("util.ts") for h in ts_hits), ts_hits
        widget = idx.symbols("pkg", query="Widget")
        assert any(h["name"] == "Widget" and h["kind"] == "class" for h in widget), widget
        send = idx.symbols("pkg", query="send")
        assert any(
            h["name"] == "button.send" and h["kind"] == "selector" for h in send
        ) or any(h["name"] == "send" and h["kind"] == "class" for h in send), send
        stop = idx.symbols("pkg", query="stop")
        assert any(h["name"] == "button.stop" and h["kind"] == "selector" for h in stop), stop
        media = idx.symbols("pkg", query="@media", kind="media")
        assert any(h["kind"] == "media" for h in media), media
        composer = idx.symbols("pkg", query="composer", kind="id")
        assert any(h["name"] == "composer" and h["kind"] == "id" for h in composer), composer
        wire = idx.symbols("pkg", query="wireSend")
        assert any(h["name"] == "wireSend" for h in wire), wire
        cmap = idx.map(".")
        text = "\n".join(cmap["lines"])
        assert "Foo" in text and "top" in text, cmap
        assert "helper" in text or "Widget" in text, cmap
        assert "should_not_appear" not in text, cmap
        store = data / "code_index"
        assert (store / "meta.json").is_file(), store
        assert (store / "symbols.jsonl").is_file(), store
        print(
            "direct ok",
            json.dumps(
                {
                    "symbols": len(hits),
                    "ts": len(ts_hits),
                    "html_css": len(send) + len(stop),
                    "map": cmap["count"],
                    "engine": cmap.get("engine"),
                },
                ensure_ascii=False,
            ),
        )

        print("== mtime refresh ==")
        util = work / "pkg" / "util.ts"
        time.sleep(0.05)
        util.write_text(
            "export class Widget {}\n"
            "export function helper(x: number): number { return x }\n"
            "export function newerFn(): void {}\n",
            encoding="utf-8",
        )
        idx2 = CodeIndex(work, data_dir=data)
        info = idx2.refresh("pkg")
        newer = idx2.symbols("pkg", query="newerFn")
        assert any(h["name"] == "newerFn" for h in newer), (info, newer)
        print("refresh ok", json.dumps(info, ensure_ascii=False))

        print("== explore borrow ==")
        ex = explore_search(
            work,
            queries=["helper"],
            path="pkg",
            head_limit=20,
            include_symbols=True,
            code_index=idx2,
        )
        kinds = {r.get("kind") for r in ex.get("results") or []}
        assert "grep" in kinds and "symbols" in kinds, ex
        sym_rows = [r for r in ex["results"] if r.get("kind") == "symbols"]
        assert any(r.get("count", 0) > 0 for r in sym_rows), ex
        print("explore ok", json.dumps({"kinds": sorted(kinds), "n": len(ex["results"])}, ensure_ascii=False))

        print("== BackgroundTools ==")
        tools = BackgroundTools(work_root=work, data_dir=data, auto_version=False)
        r1 = tools.execute(Action(kind="code_symbols", args={"path": "pkg", "query": "Foo"}))
        assert r1.ok and r1.data and r1.data.get("symbols"), r1
        r1h = tools.execute(Action(kind="code_symbols", args={"path": "pkg", "query": "send"}))
        assert r1h.ok and r1h.data and r1h.data.get("symbols"), r1h
        r2 = tools.execute(Action(kind="code_map", args={"path": ".", "head_limit": 20}))
        assert r2.ok and r2.data and r2.data.get("lines"), r2
        r3 = tools.execute(Action(kind="code_index_refresh", args={"path": "pkg"}))
        assert r3.ok and r3.data and "updated" in r3.data, r3
        r4 = tools.execute(
            Action(
                kind="explore_search",
                args={
                    "queries": ["bar"],
                    "path": "pkg",
                    "head_limit": 10,
                    "include_symbols": True,
                },
            )
        )
        assert r4.ok and r4.data and r4.data.get("results"), r4
        ex_kinds = {r.get("kind") for r in r4.data["results"]}
        assert "grep" in ex_kinds and "symbols" in ex_kinds, r4
        r4b = tools.execute(
            Action(kind="explore_search", args={"pattern": "bar", "path": "pkg", "head_limit": 10})
        )
        assert r4b.ok and r4b.data and r4b.data.get("results"), r4b
        r4c = tools.execute(
            Action(kind="explore_search", args={"queries": "helper", "path": "pkg", "head_limit": 10})
        )
        assert r4c.ok and r4c.data and r4c.data.get("results"), r4c
        print(
            "background ok",
            json.dumps({"s": r1.message, "h": r1h.message, "m": r2.message, "rf": r3.message}, ensure_ascii=False),
        )

        print("== mock plan ==")
        goal = "生成 fawai_avatar 仓地图"
        plan = mock_plan_for_goal("列符号 query:bar 在 pkg/")
        assert plan.actions and plan.actions[0].kind == "code_symbols", plan.to_dict()
        assert validate_plan("列符号 query:bar 在 pkg/", plan) is None
        plan2 = mock_plan_for_goal(goal)
        assert plan2.actions and plan2.actions[0].kind == "code_map", plan2.to_dict()
        assert validate_plan(goal, plan2) is None
        plan3 = mock_plan_for_goal("刷新索引 pkg/")
        assert plan3.actions and plan3.actions[0].kind == "code_index_refresh", plan3.to_dict()
        print(
            "mock ok",
            json.dumps(
                {
                    "symbols": plan.actions[0].kind,
                    "map": plan2.actions[0].kind,
                    "refresh": plan3.actions[0].kind,
                }
            ),
        )

        print("== skip .venv path ==")
        try:
            idx.symbols(".venv/lib/hidden.py")
            _fail("skip", "expected WorkspacePathError")
        except WorkspacePathError:
            pass
        print("skip ok")

        print("== FAWAI_CODE_INDEX gate ==")
        from fawai_avatar.config import code_index_enabled
        from fawai_avatar.memory_settings import (
            KEY_CODE_INDEX,
            get_memory_store,
            reset_memory_store_cache,
        )

        os.environ["FAWAI_DATA_DIR"] = str(data)
        reset_memory_store_cache()
        store = get_memory_store(data_dir=str(data))
        assert store.code_index_enabled() is True
        store.set_many({KEY_CODE_INDEX: "0"})
        assert KEY_CODE_INDEX not in os.environ, "不得 sync 进 env"
        reset_memory_store_cache()
        os.environ["FAWAI_DATA_DIR"] = str(data)
        assert code_index_enabled() is False
        tools_off = BackgroundTools(work_root=work, data_dir=data, auto_version=False)
        r_off = tools_off.execute(Action(kind="code_symbols", args={"path": "pkg", "query": "Foo"}))
        assert not r_off.ok and (r_off.data or {}).get("disabled"), r_off
        ex_off = explore_search(
            work,
            queries=["helper"],
            path="pkg",
            head_limit=20,
            include_symbols=True,
            code_index=idx2,
        )
        assert any(r.get("disabled") for r in (ex_off.get("results") or [])), ex_off
        get_memory_store().set_many({KEY_CODE_INDEX: "1"})
        assert KEY_CODE_INDEX not in os.environ, "sync 应清掉 NO_ENV 键"
        assert code_index_enabled() is True
        reset_memory_store_cache()
        print("gate ok")

    print("demo_code_index: all ok")


if __name__ == "__main__":
    os.environ.setdefault("FAWAI_BRAIN_MOCK", "1")
    main()
