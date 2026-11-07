# term:截屏识别接地  tool:llm_ocr  doc:doc/功能/截屏识别接地/说明.md
"""截屏 OCR：百炼 Qwen-OCR 高精识别（文字+框），供观察层出 ref 表。"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fawai_avatar.config import LlmConfig

MAX_IMAGE_BYTES = 18 * 1024 * 1024
_DEFAULT_PROMPT = (
    "定位所有的文字行，并且返回旋转矩形"
    "([cx, cy, width, height, angle])的坐标结果。"
)


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion = usage.get("completion_tokens") or usage.get("output_tokens")
        total = usage.get("total_tokens")
    else:
        prompt = getattr(usage, "prompt_tokens", None) or getattr(
            usage, "input_tokens", None
        )
        completion = getattr(usage, "completion_tokens", None) or getattr(
            usage, "output_tokens", None
        )
        total = getattr(usage, "total_tokens", None)
    out: dict[str, int] = {}
    if prompt is not None:
        out["prompt_tokens"] = int(prompt)
    if completion is not None:
        out["completion_tokens"] = int(completion)
    if total is not None:
        out["total_tokens"] = int(total)
    elif out:
        out["total_tokens"] = int(out.get("prompt_tokens", 0)) + int(
            out.get("completion_tokens", 0)
        )
    return out


def _native_generation_url(base_url: str | None) -> str | None:
    """compatible-mode/v1 → DashScope multimodal-generation URL。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return None
    low = url.lower()
    if "dashscope" not in low and "aliyuncs.com" not in low and "maas" not in low:
        return None
    marker = "/compatible-mode"
    idx = low.find(marker)
    if idx > 0:
        host = url[:idx]
        return f"{host}/api/v1/services/aigc/multimodal-generation/generation"
    if "/api/v1" in low:
        root = url.split("/api/v1")[0]
        return f"{root}/api/v1/services/aigc/multimodal-generation/generation"
    return f"{url}/api/v1/services/aigc/multimodal-generation/generation"


def _parse_words_info(payload: Any) -> list[dict[str, Any]]:
    """从原生 / OpenAI 兼容响应里抽出 words_info 列表。"""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []

    # 原生：output.choices[0].message.content[0].ocr_result.words_info
    for key in ("words_info", "ocr_result"):
        if key == "words_info" and isinstance(payload.get("words_info"), list):
            return [x for x in payload["words_info"] if isinstance(x, dict)]
        nested = payload.get("ocr_result")
        if isinstance(nested, dict) and isinstance(nested.get("words_info"), list):
            return [x for x in nested["words_info"] if isinstance(x, dict)]

    output = payload.get("output") if isinstance(payload.get("output"), dict) else None
    if output:
        choices = output.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    ocr_r = part.get("ocr_result")
                    if isinstance(ocr_r, dict) and isinstance(
                        ocr_r.get("words_info"), list
                    ):
                        return [x for x in ocr_r["words_info"] if isinstance(x, dict)]
                    if isinstance(part.get("words_info"), list):
                        return [x for x in part["words_info"] if isinstance(x, dict)]
                    text = part.get("text")
                    parsed = _words_from_text(str(text or ""))
                    if parsed:
                        return parsed
            elif isinstance(content, dict):
                ocr_r = content.get("ocr_result")
                if isinstance(ocr_r, dict) and isinstance(ocr_r.get("words_info"), list):
                    return [x for x in ocr_r["words_info"] if isinstance(x, dict)]
            elif isinstance(content, str):
                parsed = _words_from_text(content)
                if parsed:
                    return parsed

    # OpenAI chat.completions 形态
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if isinstance(part.get("ocr_result"), dict):
                        wi = part["ocr_result"].get("words_info")
                        if isinstance(wi, list):
                            return [x for x in wi if isinstance(x, dict)]
                    t = part.get("text")
                    parsed = _words_from_text(str(t or ""))
                    if parsed:
                        return parsed
        elif isinstance(content, str):
            parsed = _words_from_text(content)
            if parsed:
                return parsed
    return []


def _words_from_text(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    # 去掉 ```json 围栏
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试截取第一个 { ... }
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        if isinstance(data.get("words_info"), list):
            return [x for x in data["words_info"] if isinstance(x, dict)]
        ocr_r = data.get("ocr_result")
        if isinstance(ocr_r, dict) and isinstance(ocr_r.get("words_info"), list):
            return [x for x in ocr_r["words_info"] if isinstance(x, dict)]
        if "text" in data and ("rotate_rect" in data or "location" in data):
            return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def normalize_word_boxes(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一为 {text, cx, cy, w, h, angle, location?}；丢弃空文案。"""
    out: list[dict[str, Any]] = []
    for item in words:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cx = cy = w = h = 0.0
        angle = 0.0
        rr = item.get("rotate_rect")
        if isinstance(rr, (list, tuple)) and len(rr) >= 4:
            try:
                cx, cy, w, h = float(rr[0]), float(rr[1]), float(rr[2]), float(rr[3])
                if len(rr) >= 5:
                    angle = float(rr[4])
            except (TypeError, ValueError):
                continue
        else:
            loc = item.get("location")
            if isinstance(loc, (list, tuple)) and len(loc) >= 8:
                try:
                    xs = [float(loc[i]) for i in range(0, 8, 2)]
                    ys = [float(loc[i]) for i in range(1, 8, 2)]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    cx = (min_x + max_x) / 2.0
                    cy = (min_y + max_y) / 2.0
                    w = max_x - min_x
                    h = max_y - min_y
                except (TypeError, ValueError):
                    continue
            else:
                continue
        if w <= 0 or h <= 0:
            # 仍允许极细线：给最小热区
            w = max(w, 8.0)
            h = max(h, 8.0)
        row: dict[str, Any] = {
            "text": text,
            "cx": cx,
            "cy": cy,
            "w": w,
            "h": h,
            "angle": angle,
        }
        if isinstance(item.get("location"), (list, tuple)):
            row["location"] = list(item["location"])
        out.append(row)
    return out


def _post_json(
    url: str, *, api_key: str, body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"OCR HTTP {exc.code}: {err_body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OCR 网络失败: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCR 响应非 JSON: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OCR 响应格式异常")
    return parsed


def _call_dashscope_native(
    *,
    url: str,
    api_key: str,
    model: str,
    data_uri: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    body = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": data_uri,
                            "min_pixels": 32 * 32 * 3,
                            "max_pixels": 32 * 32 * 8192,
                        }
                    ],
                }
            ]
        },
        "parameters": {"ocr_options": {"task": "advanced_recognition"}},
    }
    resp = _post_json(url, api_key=api_key, body=body, timeout=timeout)
    code = resp.get("code")
    if code and str(code) not in {"", "None", "null"}:
        raise RuntimeError(f"OCR API code={code}: {resp.get('message') or resp}")
    words = normalize_word_boxes(_parse_words_info(resp))
    usage = _usage_dict(resp.get("usage"))
    return words, usage


def _call_openai_compatible(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    data_uri: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai 包") from exc
    client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _DEFAULT_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        extra_body={"ocr_options": {"task": "advanced_recognition"}},
    )
    # 转成 dict 再解析
    raw: dict[str, Any]
    if hasattr(resp, "model_dump"):
        raw = resp.model_dump()
    else:
        raw = {
            "choices": [
                {
                    "message": {
                        "content": getattr(resp.choices[0].message, "content", None)
                        if resp.choices
                        else None
                    }
                }
            ],
            "usage": getattr(resp, "usage", None),
        }
    words = normalize_word_boxes(_parse_words_info(raw))
    usage = _usage_dict(raw.get("usage") if isinstance(raw, dict) else None)
    return words, usage


def ocr_image_words(
    image_path: str | Path,
    *,
    config: LlmConfig | None = None,
) -> dict[str, Any]:
    """对本地截屏 PNG 做高精 OCR。成功返回 ok + words[{text,cx,cy,w,h,...}]。"""
    from fawai_avatar.llm.settings_store import get_store

    cfg = config or LlmConfig.from_env()
    ep = get_store().resolve_route("ocr")
    api_key = (ep.api_key or cfg.api_key or "").strip()
    if not api_key:
        return {"ok": False, "error": "OCR 路由的供应商未配置 API Key", "words": []}
    model = (ep.model or "").strip() or "qwen3.5-ocr"
    path = Path(image_path)
    if not path.is_file():
        return {"ok": False, "error": f"截屏文件不存在: {path}", "words": []}
    data = path.read_bytes()
    if not data:
        return {"ok": False, "error": "截屏文件为空", "words": []}
    if len(data) > MAX_IMAGE_BYTES:
        return {
            "ok": False,
            "error": f"截屏过大（>{MAX_IMAGE_BYTES // (1024 * 1024)}MB）",
            "words": [],
        }
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    timeout = float(ep.timeout_sec if ep.timeout_sec is not None else cfg.timeout_sec or 120.0)
    base_url = (ep.base_url or cfg.base_url or "").strip() or None
    native_url = _native_generation_url(base_url)
    errors: list[str] = []
    words: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    transport = ""

    if native_url:
        try:
            words, usage = _call_dashscope_native(
                url=native_url,
                api_key=api_key,
                model=model,
                data_uri=data_uri,
                timeout=timeout,
            )
            transport = "dashscope_native"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"native:{exc}")

    if not words:
        try:
            words, usage = _call_openai_compatible(
                base_url=base_url,
                api_key=api_key,
                model=model,
                data_uri=data_uri,
                timeout=timeout,
            )
            transport = "openai_compatible"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"compatible:{exc}")

    if not words:
        return {
            "ok": False,
            "error": "；".join(errors) or "OCR 未返回文字框",
            "words": [],
            "model": model,
            "transport": transport or None,
        }
    return {
        "ok": True,
        "words": words,
        "model": model,
        "transport": transport,
        "usage": usage,
        "warnings": errors or None,
    }


if __name__ == "__main__":
    IMAGE_PATH = ""  # 例：data/observe/screen_xxx.png
    CHECK_ONLY = True

    cfg = LlmConfig.from_env()
    from fawai_avatar.llm.settings_store import get_store

    ep = get_store().resolve_route("ocr")
    print(
        {
            "ocr_model": ep.model,
            "base_url": ep.base_url or cfg.base_url,
            "has_key": bool(ep.api_key or cfg.api_key),
            "native_url": _native_generation_url(ep.base_url or cfg.base_url),
        }
    )
    if not CHECK_ONLY and IMAGE_PATH:
        r = ocr_image_words(IMAGE_PATH, config=cfg)
        print(
            {
                "ok": r.get("ok"),
                "n": len(r.get("words") or []),
                "error": r.get("error"),
                "sample": (r.get("words") or [])[:3],
            }
        )
