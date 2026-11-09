# term:模型配置  doc:doc/功能/LLM接入/说明.md
"""按模型挂载生成参数：落盘 ``{data_dir}/llm_models/{model}.json``，不写 .env。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# 可挂采样 / 思考参数的 chat 类角色（ASR / embed 不走这些）
CHAT_PARAM_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "enable_thinking",
    "thinking_budget",
)

# OpenAI 标准采样键（不含思考；思考走 extra_body）
SAMPLING_PARAM_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
)

PARAM_SCHEMA: tuple[dict[str, Any], ...] = (
    {
        "key": "temperature",
        "label": "Temperature",
        "kind": "float",
        "step": "0.1",
        "hint": "空=接口默认；越高越随机（常见 0～2）",
    },
    {
        "key": "top_p",
        "label": "Top P",
        "kind": "float",
        "step": "0.05",
        "hint": "空=接口默认；核采样阈值（常见 0～1）",
    },
    {
        "key": "max_tokens",
        "label": "Max Tokens",
        "kind": "int",
        "step": "1",
        "hint": "单次补全上限；0/空=不限制（由服务商默认）",
    },
    {
        "key": "presence_penalty",
        "label": "Presence Penalty",
        "kind": "float",
        "step": "0.1",
        "hint": "空=不传；降低重复话题倾向（常见 -2～2）",
    },
    {
        "key": "frequency_penalty",
        "label": "Frequency Penalty",
        "kind": "float",
        "step": "0.1",
        "hint": "空=不传；降低重复用词倾向（常见 -2～2）",
    },
    {
        "key": "enable_thinking",
        "label": "Enable Thinking",
        "kind": "tri_bool",
        "hint": "空=跟调用角色默认（主脑开 / 快模型与观察反省关）；true/false 强制开/关",
    },
    {
        "key": "thinking_budget",
        "label": "Thinking Budget",
        "kind": "int",
        "step": "1",
        "hint": "思考 token 上限；0/空=不限制；仅思考开启且填了正整数时写入请求",
    },
)

_SAFE_RE = re.compile(r"[^A-Za-z0-9._+-]+")


def models_dir(*, data_dir: str | None = None) -> Path:
    from fawai_avatar.config import load_dotenv, _env

    load_dotenv()
    base = data_dir if data_dir is not None else (_env("FAWAI_DATA_DIR") or "data")
    return Path(base).expanduser().resolve() / "llm_models"


def sanitize_model_id(model: str) -> str:
    name = (model or "").strip()
    if not name:
        raise ValueError("empty model id")
    safe = _SAFE_RE.sub("_", name)
    if not safe or safe in {".", ".."}:
        raise ValueError(f"invalid model id: {model!r}")
    return safe


def profile_path(model: str, *, data_dir: str | None = None) -> Path:
    return models_dir(data_dir=data_dir) / f"{sanitize_model_id(model)}.json"


def empty_params() -> dict[str, Any]:
    return {k: None for k in CHAT_PARAM_KEYS}


def _coerce_float(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _coerce_int(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return None if n <= 0 else n


def _coerce_tri_bool(raw: Any) -> bool | None:
    """空 / 缺省 → None；true/false（及 1/0 等）→ bool。"""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(int(raw))
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == "":
            return None
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
    return None


def normalize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw or {}
    out = empty_params()
    out["temperature"] = _coerce_float(src.get("temperature"))
    out["top_p"] = _coerce_float(src.get("top_p"))
    out["max_tokens"] = _coerce_int(src.get("max_tokens"))
    out["presence_penalty"] = _coerce_float(src.get("presence_penalty"))
    out["frequency_penalty"] = _coerce_float(src.get("frequency_penalty"))
    out["enable_thinking"] = _coerce_tri_bool(src.get("enable_thinking"))
    out["thinking_budget"] = _coerce_int(src.get("thinking_budget"))
    return out


def sampling_kwargs_from_params(params: dict[str, Any] | None) -> dict[str, float | int]:
    """仅 OpenAI 标准采样字段；思考参数不进这里。"""
    p = normalize_params(params)
    out: dict[str, float | int] = {}
    if p["temperature"] is not None:
        out["temperature"] = float(p["temperature"])
    if p["top_p"] is not None:
        out["top_p"] = float(p["top_p"])
    if p["max_tokens"] is not None:
        out["max_tokens"] = int(p["max_tokens"])
    if p["presence_penalty"] is not None:
        out["presence_penalty"] = float(p["presence_penalty"])
    if p["frequency_penalty"] is not None:
        out["frequency_penalty"] = float(p["frequency_penalty"])
    return out


def thinking_profile_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """从档案取出思考相关字段（供 resolve_thinking）。"""
    p = normalize_params(params)
    return {
        "enable_thinking": p.get("enable_thinking"),
        "thinking_budget": p.get("thinking_budget"),
    }


def _legacy_env_seed() -> dict[str, Any]:
    """一次性兼容：若磁盘尚无档案，可从旧 .env 采样键播种。"""
    from fawai_avatar.config import _env, _env_float, _env_int

    max_tokens = _env_int("FAWAI_LLM_MAX_TOKENS", default=0)
    return normalize_params(
        {
            "temperature": _env_float("FAWAI_LLM_TEMPERATURE"),
            "top_p": _env_float("FAWAI_LLM_TOP_P"),
            "max_tokens": None if max_tokens is None or max_tokens <= 0 else max_tokens,
            "presence_penalty": _env_float("FAWAI_LLM_PRESENCE_PENALTY"),
            "frequency_penalty": _env_float("FAWAI_LLM_FREQUENCY_PENALTY"),
        }
    )


def load_profile(model: str, *, data_dir: str | None = None) -> dict[str, Any] | None:
    mid = (model or "").strip()
    if not mid:
        return None
    # 优先 SQLite
    try:
        from fawai_avatar.llm.settings_store import get_store

        store = get_store(data_dir=data_dir)
        if store.has_profile(mid):
            return {
                "model": mid,
                "params": normalize_params(store.get_profile_params(mid)),
                "path": store.db_path,
                "updated_at": None,
                "source": "sqlite",
            }
    except Exception:
        pass
    path = profile_path(mid, data_dir=data_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    mid2 = str(data.get("model") or mid).strip() or mid
    params = normalize_params(data.get("params") if isinstance(data.get("params"), dict) else data)
    return {
        "model": mid2,
        "params": params,
        "path": str(path),
        "updated_at": data.get("updated_at"),
        "source": "file",
    }


def save_profile(
    model: str,
    params: dict[str, Any] | None = None,
    *,
    data_dir: str | None = None,
) -> dict[str, Any]:
    mid = (model or "").strip()
    if not mid:
        raise ValueError("empty model id")
    clean = normalize_params(params)
    from fawai_avatar.llm.settings_store import get_store

    store = get_store(data_dir=data_dir) if data_dir else get_store()
    store.set_profile_params(mid, clean)
    # 同步写一份文件，便于外部查看
    path = profile_path(mid, data_dir=data_dir or store.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": mid,
        "params": clean,
        "updated_at": int(time.time()),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        path = Path(store.db_path)
    return {
        "model": mid,
        "params": clean,
        "path": str(path),
        "updated_at": payload["updated_at"],
        "source": "sqlite",
    }


def ensure_profile(
    model: str,
    *,
    data_dir: str | None = None,
    seed_from_env: bool = True,
) -> dict[str, Any]:
    """选定模型后保证有档案；已有则原样返回。"""
    mid = (model or "").strip()
    if not mid:
        raise ValueError("empty model id")
    existing = load_profile(mid, data_dir=data_dir)
    if existing is not None:
        return existing
    seed = _legacy_env_seed() if seed_from_env else empty_params()
    if not any(v is not None for v in seed.values()):
        seed = empty_params()
    return save_profile(mid, seed, data_dir=data_dir)


def ensure_profiles(models: list[str] | tuple[str, ...], *, data_dir: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw in models:
        mid = (raw or "").strip()
        if not mid:
            continue
        out[mid] = ensure_profile(mid, data_dir=data_dir)
    return out


def sampling_kwargs_for_model(model: str, *, data_dir: str | None = None) -> dict[str, float | int]:
    mid = (model or "").strip()
    if not mid:
        return {}
    profile = load_profile(mid, data_dir=data_dir)
    if profile is None:
        return {}
    return sampling_kwargs_from_params(profile.get("params"))


def list_local_profiles(*, data_dir: str | None = None) -> list[dict[str, Any]]:
    root = models_dir(data_dir=data_dir)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        mid = str(data.get("model") or path.stem).strip()
        rows.append(
            {
                "model": mid,
                "params": normalize_params(
                    data.get("params") if isinstance(data.get("params"), dict) else data
                ),
                "path": str(path),
                "updated_at": data.get("updated_at"),
            }
        )
    return rows


# 路由角色 → DashScope 原生目录 capabilities
_KIND_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "text": ("TG",),
    "vlm": ("VU",),
    "asr": ("ASR",),
    # OCR 与多模态同属视觉目录；启发式再按模型名收窄
    "ocr": ("VU",),
    "embed": ("TR",),
}

_KIND_ALIASES: dict[str, str] = {
    "": "all",
    "all": "all",
    "text": "text",
    "chat": "text",
    "llm": "text",
    "vlm": "vlm",
    "vision": "vlm",
    "multimodal": "vlm",
    "asr": "asr",
    "speech": "asr",
    "ocr": "ocr",
    "embed": "embed",
    "embedding": "embed",
}


def normalize_model_kind(kind: str | None) -> str:
    raw = (kind or "all").strip().lower()
    return _KIND_ALIASES.get(raw, raw if raw in _KIND_CAPABILITIES or raw == "all" else "all")


def dashscope_catalog_host(base_url: str | None) -> str | None:
    """``…/compatible-mode/v1`` → 原生目录主机；非 DashScope 返回 None。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return None
    low = url.lower()
    if "dashscope" not in low and "aliyuncs.com" not in low:
        return None
    # https://dashscope.aliyuncs.com/compatible-mode/v1
    marker = "/compatible-mode"
    idx = low.find(marker)
    if idx > 0:
        return url[:idx]
    # already root host
    if low.endswith("/v1"):
        return url[:-3]
    return url


def _heuristic_kind(model_id: str) -> str:
    s = (model_id or "").lower()
    if any(x in s for x in ("embedding", "embed", "text-embedding", "rerank")):
        return "embed"
    if any(x in s for x in ("asr", "paraformer", "fun-asr", "speech-02", "speech-2.")):
        return "asr"
    if "ocr" in s:
        return "ocr"
    if any(
        x in s
        for x in (
            "tts",
            "realtime",
            "image",
            "wanx",
            "wan2",
            "video",
            "omni",
            "kling",
            "vidu",
        )
    ):
        return "other"
    if any(x in s for x in ("-vl", "vl-", "vlm", "vision", "qvq", "gui-")):
        return "vlm"
    return "text"


def _list_dashscope_catalog(
    *,
    host: str,
    api_key: str,
    kind: str,
    timeout_sec: float | None,
) -> dict[str, Any]:
    """DashScope ``GET /api/v1/models``（按 capabilities 分页）。"""
    import urllib.error
    import urllib.parse
    import urllib.request

    caps = _KIND_CAPABILITIES.get(kind)
    if not caps:
        # all：合并各类
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        sources: list[str] = []
        for sub in ("text", "vlm", "asr", "embed"):
            part = _list_dashscope_catalog(
                host=host, api_key=api_key, kind=sub, timeout_sec=timeout_sec
            )
            if not part.get("ok"):
                return part
            sources.append(sub)
            for m in part.get("models") or []:
                mid = str(m.get("id") or "").strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                merged.append(m)
        merged.sort(key=lambda m: str(m.get("id") or "").lower())
        return {
            "ok": True,
            "source": "dashscope_catalog",
            "kind": "all",
            "models": merged,
            "count": len(merged),
            "kinds": sources,
        }

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    timeout = 30.0 if timeout_sec is None else float(timeout_sec)
    for cap in caps:
        page = 1
        total = None
        while page <= 30:
            qs = urllib.parse.urlencode(
                [
                    ("page_no", str(page)),
                    ("page_size", "100"),
                    ("capabilities", cap),
                    ("language", "zh-CN"),
                ]
            )
            req = urllib.request.Request(
                f"{host.rstrip('/')}/api/v1/models?{qs}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": f"DashScope catalog HTTP {exc.code}: {detail or exc.reason}",
                    "models": [],
                    "source": "dashscope_catalog",
                    "kind": kind,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "error": str(exc),
                    "models": [],
                    "source": "dashscope_catalog",
                    "kind": kind,
                }
            out = payload.get("output") if isinstance(payload, dict) else None
            if not isinstance(out, dict):
                return {
                    "ok": False,
                    "error": "DashScope catalog 响应异常",
                    "models": [],
                    "source": "dashscope_catalog",
                    "kind": kind,
                }
            batch = out.get("models") or []
            if not isinstance(batch, list):
                batch = []
            try:
                total = int(out.get("total") or 0)
            except (TypeError, ValueError):
                total = len(batch)
            for item in batch:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("model") or "").strip()
                if not mid or mid in seen:
                    continue
                # 嵌入类去掉 rerank
                if kind == "embed" and "rerank" in mid.lower():
                    continue
                seen.add(mid)
                name = str(item.get("name") or mid).strip() or mid
                models.append(
                    {
                        "id": mid,
                        "name": name,
                        "provider": str(item.get("provider") or ""),
                        "capabilities": list(item.get("capabilities") or [cap]),
                        "features": list(item.get("features") or []),
                        "kind": kind,
                    }
                )
            if not batch or (total is not None and len(seen) >= total) or len(batch) < 100:
                break
            page += 1

    if kind == "ocr":
        models = [m for m in models if "ocr" in str(m.get("id") or "").lower()]
    elif kind == "vlm":
        models = [m for m in models if "ocr" not in str(m.get("id") or "").lower()]
    models.sort(key=lambda m: str(m.get("id") or "").lower())
    return {
        "ok": True,
        "source": "dashscope_catalog",
        "kind": kind,
        "models": models,
        "count": len(models),
    }


def _list_openai_compatible_models(
    *,
    api_key: str,
    base_url: str | None,
    kind: str,
    timeout_sec: float | None,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "error": "未安装 openai 包", "models": [], "source": "openai_compatible"}

    client_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url or None}
    if timeout_sec is not None:
        client_kwargs["timeout"] = timeout_sec
    try:
        client = OpenAI(**client_kwargs)
        resp = client.models.list()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
            "models": [],
            "source": "openai_compatible",
            "kind": kind,
        }

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    data = getattr(resp, "data", None) or []
    for item in data:
        mid = str(getattr(item, "id", None) or "").strip()
        if not mid or mid in seen:
            continue
        bucket = _heuristic_kind(mid)
        if kind != "all" and bucket != kind:
            continue
        if bucket == "other":
            continue
        seen.add(mid)
        owned = getattr(item, "owned_by", None)
        created = getattr(item, "created", None)
        models.append(
            {
                "id": mid,
                "name": mid,
                "owned_by": str(owned) if owned is not None else "",
                "created": int(created) if created is not None else None,
                "kind": bucket,
            }
        )
    models.sort(key=lambda m: m["id"].lower())
    return {
        "ok": True,
        "source": "openai_compatible",
        "kind": kind,
        "models": models,
        "count": len(models),
    }


def list_remote_models(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_sec: float | None = None,
    kind: str | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    """
    在线拉取可选模型。

    - DashScope（base 含 dashscope）：优先原生 ``/api/v1/models``，按 kind 过滤
      （text=TG / vlm=VU / asr=ASR / embed=TR）
    - 其它 OpenAI 兼容：``GET /models`` + 启发式分类
    - 可传 ``provider_id`` 从设置库取凭证；成功时写入 ``model_catalogs`` 缓存
    """
    from fawai_avatar.config import LlmConfig
    from fawai_avatar.llm.settings_store import get_store

    cfg = LlmConfig.from_env()
    key = (api_key if api_key is not None else "") or ""
    url = base_url if base_url is not None else None
    timeout = timeout_sec
    pid = (provider_id or "").strip()
    if pid:
        prov = get_store().get_provider(pid)
        if not prov:
            return {"ok": False, "error": f"未知供应商: {pid}", "models": [], "kind": normalize_model_kind(kind)}
        if url is None:
            url = prov.get("base_url") or None
        if not key.strip():
            key = str(prov.get("api_key") or "")
        if timeout is None:
            timeout = prov.get("timeout_sec")
    if url is None:
        url = cfg.base_url
    if not key.strip():
        key = (cfg.api_key or "") if api_key is None else ""
    if timeout is None:
        timeout = cfg.timeout_sec
    kind_n = normalize_model_kind(kind)
    if not key.strip():
        return {
            "ok": False,
            "error": "未配置 API Key" + (f"（供应商 {pid}）" if pid else ""),
            "models": [],
            "kind": kind_n,
            "provider_id": pid or None,
        }

    def _persist_catalog(result: dict[str, Any]) -> dict[str, Any]:
        """按供应商写入本机目录缓存；下次打开配置页可先用水合列表。"""
        if not pid or not result.get("ok"):
            if pid:
                result = {**result, "provider_id": pid}
            return result
        try:
            get_store().set_model_catalog(
                pid,
                kind_n,
                list(result.get("models") or []),
                source=str(result.get("source") or ""),
                base_url=str(url or ""),
            )
        except Exception:
            pass
        return {**result, "provider_id": pid}

    host = dashscope_catalog_host(url)
    if host:
        result = _list_dashscope_catalog(
            host=host, api_key=key.strip(), kind=kind_n, timeout_sec=timeout
        )
        if result.get("ok"):
            return _persist_catalog(result)
        # 原生目录失败时回退 compatible /models
        fallback = _list_openai_compatible_models(
            api_key=key.strip(), base_url=url, kind=kind_n, timeout_sec=timeout
        )
        if fallback.get("ok"):
            fallback["warning"] = result.get("error")
            fallback["fallback_from"] = "dashscope_catalog"
            return _persist_catalog(fallback)
        if pid:
            fallback = {**fallback, "provider_id": pid}
        return fallback

    result = _list_openai_compatible_models(
        api_key=key.strip(), base_url=url, kind=kind_n, timeout_sec=timeout
    )
    return _persist_catalog(result)


def profiles_payload_for_models(
    models: list[str] | tuple[str, ...],
    *,
    data_dir: str | None = None,
    ensure: bool = True,
) -> dict[str, Any]:
    """供 Web 设置页：所选模型 → 档案（可选 ensure 落盘）。"""
    out: dict[str, Any] = {}
    for raw in models:
        mid = (raw or "").strip()
        if not mid:
            continue
        if ensure:
            out[mid] = ensure_profile(mid, data_dir=data_dir)
        else:
            loaded = load_profile(mid, data_dir=data_dir)
            if loaded is not None:
                out[mid] = loaded
            else:
                out[mid] = {
                    "model": mid,
                    "params": empty_params(),
                    "path": str(profile_path(mid, data_dir=data_dir)),
                    "updated_at": None,
                    "missing": True,
                }
    return out


def strip_legacy_sampling_env(*, path: Path | None = None) -> list[str]:
    """从 .env 删除旧全局采样键（参数已迁到模型档案）。"""
    from fawai_avatar.channel.system_settings import apply_process_env, env_file_path, merge_dotenv_file

    keys = [
        "FAWAI_LLM_TEMPERATURE",
        "FAWAI_LLM_TOP_P",
        "FAWAI_LLM_MAX_TOKENS",
        "FAWAI_LLM_PRESENCE_PENALTY",
        "FAWAI_LLM_FREQUENCY_PENALTY",
    ]
    updates = {k: None for k in keys}
    merge_dotenv_file(path or env_file_path(), updates)
    apply_process_env(updates)
    return keys
