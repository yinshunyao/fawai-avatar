# term:任务产物交付  doc:doc/功能/任务产物交付/说明.md
"""产物登记 / 办公摘录 / file 信封冒烟。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from fawai_avatar.channel.file_deliver import (
    collect_py_run_deliverables,
    describe_file,
    extract_office_preview,
    message_for_deliverable,
)
from fawai_avatar.channel.message import Message


def _minimal_pptx(path: Path) -> None:
    """写一份可被 zip+xml 解析的最小 pptx（仅 slide 文本）。"""
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    sld = Element("{http://schemas.openxmlformats.org/presentationml/2006/main}sld")
    sp_tree = SubElement(sld, "{http://schemas.openxmlformats.org/presentationml/2006/main}cSld")
    sp = SubElement(sp_tree, "{http://schemas.openxmlformats.org/presentationml/2006/main}spTree")
    tx = SubElement(sp, f"{{{ns}}}t")
    tx.text = "HelloSlide"
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
    pptx = root / "demo.pptx"
    _minimal_pptx(pptx)
    prev = extract_office_preview(pptx)
    assert "HelloSlide" in str(prev.get("preview_text") or ""), prev
    assert prev.get("slides"), prev

    info = describe_file(pptx)
    assert info["preview_kind"] == "office"
    assert info["filename"] == "demo.pptx"

    msg = message_for_deliverable(info, task_id="t1")
    assert isinstance(msg, Message)
    assert msg.kind == "file"
    assert msg.meta.get("filename") == "demo.pptx"

    outs = collect_py_run_deliverables(
        session_root=root,
        result_abs=None,
        stdout=f"OK: {pptx}\n",
        started_mtime=0,
    )
    assert any(d.get("filename") == "demo.pptx" for d in outs), outs

    print(json.dumps({"ok": True, "slides": len(prev.get("slides") or [])}, ensure_ascii=False))


if __name__ == "__main__":
    import tempfile

    TMP = tempfile.mkdtemp(prefix="fawai-file-deliver-")
    main(TMP)
