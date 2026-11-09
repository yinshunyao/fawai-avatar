# term:任务产物交付  doc:doc/功能/任务产物交付/说明.md
"""会话产物：登记交付物、办公摘录预览、出站 file 气泡。"""

from __future__ import annotations

import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fawai_avatar.channel.message import Message

# 交付物扩展（脚本源码不算产物）
_DELIVER_SUFFIXES = frozenset(
    {
        ".pdf",
        ".pptx",
        ".ppt",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".mp4",
        ".webm",
        ".mov",
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".zip",
        ".txt",
        ".md",
        ".html",
        ".json",
    }
)

_OK_PATH_RE = re.compile(
    r"(?im)^(?:OK|Saved|Wrote|输出|已保存|saved to|wrote)\s*[:：]\s*(.+?)\s*$"
)

_PREVIEW_TEXT_CAP = 12000
_SLIDE_CAP = 40


def guess_mime(path: str | Path) -> str:
    name = Path(path).name
    mime, _ = mimetypes.guess_type(name)
    if mime:
        return mime
    suf = Path(name).suffix.lower()
    return {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".doc": "application/msword",
        ".xls": "application/vnd.ms-excel",
    }.get(suf, "application/octet-stream")


def preview_kind_for(path: str | Path, mime: str | None = None) -> str:
    suf = Path(path).suffix.lower()
    m = (mime or guess_mime(path)).lower()
    if m.startswith("image/") or suf in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if m.startswith("audio/") or suf in {".wav", ".mp3", ".m4a", ".aac", ".ogg"}:
        return "audio"
    if m.startswith("video/") or suf in {".mp4", ".webm", ".mov"}:
        return "video"
    if m == "application/pdf" or suf == ".pdf":
        return "pdf"
    if suf in {".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls"}:
        return "office"
    if m.startswith("text/") or suf in {".txt", ".md", ".csv", ".json", ".html"}:
        return "text"
    return "binary"


def is_deliverable_path(path: Path, *, session_root: Path | None = None) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in _DELIVER_SUFFIXES:
        return False
    # 跳过 runs/ 日志
    parts = {p.lower() for p in path.parts}
    if "runs" in parts and path.suffix.lower() in {".txt", ".json"}:
        return False
    if session_root is not None:
        try:
            path.resolve().relative_to(session_root.resolve())
        except ValueError:
            # 仓内/用户指定目录的产物也可交付（跨端仍拷到 media）
            pass
    return True


def _xml_texts(root: ET.Element) -> list[str]:
    out: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag
        if tag in {"t", "a:t"} or tag.endswith("}t"):
            s = (el.text or "").strip()
            if s:
                out.append(s)
        elif el.text and tag in {"t"}:
            s = el.text.strip()
            if s:
                out.append(s)
    return out


def extract_office_preview(path: Path) -> dict[str, Any]:
    """从常见办公包抽出可浏览器展示的短文案（stdlib zip+xml）。"""
    suf = path.suffix.lower()
    meta: dict[str, Any] = {"preview_kind": "office", "filename": path.name}
    try:
        if suf == ".pptx":
            slides: list[dict[str, Any]] = []
            with zipfile.ZipFile(path, "r") as zf:
                names = sorted(
                    n
                    for n in zf.namelist()
                    if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                )
                for i, name in enumerate(names[:_SLIDE_CAP], start=1):
                    raw = zf.read(name)
                    root = ET.fromstring(raw)
                    texts = _xml_texts(root)
                    body = "\n".join(texts).strip()
                    slides.append({"index": i, "text": body[:2000]})
            meta["slides"] = slides
            meta["preview_text"] = "\n\n".join(
                f"幻灯片 {s['index']}\n{s['text']}" for s in slides if s.get("text")
            )[:_PREVIEW_TEXT_CAP]
            return meta
        if suf == ".docx":
            with zipfile.ZipFile(path, "r") as zf:
                raw = zf.read("word/document.xml")
            root = ET.fromstring(raw)
            texts = _xml_texts(root)
            meta["preview_text"] = "\n".join(texts)[:_PREVIEW_TEXT_CAP]
            return meta
        if suf == ".xlsx":
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
                lines: list[str] = []
                for sn in sheets[:8]:
                    root = ET.fromstring(zf.read(sn))
                    row_bits: list[str] = []
                    for c in root.iter():
                        tag = c.tag.rsplit("}", 1)[-1] if "}" in c.tag else c.tag
                        if tag != "v":
                            continue
                        val = (c.text or "").strip()
                        if not val:
                            continue
                        # shared string index
                        parent = None
                        # 简化：数字原样；若像索引且在 shared 内则解引用
                        if val.isdigit() and int(val) < len(shared):
                            # 可能是共享串，也可能是真数字——xlsx 用 t="s" 区分；此处宽松展示
                            row_bits.append(shared[int(val)] if shared else val)
                        else:
                            row_bits.append(val)
                        if len(row_bits) >= 40:
                            break
                    if row_bits:
                        lines.append(" · ".join(row_bits[:24]))
                meta["preview_text"] = "\n".join(lines)[:_PREVIEW_TEXT_CAP]
            return meta
    except Exception as exc:  # noqa: BLE001 — 预览失败不挡下载
        meta["preview_error"] = str(exc)[:200]
    return meta


def extract_text_preview(path: Path, *, cap: int = _PREVIEW_TEXT_CAP) -> str:
    try:
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            return ""
        return raw.decode("utf-8", errors="replace")[:cap]
    except Exception:  # noqa: BLE001
        return ""


def describe_file(path: Path) -> dict[str, Any]:
    mime = guess_mime(path)
    kind = preview_kind_for(path, mime)
    info: dict[str, Any] = {
        "path": str(path.resolve()),
        "filename": path.name,
        "mime": mime,
        "size": int(path.stat().st_size) if path.is_file() else 0,
        "preview_kind": kind,
    }
    if kind == "office":
        info.update({k: v for k, v in extract_office_preview(path).items() if k != "filename"})
    elif kind == "text":
        text = extract_text_preview(path)
        if text:
            info["preview_text"] = text
    return info


def collect_py_run_deliverables(
    *,
    session_root: Path,
    result_abs: str | None,
    stdout: str,
    started_mtime: float | None = None,
) -> list[dict[str, Any]]:
    """从 py_run 结果收集应推送到会话的产物描述。"""
    root = session_root.resolve()
    found: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            rp = p.expanduser().resolve()
        except Exception:  # noqa: BLE001
            return
        key = str(rp)
        if key in seen:
            return
        if not is_deliverable_path(rp, session_root=root):
            return
        seen.add(key)
        found.append(rp)

    if result_abs:
        _add(Path(result_abs))

    for m in _OK_PATH_RE.finditer(stdout or ""):
        raw = m.group(1).strip().strip("\"'")
        if not raw:
            continue
        cand = Path(raw)
        if not cand.is_absolute():
            cand = root / raw
        _add(cand)

    # 会话根下本趟新出现/更新的交付物（不含 runs/、不含 .py）
    try:
        for child in root.rglob("*"):
            if not child.is_file():
                continue
            rel_parts = child.resolve().relative_to(root).parts
            if rel_parts and rel_parts[0] == "runs":
                continue
            if child.suffix.lower() == ".py":
                continue
            if started_mtime is not None:
                try:
                    if child.stat().st_mtime + 0.05 < started_mtime:
                        continue
                except OSError:
                    continue
            _add(child)
    except Exception:  # noqa: BLE001
        pass

    return [describe_file(p) for p in found]


def message_for_deliverable(
    info: dict[str, Any],
    *,
    task_id: str | None = None,
) -> Message:
    path = str(info.get("path") or "")
    filename = str(info.get("filename") or Path(path).name or "file")
    mime = str(info.get("mime") or guess_mime(filename))
    kind = str(info.get("preview_kind") or preview_kind_for(filename, mime))
    size = int(info.get("size") or 0)
    meta: dict[str, Any] = {
        "filename": filename,
        "mime": mime,
        "size": size,
        "preview_kind": kind,
    }
    if info.get("preview_text"):
        meta["preview_text"] = str(info["preview_text"])[:_PREVIEW_TEXT_CAP]
    if info.get("slides"):
        meta["slides"] = info["slides"]
    if info.get("preview_error"):
        meta["preview_error"] = info["preview_error"]
    # 图片仍可用 image 气泡（lightbox）；其它走 file
    if kind == "image":
        return Message.image_out(
            media_path=path or None,
            text=filename,
            task_id=task_id,
        )
    return Message.file_out(
        media_path=path or None,
        text=filename,
        task_id=task_id,
        meta=meta,
    )


def messages_for_deliverables(
    items: list[dict[str, Any]],
    *,
    task_id: str | None = None,
) -> list[Message]:
    out: list[Message] = []
    for info in items:
        if not isinstance(info, dict):
            continue
        out.append(message_for_deliverable(info, task_id=task_id))
    return out
