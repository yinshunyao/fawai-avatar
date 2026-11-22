# term:Cursor Agent  doc:doc/功能/06-工作区与编码/07-CursorAgent/说明.md
"""Cursor Agent 业务面：诊断、刷模型、创建/追问/列表/归档。"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fawai_avatar.cursor_agent import auth as ca_auth
from fawai_avatar.cursor_agent import runtime as ca_runtime
from fawai_avatar.cursor_agent.settings_store import get_store

OnEvent = Callable[[dict[str, Any]], None]


def resolve_cwd(cwd: str | None = None) -> str:
    raw = (cwd or "").strip()
    if raw:
        return str(os.path.abspath(os.path.expanduser(raw)))
    try:
        from fawai_avatar.config import RuntimeConfig

        return str(os.path.abspath(RuntimeConfig.from_env().work_root or "."))
    except Exception:  # noqa: BLE001
        return str(os.path.abspath("."))


def diagnose() -> dict[str, Any]:
    store = get_store()
    sdk_ok = ca_runtime.sdk_available()
    enabled = store.is_enabled()
    snap = ca_auth.auth_snapshot()
    has_auth = bool(snap.get("logged_in"))
    model = store.model_id()
    catalog = store.model_catalog()
    status = "available"
    detail = ""
    if not sdk_ok:
        status = "not_installed"
        detail = ca_runtime.sdk_import_error() or "pip install cursor-sdk"
    elif snap.get("login_pending"):
        status = "logging_in"
        detail = "正在登录 Cursor…"
    elif not has_auth:
        # 已有 API Key 时 Agent.create / 刷模型不依赖 Node；Node 仅浏览器登录需要
        if not snap.get("node_available"):
            status = "no_node"
            detail = (
                "未登录且未找到 Node.js ≥22（浏览器登录依赖 @cursor/sdk；"
                "可安装 nvm install 22、设置 FAWAI_CURSOR_NODE，或配置 CURSOR_API_KEY）"
            )
        else:
            status = "no_auth"
            detail = "未登录 Cursor（侧栏 Cursor Agent → 登录）"
    elif not enabled:
        status = "disabled"
        detail = "已禁用"
    return {
        "ok": status == "available" and enabled and has_auth and sdk_ok,
        "status": status,
        "detail": detail,
        "enabled": enabled,
        "has_auth": has_auth,
        "has_key": has_auth,  # 兼容旧前端字段
        "auth": snap,
        "sdk_installed": sdk_ok,
        "model_id": model,
        "mode": store.mode(),
        "auto_accept": store.auto_accept(),
        "models_count": catalog["count"],
        "models_fetched_at": catalog["fetched_at"],
        "label": "Cursor",
        "provider_id": "cursor",
    }


def refresh_models(*, api_key: str | None = None) -> dict[str, Any]:
    store = get_store()
    key = (api_key or "").strip()
    if not key:
        key, _src = ca_auth.resolve_api_key()
    if not key:
        return {"ok": False, "error": "未登录 Cursor（请先登录）", "models": []}
    try:
        models = ca_runtime.list_models(api_key=key)
        store.set_model_catalog(models, source="cursor.models.list")
        return {
            "ok": True,
            "models": models,
            "count": len(models),
            "fallback": False,
            "models_fetched_at": store.model_catalog()["fetched_at"],
            "models_source": "cursor.models.list",
        }
    except Exception as exc:  # noqa: BLE001
        # Bridge 502 等：落盘兜底目录，页面仍可选模型
        models = ca_runtime.fallback_models()
        store.set_model_catalog(models, source="fallback")
        return {
            "ok": True,
            "models": models,
            "count": len(models),
            "fallback": True,
            "warning": str(exc),
            "models_fetched_at": store.model_catalog()["fetched_at"],
            "models_source": "fallback",
        }


def ready_or_error() -> str | None:
    """可用返回 None；否则返回中文原因。"""
    d = diagnose()
    if d.get("ok"):
        return None
    status = str(d.get("status") or "")
    if status == "not_installed":
        return f"cursor-sdk 未安装（{d.get('detail') or 'pip install cursor-sdk'}）"
    if status == "no_node":
        return str(d.get("detail") or "未找到 Node.js ≥22（Cursor 浏览器登录需要）")
    if status in {"no_auth", "no_key"}:
        return "未登录 Cursor（侧栏 Cursor Agent → 登录）"
    if status == "logging_in":
        return "正在登录 Cursor，请稍候"
    if status == "disabled":
        return "Cursor Agent 已禁用"
    return str(d.get("detail") or "Cursor Agent 不可用")


def _resolved_key() -> str:
    key, _ = ca_auth.resolve_api_key()
    return key


def _friendly_sdk_error(exc: BaseException) -> str:
    """把 Bridge 502 等常见坑转成可行动提示。"""
    text = str(exc or "").strip() or exc.__class__.__name__
    low = text.lower()
    if "502" in low or "bridge request failed" in low:
        return (
            "Cursor Bridge 调用失败（常为本机 HTTP 代理劫持 127.0.0.1）。"
            "已自动绕过回环地址；若仍失败请检查系统代理对 localhost 的例外，或暂时关闭代理后重试。"
            f" 原始错误：{text}"
        )
    return text


def create_and_prompt(
    prompt: str,
    *,
    title: str = "",
    task_id: str = "",
    cwd: str | None = None,
    model: str | None = None,
    on_event: OnEvent | None = None,
) -> dict[str, Any]:
    err = ready_or_error()
    if err:
        return {"ok": False, "error": err}
    store = get_store()
    work = resolve_cwd(cwd)
    model_id = (model or "").strip() or store.model_id()
    mode_id = store.mode()
    auto_accept = store.auto_accept()
    try:
        result = ca_runtime.run_agent_prompt(
            prompt,
            api_key=_resolved_key(),
            model=model_id,
            cwd=work,
            agent_id=None,
            mode=mode_id,
            auto_accept=auto_accept,
            on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": _friendly_sdk_error(exc),
            "cwd": work,
            "model": model_id,
        }
    aid = str(result.get("agent_id") or "")
    if aid:
        store.upsert_agent(
            aid,
            task_id=task_id,
            cwd=work,
            model_id=model_id,
            title=(title or prompt[:80]).strip(),
            meta={
                "last_status": result.get("status"),
                "mode": mode_id,
                "auto_accept": auto_accept,
            },
        )
    result["title"] = (title or prompt[:80]).strip()
    return result


def send_followup(
    agent_id: str,
    prompt: str,
    *,
    cwd: str | None = None,
    model: str | None = None,
    task_id: str = "",
    on_event: OnEvent | None = None,
) -> dict[str, Any]:
    err = ready_or_error()
    if err:
        return {"ok": False, "error": err}
    store = get_store()
    aid = (agent_id or "").strip()
    if not aid:
        return {"ok": False, "error": "agent_id required"}
    row = store.get_agent(aid)
    work = resolve_cwd(cwd or (row or {}).get("cwd"))
    model_id = (model or "").strip() or (row or {}).get("model_id") or store.model_id()
    mode_id = store.mode()
    auto_accept = store.auto_accept()
    try:
        result = ca_runtime.run_agent_prompt(
            prompt,
            api_key=_resolved_key(),
            model=str(model_id),
            cwd=work,
            agent_id=aid,
            mode=mode_id,
            auto_accept=auto_accept,
            on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": _friendly_sdk_error(exc),
            "agent_id": aid,
            "cwd": work,
        }
    store.upsert_agent(
        aid,
        task_id=task_id or (row or {}).get("task_id") or "",
        cwd=work,
        model_id=str(model_id),
        title=(row or {}).get("title") or "",
        meta={
            "last_status": result.get("status"),
            "mode": mode_id,
            "auto_accept": auto_accept,
        },
    )
    return result


def list_agents(*, include_archived: bool = False) -> list[dict[str, Any]]:
    return get_store().list_agents(include_archived=include_archived)


def archive_agent(agent_id: str) -> dict[str, Any]:
    ok = get_store().archive_agent(agent_id)
    return {"ok": ok, "agent_id": (agent_id or "").strip()}


def bind_task_agent(task_id: str, agent_id: str, *, cwd: str = "", model_id: str = "", title: str = "") -> None:
    get_store().upsert_agent(
        agent_id,
        task_id=task_id,
        cwd=cwd,
        model_id=model_id,
        title=title,
    )


def agent_for_task(task_id: str) -> dict[str, Any] | None:
    return get_store().find_agent_by_task(task_id)
