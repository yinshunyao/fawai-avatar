"""进程内重建进度（Web 轮询 / 工具旁路可读）。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

_lock = threading.RLock()
_job_thread: threading.Thread | None = None

_state: dict[str, Any] = {
    "running": False,
    "which": "",
    "phase": "idle",
    "message": "",
    "indexed": 0,
    "total": None,
    "roots": [],
    "error": None,
    "started_at": None,
    "updated_at": None,
    "elapsed_s": 0.0,
    "result": None,
}


def snapshot() -> dict[str, Any]:
    with _lock:
        out = dict(_state)
        started = out.get("started_at")
        if started and out.get("running"):
            out["elapsed_s"] = round(time.time() - float(started), 1)
        elif started and out.get("updated_at"):
            out["elapsed_s"] = round(float(out["updated_at"]) - float(started), 1)
        return out


def begin(which: str, *, message: str = "") -> dict[str, Any]:
    with _lock:
        now = time.time()
        _state.update(
            {
                "running": True,
                "which": str(which or ""),
                "phase": "starting",
                "message": message or f"开始重建 {which}",
                "indexed": 0,
                "total": None,
                "roots": [],
                "error": None,
                "started_at": now,
                "updated_at": now,
                "elapsed_s": 0.0,
                "result": None,
            }
        )
        return dict(_state)


def update(
    *,
    phase: str | None = None,
    message: str | None = None,
    indexed: int | None = None,
    total: int | None = None,
    roots: list[str] | None = None,
) -> None:
    with _lock:
        if phase is not None:
            _state["phase"] = phase
        if message is not None:
            _state["message"] = message
        if indexed is not None:
            _state["indexed"] = int(indexed)
        if total is not None:
            _state["total"] = int(total) if total >= 0 else None
        if roots is not None:
            _state["roots"] = list(roots)
        _state["updated_at"] = time.time()


def finish(*, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    with _lock:
        now = time.time()
        ok = error is None and (result is None or bool(result.get("ok", True)))
        _state.update(
            {
                "running": False,
                "phase": "done" if ok else "error",
                "message": (
                    error
                    or (result or {}).get("error")
                    or _state.get("message")
                    or ("完成" if ok else "失败")
                ),
                "error": error or ((result or {}).get("error") if not ok else None),
                "result": result,
                "updated_at": now,
            }
        )
        if result and result.get("indexed") is not None:
            _state["indexed"] = int(result["indexed"])
        started = _state.get("started_at")
        if started:
            _state["elapsed_s"] = round(now - float(started), 1)
        return dict(_state)


def is_running() -> bool:
    with _lock:
        return bool(_state.get("running"))


def report_progress(**kwargs: Any) -> None:
    """供索引实现回调：phase/message/indexed/total/roots。"""
    update(**{k: v for k, v in kwargs.items() if v is not None})


def _run_target(which: str, fn: Callable[[], dict[str, Any]]) -> None:
    try:
        begin(which, message=f"正在重建：{which}")
        result = fn() or {}
        if result.get("ok") is False:
            finish(result=result, error=str(result.get("error") or "重建失败"))
        else:
            indexed = result.get("indexed")
            if indexed is None and isinstance(result.get("total"), int):
                indexed = result["total"]
            msg = f"重建完成：{which}"
            if indexed is not None:
                msg = f"重建完成：{which} · {indexed} 条"
            update(phase="done", message=msg, indexed=indexed)
            finish(result=result)
    except Exception as exc:  # noqa: BLE001
        finish(error=str(exc))


def start_job(which: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """后台启动重建；已在跑则返回冲突快照。"""
    global _job_thread
    with _lock:
        if _state.get("running") and _job_thread is not None and _job_thread.is_alive():
            snap = dict(_state)
            snap["ok"] = False
            snap["started"] = False
            snap["error"] = snap.get("error") or "已有重建任务进行中"
            snap["busy"] = True
            return snap
        # 先占位，避免并发双开；真正进度由 _run_target → begin 覆盖
        _state["running"] = True
        _state["which"] = str(which or "")
        _state["phase"] = "starting"
        _state["message"] = f"排队启动：{which}"
        _state["error"] = None
        _state["result"] = None
        _state["updated_at"] = time.time()
        t = threading.Thread(
            target=_run_target,
            args=(which, fn),
            name=f"memory-reindex-{which}",
            daemon=True,
        )
        _job_thread = t
        t.start()
    return {"ok": True, "started": True, "which": which, "progress": snapshot()}


def run_job(which: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """同步跑（工具侧）；同样写进度。"""
    if is_running():
        snap = snapshot()
        return {
            "ok": False,
            "error": "已有重建任务进行中",
            "busy": True,
            "progress": snap,
        }
    begin(which, message=f"正在重建：{which}")
    try:
        result = fn() or {}
        if result.get("ok") is False:
            finish(result=result, error=str(result.get("error") or "重建失败"))
            return result
        indexed = result.get("indexed")
        if indexed is None and isinstance(result.get("total"), int):
            indexed = result["total"]
        update(
            phase="done",
            message=f"重建完成：{which}" + (f" · {indexed} 条" if indexed is not None else ""),
            indexed=indexed,
        )
        finish(result=result)
        return result
    except Exception as exc:  # noqa: BLE001
        finish(error=str(exc))
        return {"ok": False, "error": str(exc)}
