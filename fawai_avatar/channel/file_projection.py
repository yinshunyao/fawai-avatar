# term:修改与产物浏览  doc:doc/功能/02-任务线/06-修改与产物浏览/说明.md
"""交付物浏览器投影：主机生成 .preview.html（可选 soffice→PDF）。"""

from __future__ import annotations

import html
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_OFFICE = frozenset({".pptx", ".docx", ".xlsx"})
_SLIDE_CAP = 40
_TABLE_ROWS = 80
_TABLE_COLS = 24
_TEXT_CAP = 200_000


def projection_sidecar(path: Path, *, kind: str = "html") -> Path:
    suf = ".preview.pdf" if kind == "pdf" else ".preview.html"
    return path.with_name(path.name + suf)


def _xml_texts(root: ET.Element) -> list[str]:
    out: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag
        if tag in {"t", "a:t"} or tag.endswith("}t"):
            s = (el.text or "").strip()
            if s:
                out.append(s)
    return out


def _html_shell(title: str, body: str, *, note: str = "") -> str:
    note_html = (
        f'<p class="note">{html.escape(note)}</p>' if note else ""
    )
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
        f"<title>{html.escape(title)}</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:16px;"
        "background:#0f1115;color:#e8eaed;line-height:1.5}"
        "h1{font-size:1.1rem;margin:0 0 12px}"
        ".note{opacity:.7;font-size:.85rem;margin:0 0 16px}"
        ".slide{border:1px solid #2a2f3a;border-radius:8px;padding:12px 14px;"
        "margin:0 0 12px;background:#161a22}"
        ".slide h2{font-size:.95rem;margin:0 0 8px;color:#9db4ff}"
        "pre,p{white-space:pre-wrap;word-break:break-word;margin:0}"
        "table{border-collapse:collapse;width:100%;font-size:.85rem}"
        "td,th{border:1px solid #2a2f3a;padding:4px 8px;text-align:left}"
        "th{background:#1c2230}"
        "</style></head><body>"
        f"<h1>{html.escape(title)}</h1>{note_html}{body}</body></html>"
    )


def _project_pptx(path: Path) -> str:
    slides_html: list[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        names = sorted(
            n
            for n in zf.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
        for i, name in enumerate(names[:_SLIDE_CAP], start=1):
            root = ET.fromstring(zf.read(name))
            texts = _xml_texts(root)
            body = html.escape("\n".join(texts).strip() or "（空幻灯片）")
            slides_html.append(
                f'<section class="slide"><h2>幻灯片 {i}</h2><pre>{body}</pre></section>'
            )
    note = "投影（HTML 摘录版式）；版式与动画非像素级一致。下载原件可另存。"
    return _html_shell(path.name, "\n".join(slides_html) or "<p>（无幻灯片）</p>", note=note)


def _project_docx(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    paras: list[str] = []
    for p in root.iter():
        tag = p.tag.rsplit("}", 1)[-1] if "}" in p.tag else p.tag
        if tag != "p":
            continue
        bits = _xml_texts(p)
        if bits:
            paras.append(html.escape("".join(bits)))
    body = "".join(f"<p>{t}</p>" for t in paras) or "<p>（空文档）</p>"
    note = "投影（HTML）；复杂排版可能简化。下载原件可另存。"
    return _html_shell(path.name, body, note=note)


def _project_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sroot:
                bits = [
                    (t.text or "")
                    for t in si.iter()
                    if (t.tag.rsplit("}", 1)[-1] if "}" in t.tag else t.tag) == "t"
                ]
                shared.append("".join(bits))
        sheets = [
            n
            for n in zf.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        ]
        parts: list[str] = []
        for sn in sheets[:6]:
            root = ET.fromstring(zf.read(sn))
            rows: list[list[str]] = []
            cur_row = -1
            row_cells: list[str] = []
            for c in root.iter():
                tag = c.tag.rsplit("}", 1)[-1] if "}" in c.tag else c.tag
                if tag == "c":
                    r_attr = c.attrib.get("r") or ""
                    # 行号：A1 → 1
                    digits = "".join(ch for ch in r_attr if ch.isdigit())
                    rnum = int(digits) if digits.isdigit() else cur_row
                    if rnum != cur_row and row_cells:
                        rows.append(row_cells[:_TABLE_COLS])
                        row_cells = []
                        if len(rows) >= _TABLE_ROWS:
                            break
                    cur_row = rnum
                    t = c.attrib.get("t")
                    v_el = None
                    for child in c:
                        ct = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
                        if ct == "v":
                            v_el = child
                            break
                    val = (v_el.text if v_el is not None else "") or ""
                    if t == "s" and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    row_cells.append(val)
            if row_cells and len(rows) < _TABLE_ROWS:
                rows.append(row_cells[:_TABLE_COLS])
            if not rows:
                continue
            trs = []
            for i, row in enumerate(rows):
                cells = "".join(
                    f"<{'th' if i == 0 else 'td'}>{html.escape(str(cell))}</{'th' if i == 0 else 'td'}>"
                    for cell in row
                )
                trs.append(f"<tr>{cells}</tr>")
            parts.append(
                f'<section class="slide"><h2>{html.escape(Path(sn).name)}</h2>'
                f"<table>{''.join(trs)}</table></section>"
            )
    note = "投影（HTML 表）；大表已截断。下载原件可另存。"
    return _html_shell(path.name, "\n".join(parts) or "<p>（空表）</p>", note=note)


def _project_markdown(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")[:_TEXT_CAP]
    # 预览优先走前端 Markdown；旁路 HTML 仅作无 JS 降级（pre 包裹）
    body = f"<pre>{html.escape(raw)}</pre>"
    note = "Markdown 投影降级（纯文本）；会话预览优先用渲染器。"
    return _html_shell(path.name, body, note=note)


def _try_soffice_pdf(path: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    out_dir = path.parent
    target = projection_sidecar(path, kind="pdf")
    if target.is_file() and target.stat().st_mtime >= path.stat().st_mtime:
        return target
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            check=False,
            capture_output=True,
            timeout=90,
        )
        # LibreOffice 默认输出 stem.pdf
        cand = out_dir / f"{path.stem}.pdf"
        if cand.is_file():
            if cand.resolve() != target.resolve():
                target.write_bytes(cand.read_bytes())
            return target if target.is_file() else cand
    except Exception:  # noqa: BLE001 — 可选投影失败不挡交付
        return None
    return target if target.is_file() else None


def ensure_projection(path: Path | str) -> dict[str, Any]:
    """为办公/文档生成旁路投影；返回可写入 meta 的字段。"""
    p = Path(path)
    info: dict[str, Any] = {}
    if not p.is_file():
        return info
    suf = p.suffix.lower()

    # 可选 PDF（有 LibreOffice 才试）
    if suf in _OFFICE:
        pdf = _try_soffice_pdf(p)
        if pdf and pdf.is_file():
            info["projection_path"] = str(pdf.resolve())
            info["projection_kind"] = "pdf"
            info["projection_mime"] = "application/pdf"
            return info

    html_path = projection_sidecar(p, kind="html")
    need = True
    if html_path.is_file():
        try:
            need = html_path.stat().st_mtime + 0.05 < p.stat().st_mtime
        except OSError:
            need = True
    try:
        if need:
            if suf == ".pptx":
                html_path.write_text(_project_pptx(p), encoding="utf-8")
            elif suf == ".docx":
                html_path.write_text(_project_docx(p), encoding="utf-8")
            elif suf == ".xlsx":
                html_path.write_text(_project_xlsx(p), encoding="utf-8")
            elif suf == ".md":
                html_path.write_text(_project_markdown(p), encoding="utf-8")
            else:
                return info
        if html_path.is_file():
            info["projection_path"] = str(html_path.resolve())
            info["projection_kind"] = "html"
            info["projection_mime"] = "text/html; charset=utf-8"
            info["projection_note"] = "html_extract"
    except Exception as exc:  # noqa: BLE001
        info["projection_error"] = str(exc)[:200]
    return info
