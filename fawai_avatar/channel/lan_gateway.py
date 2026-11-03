# term:会话网关  doc:doc/功能/会话网关/说明.md
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fawai_avatar.channel.hub import SessionHub
from fawai_avatar.channel.message import Message
from fawai_avatar.channel.relay import CloudRelayClient
from fawai_avatar.channel.system_settings import get_settings_payload, update_settings
from fawai_avatar.runtime.releases import EXIT_ACTIVATE, EXIT_RESTART, ReleaseStore, releases_from_env

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _schedule_process_exit(code: int, *, delay_s: float = 0.6) -> None:
    """让当前 HTTP 响应先返回，再退出进程（保活外层或本机自拉起）。"""

    def _exit() -> None:
        time.sleep(delay_s)
        os._exit(code)

    threading.Thread(target=_exit, name="fawai-exit", daemon=True).start()


def generate_passcode(length: int = 6) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def guess_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, message: Message) -> None:
        payload = message.to_dict()
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


def create_app(
    hub: SessionHub,
    *,
    passcode: str,
    media_dir: Path,
    relay: CloudRelayClient | None = None,
    releases: ReleaseStore | None = None,
) -> FastAPI:
    media_dir.mkdir(parents=True, exist_ok=True)
    manager = ConnectionManager()
    relay_client = relay
    release_store = releases

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        if relay_client is not None:
            await relay_client.connect()
            hub.add_broadcaster(relay_client.send)
        # 进程中断续跑：拉起孤儿 running/waiting/blocked/pending
        recovered = hub.recover_interrupted_tasks()
        if recovered:
            from fawai_avatar.runtime.console_log import cprint

            cprint(f"  续跑恢复: {len(recovered)} 个任务")
        yield
        if relay_client is not None:
            await relay_client.disconnect()

    app = FastAPI(
        title="fawai-avatar session gateway",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.hub = hub
    app.state.passcode = passcode.upper()
    app.state.manager = manager
    app.state.media_dir = media_dir
    app.state.releases = release_store

    async def _broadcast(message: Message) -> None:
        # 出站图：若有本地路径则映射为 /media/…
        if message.kind == "image" and message.media_path and not message.media_url:
            src = Path(message.media_path)
            if src.is_file():
                dest = media_dir / f"out_{message.message_id}{src.suffix or '.png'}"
                if not dest.exists():
                    dest.write_bytes(src.read_bytes())
                message.media_url = f"/media/{dest.name}"
        await manager.broadcast(message)

    hub.add_broadcaster(_broadcast)

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

    def _check_token(token: str | None) -> None:
        if not token or token.strip().upper() != app.state.passcode:
            raise HTTPException(status_code=401, detail="invalid passcode")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="PWA missing")
        return FileResponse(index_path)

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        under_keeper = os.environ.get("FAWAI_KEEPER", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        slog = getattr(hub, "system_log", None)
        slog_ok = bool(getattr(slog, "available", True)) if slog is not None else True
        return {
            "ok": True,
            "service": "fawai-avatar-gateway",
            "keeper": under_keeper,
            "degraded": not slog_ok,
            "system_log_available": slog_ok,
        }

    def _releases() -> ReleaseStore:
        store = getattr(app.state, "releases", None)
        if store is None:
            raise HTTPException(status_code=503, detail="runtime releases unavailable")
        return store

    @app.get("/api/runtime")
    async def runtime_status(x_fawai_token: str | None = Header(default=None)) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        slog = getattr(hub, "system_log", None)
        slog_status = slog.status() if slog is not None and hasattr(slog, "status") else {}
        from fawai_avatar.runtime.console_log import last_crash_path, console_log_path

        data_dir = str(hub.supervisor.data_dir)
        crash_path = last_crash_path(data_dir)
        return JSONResponse(
            {
                "ok": True,
                **store.status(),
                "degraded": not bool(slog_status.get("available", True)),
                "system_log": slog_status,
                "console_log_path": str(console_log_path(data_dir)),
                "last_crash_path": str(crash_path),
                "has_crash": crash_path.is_file() and crash_path.stat().st_size > 0,
            }
        )

    @app.get("/api/runtime/console-logs")
    async def runtime_console_logs(
        x_fawai_token: str | None = Header(default=None),
        limit: int = 200,
    ) -> JSONResponse:
        """后台控制台日志（保活 + 网关 stdout/stderr 落盘）。"""
        _check_token(x_fawai_token)
        from fawai_avatar.runtime.console_log import read_console_log

        data_dir = str(hub.supervisor.data_dir)
        snap = read_console_log(data_dir, max_lines=limit)
        slog = getattr(hub, "system_log", None)
        return JSONResponse(
            {
                **snap,
                "degraded": not getattr(slog, "available", True) if slog else False,
                "system_log_error": getattr(slog, "init_error", None) if slog else None,
            }
        )

    @app.post("/api/runtime/repair-startup")
    async def runtime_repair_startup(
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """一键修复启动错误：自动创建整改任务并开跑。"""
        _check_token(x_fawai_token)
        body = payload or {}
        force = bool(body.get("force"))
        card = await hub.start_startup_repair(force=force)
        if card.kind == "error":
            raise HTTPException(status_code=400, detail=card.text or "repair failed")
        return JSONResponse(
            {
                "ok": True,
                "task_id": card.task_id,
                "message": card.text,
                "meta": card.meta or {},
            }
        )

    @app.post("/api/runtime/restart")
    async def runtime_restart(x_fawai_token: str | None = Header(default=None)) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        cmd = store.request_restart()
        under_keeper = store.status()["keeper"]
        await hub.publish(
            Message.card(
                "服务即将重启…" + ("（保活外层会拉起）" if under_keeper else "（无保活外层，进程退出后需手动启动）"),
                meta={"ok": True, "control": "restart"},
            )
        )
        _schedule_process_exit(EXIT_RESTART)
        return JSONResponse(
            {
                "ok": True,
                "action": "restart",
                "keeper": under_keeper,
                "command": cmd,
                "message": "restart scheduled",
            }
        )

    @app.get("/api/runtime/releases")
    async def list_releases_api(
        x_fawai_token: str | None = Header(default=None),
        limit: int = 50,
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        limit = max(1, min(int(limit or 50), 200))
        return JSONResponse(
            {
                "ok": True,
                "releases": store.list_releases(limit=limit),
                "active": store.read_active(),
            }
        )

    @app.post("/api/runtime/releases")
    async def snapshot_release_api(
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        body = payload or {}
        release_id = store.snapshot(
            message=str(body.get("message") or ""),
            label=str(body.get("label") or ""),
        )
        return JSONResponse({"ok": True, "release_id": release_id})

    @app.delete("/api/runtime/releases/{release_id}")
    async def delete_release_api(
        release_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        ok = store.delete(release_id)
        if not ok:
            raise HTTPException(status_code=404, detail="unknown release")
        return JSONResponse({"ok": True, "release_id": release_id})

    @app.post("/api/runtime/releases/{release_id}/activate")
    async def activate_release_api(
        release_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        store = _releases()
        try:
            cmd = store.request_activate(release_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        under_keeper = store.status()["keeper"]
        await hub.publish(
            Message.card(
                f"即将激活存档 {release_id[:18]}… 并重启",
                meta={"ok": True, "control": "activate", "release_id": release_id},
            )
        )
        if under_keeper:
            _schedule_process_exit(EXIT_ACTIVATE)
        else:
            # 无外层保活：本进程内切换代码后以重启码退出，提示需手动再启
            try:
                store.apply_activate(release_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"activate failed: {exc}") from exc
            _schedule_process_exit(EXIT_RESTART)
        return JSONResponse(
            {
                "ok": True,
                "action": "activate",
                "release_id": release_id,
                "keeper": under_keeper,
                "command": cmd,
                "message": "activate scheduled",
            }
        )

    @app.get("/api/settings")
    async def settings_get(x_fawai_token: str | None = Header(default=None)) -> JSONResponse:
        _check_token(x_fawai_token)
        return JSONResponse(get_settings_payload())

    @app.put("/api/settings")
    async def settings_put(
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        body = payload or {}
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        clear_raw = body.get("clear") or []
        clear_list = [str(x) for x in clear_raw] if isinstance(clear_raw, list) else []
        result = update_settings(values=values, clear=clear_list)
        if not result.get("ok", True) and result.get("errors"):
            raise HTTPException(status_code=400, detail={"errors": result["errors"]})
        return JSONResponse(result)

    @app.get("/api/session")
    async def session_info(x_fawai_token: str | None = Header(default=None)) -> JSONResponse:
        _check_token(x_fawai_token)
        tasks = [
            _task_summary(t)
            for t in hub.supervisor.list_tasks(limit=20, archived=False)
        ]
        # 仅引导近段；打开线程时再按任务拉齐执行时间线（见 GET /api/tasks/{id}/history）
        history = [m.to_dict() for m in hub.history[-120:]]
        runtime = None
        if release_store is not None:
            runtime = {
                "keeper": release_store.status()["keeper"],
                "active": release_store.read_active(),
            }
        return JSONResponse({"ok": True, "tasks": tasks, "history": history, "runtime": runtime})

    def _resolve_task(task_id: str):
        task = hub.supervisor.get_task(task_id)
        if task is not None:
            return task
        matches = [
            t
            for t in hub.supervisor.list_tasks(limit=100, archived=None)
            if t.task_id.startswith(task_id)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    @app.get("/api/system-logs")
    async def list_system_logs_api(
        x_fawai_token: str | None = Header(default=None),
        limit: int = 100,
        action: str | None = None,
        category: str | None = None,
        task_id: str | None = None,
        since: float | None = None,
        ok: str | None = None,
    ) -> JSONResponse:
        """系统日志查询；按订阅落盘，不进会话。"""
        _check_token(x_fawai_token)
        slog = hub.system_log
        if not getattr(slog, "available", True):
            return JSONResponse(
                {
                    "ok": True,
                    "logs": [],
                    "degraded": True,
                    "init_error": getattr(slog, "init_error", None),
                    "message": "系统日志库降级；请到系统配置查看后台日志或一键修复启动错误",
                }
            )
        ok_flag: bool | None = None
        raw_ok = (ok or "").strip().lower()
        if raw_ok in {"1", "true", "yes", "ok"}:
            ok_flag = True
        elif raw_ok in {"0", "false", "no", "fail", "failed"}:
            ok_flag = False
        entries = slog.list_logs(
            limit=limit,
            action=(action or None),
            category=(category or None),
            task_id=(task_id or None),
            since=since,
            ok=ok_flag,
        )
        return JSONResponse(
            {"ok": True, "logs": [e.to_dict() for e in entries]}
        )

    @app.get("/api/system-log-subscriptions")
    async def get_system_log_subscriptions_api(
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """系统日志订阅目录与开关（默认全开）。"""
        _check_token(x_fawai_token)
        return JSONResponse({"ok": True, **hub.system_log.get_subscriptions()})

    @app.put("/api/system-log-subscriptions")
    async def put_system_log_subscriptions_api(
        request: Request,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """更新系统日志订阅；仅影响之后写入。"""
        _check_token(x_fawai_token)
        body = await request.json()
        enabled = body.get("enabled") if isinstance(body, dict) else None
        if enabled is None and isinstance(body, dict):
            # 允许直接传 { "task.delete": false, ... }
            enabled = {
                k: v
                for k, v in body.items()
                if k not in {"ok", "catalog"} and isinstance(v, (bool, int))
            }
        data = hub.system_log.set_subscriptions(enabled if isinstance(enabled, dict) else {})
        return JSONResponse({"ok": True, **data})

    @app.post("/api/system-logs/analyze")
    async def analyze_system_logs_api(
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """失败系统日志一键分析：自动建整改任务（目标/要求/边界）并复用任务对话开跑。

        默认跳过已分析日志；已分析则复开原任务。force=true 强制重分析。
        """
        _check_token(x_fawai_token)
        body = payload or {}
        raw_ids = body.get("log_ids") or body.get("ids") or []
        if body.get("log_id") and not raw_ids:
            raw_ids = [body.get("log_id")]
        if isinstance(raw_ids, str):
            raw_ids = [x.strip() for x in raw_ids.replace("，", ",").split(",") if x.strip()]
        if raw_ids and not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="log_ids must be a list")
        limit = body.get("limit", 8)
        try:
            limit_n = max(1, min(int(limit or 8), 12))
        except (TypeError, ValueError):
            limit_n = 8
        force = bool(body.get("force"))
        card = await hub.start_log_failure_analysis(
            [str(x) for x in (raw_ids or [])] or None,
            limit=limit_n,
            force=force,
        )
        if card.kind == "error":
            raise HTTPException(status_code=400, detail=card.text)
        tid = card.task_id or ""
        task = hub.supervisor.get_task(tid) if tid else None
        already = bool((card.meta or {}).get("already"))
        return JSONResponse(
            {
                "ok": True,
                "already": already,
                "task_id": tid,
                "status": task.status if task else (card.meta or {}).get("status") or "pending",
                "title": (task.meta or {}).get("title") if task else (card.meta or {}).get("title") or "",
                "goal": task.goal if task else "",
                "log_ids": (card.meta or {}).get("log_ids")
                or ((task.meta or {}).get("log_ids") if task else []),
                "message": card.text,
            }
        )

    @app.get("/api/tasks")
    async def list_tasks_api(
        x_fawai_token: str | None = Header(default=None),
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
        archived: str | None = None,
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        limit = max(1, min(int(limit or 50), 200))
        # archived: 缺省 false；1/true=仅归档；all=全部
        arch_flag: bool | None = False
        raw = (archived or "").strip().lower()
        if raw in {"1", "true", "yes", "archived"}:
            arch_flag = True
        elif raw in {"all", "*"}:
            arch_flag = None
        tasks = hub.supervisor.list_tasks(
            status=status or None,
            q=q,
            limit=limit,
            archived=arch_flag,
        )
        return JSONResponse({"ok": True, "tasks": [_task_summary(t) for t in tasks]})

    @app.post("/api/tasks/{task_id}/archive")
    async def archive_task_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        ok, message = hub.supervisor.archive_task(task.task_id)
        if not ok:
            hub.log_system(
                "archive",
                message=message,
                ok=False,
                task_id=task.task_id,
                actor="api",
            )
            code = 409 if "执行中" in message else 400
            raise HTTPException(status_code=code, detail=message)
        hub.log_system(
            "archive",
            message=f"已归档 {task.task_id[:8]}…",
            ok=True,
            task_id=task.task_id,
            actor="api",
            detail={"status": "archived"},
        )
        return JSONResponse({"ok": True, "task_id": task.task_id, "status": "archived", "message": message})

    @app.post("/api/tasks/{task_id}/unarchive")
    async def unarchive_task_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        ok, message = hub.supervisor.unarchive_task(task.task_id)
        updated = hub.supervisor.get_task(task.task_id)
        status = updated.status if updated else "unknown"
        if not ok:
            hub.log_system(
                "unarchive",
                message=message,
                ok=False,
                task_id=task.task_id,
                actor="api",
                detail={"status": status},
            )
            raise HTTPException(status_code=400, detail=message)
        hub.log_system(
            "unarchive",
            message=f"已取消归档 {task.task_id[:8]}… → {status}",
            ok=True,
            task_id=task.task_id,
            actor="api",
            detail={"status": status},
        )
        return JSONResponse(
            {"ok": True, "task_id": task.task_id, "status": status, "message": message}
        )

    @app.get("/api/tasks/{task_id}")
    async def get_task_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        return JSONResponse({"ok": True, "task": _task_detail(task)})

    @app.get("/api/tasks/{task_id}/history")
    async def get_task_history_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """按任务回放会话信封（含执行时间线 progress）；不限于 /api/session 近段窗口。"""
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        messages = hub.history_for_task(task.task_id)
        return JSONResponse(
            {
                "ok": True,
                "task_id": task.task_id,
                "history": [m.to_dict() for m in messages],
                "count": len(messages),
            }
        )

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        ok = hub.supervisor.cancel(task.task_id)
        updated = hub.supervisor.get_task(task.task_id)
        status = updated.status if updated else None
        msg = "cancelled" if ok else "cancel failed"
        hub.log_system(
            "cancel",
            message=msg,
            ok=ok,
            task_id=task.task_id,
            actor="api",
            detail={"status": status},
        )
        if ok:
            # 立刻推到会话：否则 LLM 规划等待期间页面像「点了没反应」
            await hub.publish(
                Message.progress(
                    "正在停止任务…",
                    task_id=task.task_id,
                    meta={"phase": "cancelling", "status": "cancelled"},
                )
            )
        elif status == "archived":
            raise HTTPException(status_code=409, detail="已归档任务无法停止，请先取消归档")
        elif not ok:
            raise HTTPException(status_code=400, detail=msg or "停止失败")
        return JSONResponse(
            {
                "ok": ok,
                "task_id": task.task_id,
                "status": status,
                "message": msg,
            }
        )

    @app.delete("/api/tasks/{task_id}")
    async def delete_task_api(
        task_id: str,
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        ok, message = hub.supervisor.delete_task(task.task_id)
        if not ok:
            hub.log_system(
                "delete",
                message=message,
                ok=False,
                task_id=task.task_id,
                actor="api",
            )
            if "请先取消" in message:
                raise HTTPException(status_code=409, detail=message)
            raise HTTPException(status_code=400, detail=message)
        hub.log_system(
            "delete",
            message=f"已删除任务 {task.task_id[:8]}…",
            ok=True,
            task_id=task.task_id,
            actor="api",
            detail={"status": "deleted"},
        )
        return JSONResponse({"ok": True, "task_id": task.task_id, "message": message})

    @app.post("/api/tasks/review")
    async def review_tasks_api(
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        """创建复盘任务（单/联合）：先出指令，等对话里放行。body.task_ids 必填。"""
        _check_token(x_fawai_token)
        body = payload or {}
        raw_ids = body.get("task_ids") or body.get("ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [x.strip() for x in raw_ids.replace("，", ",").split(",") if x.strip()]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise HTTPException(status_code=400, detail="task_ids required")

        resolved: list[str] = []
        for item in raw_ids:
            task = _resolve_task(str(item))
            if task is None:
                raise HTTPException(status_code=404, detail=f"unknown task: {item}")
            if task.task_id not in resolved:
                resolved.append(task.task_id)

        card = await hub.start_review(
            resolved,
            distill=bool(body.get("distill", True)),
            ratchet=bool(body.get("ratchet", False)),
        )
        if card.kind == "error":
            raise HTTPException(status_code=400, detail=card.text)
        review_tid = card.task_id or ""
        review_task = hub.supervisor.get_task(review_tid) if review_tid else None
        return JSONResponse(
            {
                "ok": True,
                "phase": "awaiting_approval",
                "task_id": review_tid,
                "source_task_ids": resolved,
                "joint": len(resolved) > 1,
                "status": review_task.status if review_task else "waiting",
                "message": "已创建复盘任务，请在对话中放行分析，再勾选优化项后备份执行",
                "goal": review_task.goal if review_task else "",
            }
        )

    @app.post("/api/tasks/{task_id}/review")
    async def review_task_api(
        task_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        task = _resolve_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="unknown task")
        body = payload or {}
        card = await hub.start_review(
            [task.task_id],
            distill=bool(body.get("distill", True)),
            ratchet=bool(body.get("ratchet", False)),
        )
        if card.kind == "error":
            raise HTTPException(status_code=400, detail=card.text)
        review_tid = card.task_id or ""
        review_task = hub.supervisor.get_task(review_tid) if review_tid else None
        return JSONResponse(
            {
                "ok": True,
                "phase": "awaiting_approval",
                "task_id": review_tid,
                "source_task_ids": [task.task_id],
                "joint": False,
                "status": review_task.status if review_task else "waiting",
                "message": "已创建复盘任务，请在对话中放行分析，再勾选优化项后备份执行",
                "goal": review_task.goal if review_task else "",
            }
        )

    @app.post("/api/messages")
    async def post_message(
        payload: dict[str, Any],
        x_fawai_token: str | None = Header(default=None),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        msg = Message.from_dict({**payload, "role": "user"})
        out = await hub.handle_inbound(msg)
        return JSONResponse({"ok": True, "echo": out.to_dict() if out else None})

    @app.post("/api/upload")
    async def upload_image(
        x_fawai_token: str | None = Header(default=None),
        text: str = Form(default=""),
        task_id: str = Form(default=""),
        file: UploadFile = File(...),
    ) -> JSONResponse:
        _check_token(x_fawai_token)
        suffix = Path(file.filename or "upload.png").suffix or ".png"
        dest = media_dir / f"in_{secrets.token_hex(8)}{suffix}"
        dest.write_bytes(await file.read())
        msg = Message(
            kind="image",
            role="user",
            text=text,
            media_path=str(dest),
            media_url=f"/media/{dest.name}",
            task_id=(task_id or "").strip() or None,
        )
        out = await hub.handle_inbound(msg)
        return JSONResponse(
            {
                "ok": True,
                "echo": out.to_dict() if out else None,
                "inbound": msg.to_dict(),
            }
        )

    @app.post("/api/transcribe")
    async def transcribe_voice(
        x_fawai_token: str | None = Header(default=None),
        language: str = Form(default=""),
        file: UploadFile = File(...),
    ) -> JSONResponse:
        """语音输入：上传录音 → ASR 文本；不入站、不建任务（前端填入输入框）。"""
        # term:语音输入
        _check_token(x_fawai_token)
        from fawai_avatar.llm.asr import MAX_AUDIO_BYTES, mime_for_audio, transcribe_audio

        raw = await file.read()
        if len(raw) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="音频过大，请缩短录音")
        mime = mime_for_audio(file.filename, file.content_type)
        suffix = Path(file.filename or "").suffix
        if not suffix:
            suffix = {
                "audio/wav": ".wav",
                "audio/mpeg": ".mp3",
                "audio/mp4": ".m4a",
                "audio/webm": ".webm",
                "audio/ogg": ".ogg",
            }.get(mime, ".webm")
        dest = media_dir / f"in_audio_{secrets.token_hex(8)}{suffix}"
        dest.write_bytes(raw)
        result = await asyncio.to_thread(
            transcribe_audio,
            raw,
            mime_type=mime,
            language=(language or "").strip() or None,
        )
        if not result.get("ok"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": result.get("error") or "语音识别失败",
                    "text": "",
                    "model": result.get("model"),
                    "media_url": f"/media/{dest.name}",
                },
                status_code=400,
            )
        return JSONResponse(
            {
                "ok": True,
                "text": result.get("text") or "",
                "model": result.get("model"),
                "usage": result.get("usage") or {},
                "media_url": f"/media/{dest.name}",
            }
        )

    @app.get("/media/{name}")
    async def media(name: str) -> FileResponse:
        # 媒体只读；仍要求同网可达。首期不另验 token，文件名不可猜测。
        path = media_dir / Path(name).name
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, token: str = "") -> None:
        if token.strip().upper() != app.state.passcode:
            await websocket.close(code=4401)
            return
        await manager.connect(websocket)
        try:
            # 近段历史由 /api/session；线程内再拉 /api/tasks/{id}/history；此处只推实时出站
            while True:
                data = await websocket.receive_json()
                try:
                    msg = Message.from_dict({**data, "role": "user"})
                    await hub.handle_inbound(msg)
                except WebSocketDisconnect:
                    raise
                except Exception as exc:  # noqa: BLE001 — 单条失败不拆连接
                    logger.exception("ws inbound failed")
                    try:
                        err = Message.error(f"处理失败: {exc}")
                        await websocket.send_json(err.to_dict())
                    except Exception:  # noqa: BLE001
                        pass
        except WebSocketDisconnect:
            await manager.disconnect(websocket)
        except Exception:  # noqa: BLE001
            await manager.disconnect(websocket)
            raise

    return app


def _task_stats_summary(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """列表/详情用的任务统计摘要；无数据返回 None。"""
    from fawai_avatar.task import stats as task_stats

    m = dict(meta or {})
    raw = m.get(task_stats.STATS_KEY)
    if isinstance(raw, dict) and (
        raw.get("rounds")
        or raw.get("llm_calls")
        or raw.get("total_tokens")
        or raw.get("wall_ms")
        or raw.get("tools")
        or raw.get("run_started_at")
    ):
        return task_stats.summary_for_api(raw)
    filled = task_stats.backfill_from_steps(m.get("steps"))
    if (
        filled.get("rounds")
        or filled.get("llm_calls")
        or filled.get("total_tokens")
        or filled.get("tools")
    ):
        return task_stats.summary_for_api(filled)
    return None


def _task_summary(t: Any) -> dict[str, Any]:
    from fawai_avatar.task.title import normalize_task_title, suggest_task_title

    meta = getattr(t, "meta", None) or {}
    title = normalize_task_title(str(meta.get("title") or "")) or suggest_task_title(
        t.goal or ""
    )
    out: dict[str, Any] = {
        "task_id": t.task_id,
        "title": title,
        "title_source": str(meta.get("title_source") or "") or "heuristic",
        "goal": (t.goal or "")[:160],
        "status": t.status,
        "updated_at": getattr(t, "updated_at", 0),
        "created_at": getattr(t, "created_at", 0),
        "has_steps": bool(meta.get("steps")),
        "final_thought": str(meta.get("final_thought") or "")[:200],
    }
    stats = _task_stats_summary(meta if isinstance(meta, dict) else {})
    if stats is not None:
        out["stats"] = stats
    return out


def _task_detail(t: Any) -> dict[str, Any]:
    data = _task_summary(t)
    meta = dict(getattr(t, "meta", None) or {})
    detail_meta: dict[str, Any] = {
        "title": meta.get("title") or data.get("title"),
        "title_source": meta.get("title_source") or data.get("title_source"),
        "final_thought": meta.get("final_thought"),
        "error": meta.get("error"),
        "ok": meta.get("ok"),
        "replan_count": meta.get("replan_count"),
        "steps": meta.get("steps") or [],
        "last_review": meta.get("last_review"),
        "stats": meta.get("stats"),
        "kind": meta.get("kind"),
        "review_phase": meta.get("review_phase"),
    }
    if meta.get("kind") == "review" and meta.get("review_phase") == "awaiting_apply":
        from fawai_avatar.skills.review import items_for_ui

        detail_meta["review_items"] = items_for_ui(meta.get("review_items") or [])
    clar = meta.get("clarification")
    if isinstance(clar, dict) and clar.get("phase") == "awaiting_input":
        detail_meta["clarification"] = {
            "phase": clar.get("phase"),
            "question": clar.get("question"),
            "options": clar.get("options") or [],
        }
    data["meta"] = detail_meta
    return data


def run_lan_gateway(
    *,
    host: str = "0.0.0.0",
    port: int = 7980,
    data_dir: str = "data",
    passcode: str | None = None,
    auto_run_worker: bool = True,
    dry_run: bool = True,
    mock_brain: bool = True,
    capture: bool = False,
) -> None:
    """启动局域网会话网关（阻塞）。"""
    import uvicorn

    from fawai_avatar.channel.hub import SessionHub
    from fawai_avatar.channel.relay import relay_from_env
    from fawai_avatar.runtime.console_log import configure_logging, cprint, uvicorn_log_config
    from fawai_avatar.runtime.loop import WorkerLoop
    from fawai_avatar.task.supervisor import Supervisor

    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    configure_logging(data_dir=root)

    # Web 入口幂等请求权限（main() 已请求则直接返回当前状态）
    perm_report = None
    try:
        from fawai_avatar.control.permissions_mac import ensure_startup_permissions, is_macos

        if is_macos():
            perm_report = ensure_startup_permissions(
                accessibility=True,
                screen_recording=True,
            )
    except Exception:  # noqa: BLE001
        perm_report = None

    media_dir = root / "session_media"
    configured = (passcode or "").strip()
    code = configured.upper() if configured else generate_passcode()
    passcode_source = "环境变量 FAWAI_GATEWAY_PASSCODE" if configured else "本次随机"
    supervisor = Supervisor(str(root))
    hub_holder: dict[str, SessionHub | None] = {"hub": None}

    def worker(task_id: str) -> dict[str, Any]:
        import os

        from fawai_avatar.control.executor import Controller

        if mock_brain:
            os.environ["FAWAI_BRAIN_MOCK"] = "1"
        else:
            os.environ.pop("FAWAI_BRAIN_MOCK", None)

        def on_progress(text: str, meta: dict[str, Any]) -> None:
            h = hub_holder["hub"]
            if h is not None:
                h.emit_progress(text, task_id=task_id, meta=meta)

        from fawai_avatar.config import max_steps_from_env

        h = hub_holder["hub"]
        store = h.system_log if h is not None else None
        loop = WorkerLoop(
            supervisor,
            controller=Controller(
                dry_run=dry_run, supervisor=supervisor, system_log=store
            ),
            max_steps=max_steps_from_env(default=0),
            capture=capture,
            on_progress=on_progress,
        )
        return loop.run(task_id)

    hub = SessionHub(supervisor, worker=worker, auto_run_worker=auto_run_worker)
    hub_holder["hub"] = hub
    if not getattr(hub.system_log, "available", True):
        cprint(
            "  ⚠ 系统日志库降级："
            f"{getattr(hub.system_log, 'init_error', '')}；"
            "Web 仍可用，请打开左侧「服务与版本」查看后台日志 / 一键修复"
        )
    relay = relay_from_env()
    releases = releases_from_env(root)

    async def _relay_inbound(message: Message) -> None:
        await hub.handle_inbound(message)

    relay.on_message(_relay_inbound)
    app = create_app(
        hub,
        passcode=code,
        media_dir=media_dir,
        relay=relay,
        releases=releases,
    )

    frpc_cfg = None
    try:
        from fawai_avatar.channel.frpc_tunnel import ensure_frpc_for_gateway, stop_frpc

        frpc_cfg = ensure_frpc_for_gateway(local_port=port)
    except Exception as exc:  # noqa: BLE001
        cprint(f"  frpc 穿透启动失败: {exc}")
        frpc_cfg = None

    lan_ip = guess_lan_ip()
    url = f"http://{lan_ip}:{port}/"
    from fawai_avatar.config import max_steps_from_env
    from fawai_avatar.platform_info import runtime_platform

    plat = runtime_platform()
    cprint("=" * 60)
    cprint("fawai-avatar 会话网关（局域网）")
    cprint(f"  手机打开: {url}")
    if frpc_cfg is not None:
        cprint(f"  公网穿透: {frpc_cfg.public_url}（frpc → {frpc_cfg.server_addr}:{frpc_cfg.remote_port}）")
    else:
        cprint("  公网穿透: 未启用（配 FAWAI_FRP_SERVER_ADDR + FAWAI_FRP_REMOTE_PORT）")
    cprint(f"  口令:     {code}（{passcode_source}）")
    cprint(
        f"  平台:     {plat.system}/{plat.machine or '?'}；"
        f"{'headless' if plat.headless else 'desktop'}（{plat.headless_source}）"
    )
    cprint(f"  大脑:     {'mock' if mock_brain else 'LLM（读 .env 模型）'}")
    cprint(f"  控制:     {'dry_run（不真点）' if dry_run else '实操'}")
    _max_steps = max_steps_from_env(default=0)
    cprint(
        f"  观察:     {'允许截屏候选' if capture else '禁截屏（结构化/跳过）'}；方式按目标选取"
    )
    if plat.headless:
        cprint("  GUI:      已禁用（无界面）；编码/文件/shell 可用")
    cprint(
        f"  最大步数: {'不限制（FAWAI_MAX_STEPS=0）' if _max_steps <= 0 else _max_steps}"
    )
    if perm_report is not None:
        cprint(f"  macOS权限: {perm_report.summary_line()}")
    cprint("  同网可达即可；控制口令示例: 暂停 <task前缀> / 取消 <task前缀>")
    if relay.enabled:
        cprint(f"  云端中继: 已配置 {relay.relay_url}（骨架挂载）")
    else:
        cprint("  云端中继: 未配置 FAWAI_RELAY_URL（可后置）")
    cprint("=" * 60)

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            log_config=uvicorn_log_config("info"),
        )
    finally:
        try:
            from fawai_avatar.channel.frpc_tunnel import stop_frpc

            stop_frpc()
        except Exception:  # noqa: BLE001
            pass


def _env_flag(name: str, default: str = "0") -> bool:
    import os

    return (os.environ.get(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def _raise_nofile_soft_limit(*, floor: int = 4096) -> None:
    """macOS 常默认 soft nofile=256；SQLite/WS 易顶满。尽量抬到 floor（不超过 hard）。"""
    try:
        import resource
    except ImportError:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError):
        return
    if hard <= 0:
        return
    target = min(hard, max(int(soft), int(floor)))
    if target <= soft:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (OSError, ValueError):
        return


def main() -> None:
    """Web 主入口：先一次性请求 macOS 权限，再启动会话网关。

    默认经服务保活外层拉起（崩溃/Web 重启可自动存活）。
    调试可设 FAWAI_KEEPER=0 或 FAWAI_NO_KEEPER=1 直接跑网关。
    """
    from fawai_avatar.config import RuntimeConfig, load_dotenv
    from fawai_avatar.control.permissions_mac import ensure_startup_permissions, is_macos

    load_dotenv()
    _raise_nofile_soft_limit()

    # 已在保活子进程内，或显式关闭保活 → 直接跑网关
    already_kept = _env_flag("FAWAI_KEEPER", "0")
    no_keeper = _env_flag("FAWAI_NO_KEEPER", "0")
    if not already_kept and not no_keeper:
        from fawai_avatar.runtime.keeper import run_keeper

        cfg = RuntimeConfig.from_env()
        run_keeper(data_dir=cfg.data_dir)
        return

    # 主入口获取权限（辅助功能 + 屏幕录制）；任务执行中不再弹窗
    if is_macos():
        from fawai_avatar.runtime.console_log import cprint

        cprint("正在检查 / 请求 macOS 权限（辅助功能、屏幕录制）…")
        report = ensure_startup_permissions(
            accessibility=True,
            screen_recording=True,
        )
        cprint(f"  → {report.summary_line()}")
        if not report.accessibility or not report.screen_recording:
            cprint(
                "  → 若刚勾选授权，请重启本进程后再用实操/截屏；"
                "未授权时任务内不会反复弹窗。"
            )

    cfg = RuntimeConfig.from_env()
    host = (os.environ.get("FAWAI_GATEWAY_HOST") or "0.0.0.0").strip()
    port = int((os.environ.get("FAWAI_GATEWAY_PORT") or "7980").strip())
    passcode = (os.environ.get("FAWAI_GATEWAY_PASSCODE") or "").strip() or None
    # 默认关截屏；无界面强制关（即使误设 FAWAI_GATEWAY_CAPTURE=1）
    capture = _env_flag("FAWAI_GATEWAY_CAPTURE", "0") and not cfg.headless
    run_lan_gateway(
        host=host,
        port=port,
        data_dir=cfg.data_dir,
        passcode=passcode,
        auto_run_worker=True,
        dry_run=cfg.dry_run,
        mock_brain=_env_flag("FAWAI_BRAIN_MOCK", "0"),
        capture=capture,
    )


if __name__ == "__main__":
    main()
