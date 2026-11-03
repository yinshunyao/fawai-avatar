# term:会话 Diff  tool:diff_confirm  doc:doc/功能/编码闭环/说明.md
"""confirm 模式下的暂存补丁。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


class PendingDiffStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "pending_diffs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, pending_id: str) -> Path:
        safe = "".join(c for c in pending_id if c.isalnum() or c in "-_")
        return self.root / f"{safe}.json"

    def put(
        self,
        *,
        task_id: str | None,
        path: str,
        old_text: str,
        new_text: str,
        kind: str,
        diff: str,
    ) -> dict[str, Any]:
        pending_id = uuid.uuid4().hex[:12]
        rec = {
            "id": pending_id,
            "task_id": task_id,
            "path": path,
            "old_text": old_text,
            "new_text": new_text,
            "kind": kind,
            "diff": diff,
            "created_at": time.time(),
            "status": "pending",
        }
        self._path(pending_id).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec

    def get(self, pending_id: str) -> dict[str, Any] | None:
        p = self._path(pending_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if rec.get("task_id") == task_id and rec.get("status") == "pending":
                out.append(rec)
        return out

    def mark(self, pending_id: str, status: str) -> dict[str, Any] | None:
        rec = self.get(pending_id)
        if rec is None:
            return None
        rec["status"] = status
        self._path(pending_id).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec


def unified_diff(path: str, old: str, new: str, *, max_lines: int = 200) -> str:
    import difflib

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["…(diff truncated)\n"]
    return "".join(lines)
