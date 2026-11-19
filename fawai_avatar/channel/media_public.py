# term:会话网关  doc:doc/功能/01-接入与呈现/03-会话网关/说明.md
"""跨端 / 远端 VLM 可用的媒体绝对 URL。

浏览器他端：相对 ``/media/{name}`` 已够（同源网关）。
远端 VLM（如 4090 WeVisOne）：须 ``http(s)://公网可达主机/media/{name}``，
不能只塞本机绝对路径；超大 ``data:`` 也可能被反代拒掉。
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def public_base_url() -> str | None:
    """公网或跨端可访问的网关根（无尾斜杠）。

    优先 ``FAWAI_PUBLIC_BASE_URL``；否则用 frp 穿透的 ``http://{addr}:{remote_port}``。
    """
    from fawai_avatar.config import load_dotenv

    load_dotenv()
    raw = (os.environ.get("FAWAI_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    addr = (os.environ.get("FAWAI_FRP_SERVER_ADDR") or "").strip()
    remote = (os.environ.get("FAWAI_FRP_REMOTE_PORT") or "").strip()
    enable = (os.environ.get("FAWAI_FRP_ENABLE") or "").strip().lower()
    if enable in {"0", "false", "no", "off"}:
        return None
    if addr and remote:
        return f"http://{addr}:{remote}"
    return None


def session_media_dir() -> Path:
    from fawai_avatar.config import RuntimeConfig

    data_dir = Path(RuntimeConfig.from_env().data_dir).expanduser()
    media = data_dir / "session_media"
    media.mkdir(parents=True, exist_ok=True)
    return media


def ensure_session_media(path: str | Path) -> Path | None:
    """保证文件在 ``session_media`` 下，返回该路径；失败返回 None。"""
    src = Path(path).expanduser()
    if not src.is_file():
        return None
    media = session_media_dir()
    try:
        if src.resolve().parent == media.resolve():
            return src.resolve()
    except OSError:
        pass
    digest = hashlib.sha1(str(src.resolve()).encode("utf-8", errors="replace")).hexdigest()[:10]
    dest = media / f"llm_{digest}_{src.name}"
    if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dest)
    return dest


def absolute_media_url(path: str | Path) -> str | None:
    """``{public_base}/media/{name}``；无公网基址则 None。"""
    base = public_base_url()
    if not base:
        return None
    local = ensure_session_media(path)
    if local is None:
        return None
    return f"{base}/media/{local.name}"


def llm_image_url(path: str | Path) -> str | None:
    """给 OpenAI 兼容 VLM 的 image_url。

    有公网基址 → 绝对 ``/media`` URL（远端可拉）；否则 ``data:``（本机/百炼等）。
    """
    abs_url = absolute_media_url(path)
    if abs_url:
        return abs_url
    return _image_data_url(path)


def _image_data_url(path: str | Path) -> str | None:
    import base64

    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"
