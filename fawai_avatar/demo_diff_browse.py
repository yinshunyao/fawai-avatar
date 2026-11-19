# term:修改与产物浏览  doc:doc/功能/02-任务线/06-修改与产物浏览/说明.md
"""Diff 全文落盘 / 统计 / 投影冒烟。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fawai_avatar.channel.file_deliver import describe_file
from fawai_avatar.channel.file_projection import ensure_projection
from fawai_avatar.workspace.diff_store import DiffStore
from fawai_avatar.workspace.pending_diff import diff_line_stats, unify_diff_bundle


def _minimal_pptx(path: Path) -> None:
    import zipfile
    from xml.etree.ElementTree import Element, SubElement, tostring

    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    sld = Element("{http://schemas.openxmlformats.org/presentationml/2006/main}sld")
    sp_tree = SubElement(sld, "{http://schemas.openxmlformats.org/presentationml/2006/main}cSld")
    sp = SubElement(sp_tree, "{http://schemas.openxmlformats.org/presentationml/2006/main}spTree")
    tx = SubElement(sp, f"{{{ns}}}t")
    tx.text = "ProjectionSlide"
    slide_xml = tostring(sld, encoding="utf-8", xml_declaration=True)
    content_types = (
        b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>"""
        b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">"""
        b"""<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>"""
        b"""<Default Extension="xml" ContentType="application/xml"/>"""
        b"""<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>"""
        b"""</Types>"""
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("ppt/slides/slide1.xml", slide_xml)


def main(tmp: str) -> None:
    root = Path(tmp)
    root.mkdir(parents=True, exist_ok=True)

    old = "a\n" * 5 + "old line\n" + "b\n" * 5
    new = "a\n" * 5 + "new line\n" + "b\n" * 5 + ("x\n" * 100)
    bundle = unify_diff_bundle("demo.py", old, new, preview_lines=20)
    assert bundle["added"] >= 1 and bundle["deleted"] >= 1, bundle
    assert bundle["diff_full"]
    assert "truncated" in bundle

    store = DiffStore(root)
    rec = store.put(
        task_id="t-demo",
        path="demo.py",
        diff=str(bundle["diff_full"]),
        added=int(bundle["added"]),
        deleted=int(bundle["deleted"]),
        kind="fs_patch",
    )
    got = store.get("t-demo", rec["id"])
    assert got and got.get("diff") == bundle["diff_full"]
    listed = store.list_for_task("t-demo")
    assert any(x.get("id") == rec["id"] for x in listed)
    assert "diff" not in listed[0]

    stats = diff_line_stats(str(bundle["diff_full"]))
    assert stats["added"] == bundle["added"]

    pptx = root / "demo.pptx"
    _minimal_pptx(pptx)
    proj = ensure_projection(pptx)
    assert proj.get("projection_kind") in {"html", "pdf"}, proj
    assert Path(str(proj["projection_path"])).is_file()
    html = Path(str(proj["projection_path"])).read_text(encoding="utf-8")
    assert "ProjectionSlide" in html or proj.get("projection_kind") == "pdf"

    info = describe_file(pptx)
    assert info.get("projection_path") or info.get("preview_text")

    md = root / "note.md"
    md.write_text("# Hello\n\nworld\n", encoding="utf-8")
    md_info = describe_file(md)
    assert md_info.get("text_format") == "markdown"
    assert "Hello" in str(md_info.get("preview_text") or "")

    print(
        json.dumps(
            {
                "ok": True,
                "diff_id": rec["id"],
                "added": bundle["added"],
                "deleted": bundle["deleted"],
                "projection_kind": proj.get("projection_kind"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    TMP = tempfile.mkdtemp(prefix="fawai-diff-browse-")
    main(TMP)
