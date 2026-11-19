# term:修改与产物浏览  doc:doc/功能/02-任务线/06-修改与产物浏览/说明.md
"""任务级完整 Diff 落盘，供会话卡懒加载全文。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class DiffStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "diff_artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def _task_dir(self, task_id: str) -> Path:
        safe = "".join(c for c in task_id if c.isalnum() or c in "-_") or "unknown"
        d = self.root / safe
        d.mkdir(parents=True, exist_ok=True)
        return d

    def put(
        self,
        *,
        task_id: str | None,
        path: str,
        diff: str,
        pending: bool = False,
        pending_id: str | None = None,
        added: int = 0,
        deleted: int = 0,
        kind: str | None = None,
    ) -> dict[str, Any]:
        tid = (task_id or "").strip() or "orphan"
        diff_id = uuid.uuid4().hex[:12]
        rec: dict[str, Any] = {
            "id": diff_id,
            "task_id": tid,
            "path": path,
            "diff": diff,
            "pending": bool(pending),
            "pending_id": pending_id,
            "added": int(added),
            "deleted": int(deleted),
            "kind": kind,
            "created_at": time.time(),
        }
        out = self._task_dir(tid) / f"{diff_id}.json"
        out.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec

    def get(self, task_id: str, diff_id: str) -> dict[str, Any] | None:
        safe_t = "".join(c for c in task_id if c.isalnum() or c in "-_")
        safe_d = "".join(c for c in diff_id if c.isalnum() or c in "-_")
        p = self.root / safe_t / f"{safe_d}.json"
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        safe_t = "".join(c for c in task_id if c.isalnum() or c in "-_")
        d = self.root / safe_t
        if not d.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            slim = {k: v for k, v in rec.items() if k != "diff"}
            slim["truncated"] = bool(rec.get("diff"))
            out.append(slim)
        return out
