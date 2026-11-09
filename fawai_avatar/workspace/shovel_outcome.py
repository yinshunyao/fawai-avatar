# term:成败经验与检索极性  doc:doc/功能/成败经验与检索极性/说明.md
"""铲子运行态成败账（与设计态三维分并列，禁止混用）。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MIN_SAMPLES = 2
_LEDGER_NAME = "shovel_outcomes.json"

_SHOVEL_PATH_RE = re.compile(
    r"(?:fawai_shovels|hipp-tools)/(?:shell|python|media|scaffold)/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)\.(?:py|sh)\b",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_shovel_key(shovel_id: str) -> str:
    """文件名常连字符、台账 id 常下划线；账本键统一成下划线。"""
    return str(shovel_id or "").strip().replace("-", "_")


def ledger_path(data_dir: str | Path) -> Path:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / _LEDGER_NAME


def rate_from_counts(calls_ok: int, calls_fail: int, *, min_samples: int = _MIN_SAMPLES) -> str:
    ok = max(0, int(calls_ok or 0))
    fail = max(0, int(calls_fail or 0))
    total = ok + fail
    if total < max(1, int(min_samples)):
        return "unknown"
    ratio = ok / total
    if ratio >= 0.7:
        return "good"
    if ratio >= 0.4:
        return "mixed"
    return "poor"


def _empty_entry() -> dict[str, Any]:
    return {
        "calls_ok": 0,
        "calls_fail": 0,
        "last_fail_one_liner": "",
        "last_ok_at": "",
        "last_fail_at": "",
        "updated_at": "",
    }


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalize_outcome(raw: dict[str, Any] | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    ok = max(0, int(src.get("calls_ok") or 0))
    fail = max(0, int(src.get("calls_fail") or 0))
    return {
        "calls_ok": ok,
        "calls_fail": fail,
        "rate": rate_from_counts(ok, fail),
        "last_fail_one_liner": str(src.get("last_fail_one_liner") or "")[:240],
        "last_ok_at": str(src.get("last_ok_at") or ""),
        "last_fail_at": str(src.get("last_fail_at") or ""),
        "updated_at": str(src.get("updated_at") or ""),
    }


def get_outcome(data_dir: str | Path, shovel_id: str) -> dict[str, Any]:
    sid = normalize_shovel_key(shovel_id)
    if not sid:
        return normalize_outcome(None)
    with _LOCK:
        data = _load(ledger_path(data_dir))
        raw = data.get(sid)
        if not isinstance(raw, dict):
            # 兼容旧键（连字符）
            raw = data.get(str(shovel_id or "").strip())
        return normalize_outcome(raw if isinstance(raw, dict) else None)


def set_outcome(
    data_dir: str | Path,
    shovel_id: str,
    *,
    calls_ok: int | None = None,
    calls_fail: int | None = None,
    last_fail_one_liner: str | None = None,
) -> dict[str, Any]:
    """手工改写运行成败账（管理页）；未传字段保留原值。"""
    sid = normalize_shovel_key(shovel_id)
    if not sid:
        return normalize_outcome(None)
    now = _utc_now()
    with _LOCK:
        path = ledger_path(data_dir)
        data = _load(path)
        prev = data.get(sid)
        if not isinstance(prev, dict):
            prev = data.get(str(shovel_id or "").strip())
        entry = dict(prev if isinstance(prev, dict) else _empty_entry())
        if calls_ok is not None:
            entry["calls_ok"] = max(0, int(calls_ok))
        if calls_fail is not None:
            entry["calls_fail"] = max(0, int(calls_fail))
        if last_fail_one_liner is not None:
            entry["last_fail_one_liner"] = re.sub(
                r"\s+", " ", str(last_fail_one_liner).strip()
            )[:240]
        entry["updated_at"] = now
        data[sid] = entry
        # 清理旧连字符键，避免双份
        hyphen = str(shovel_id or "").strip()
        if hyphen and hyphen != sid and hyphen in data:
            del data[hyphen]
        _save(path, data)
        return normalize_outcome(entry)


def record_outcome(
    data_dir: str | Path,
    shovel_id: str,
    *,
    ok: bool,
    message: str = "",
) -> dict[str, Any]:
    sid = normalize_shovel_key(shovel_id)
    if not sid:
        return normalize_outcome(None)
    now = _utc_now()
    fail_line = ""
    if not ok:
        fail_line = re.sub(r"\s+", " ", str(message or "").strip())[:240]
    with _LOCK:
        path = ledger_path(data_dir)
        data = _load(path)
        entry = dict(data.get(sid) if isinstance(data.get(sid), dict) else _empty_entry())
        if ok:
            entry["calls_ok"] = int(entry.get("calls_ok") or 0) + 1
            entry["last_ok_at"] = now
        else:
            entry["calls_fail"] = int(entry.get("calls_fail") or 0) + 1
            entry["last_fail_at"] = now
            if fail_line:
                entry["last_fail_one_liner"] = fail_line
        entry["updated_at"] = now
        data[sid] = entry
        _save(path, data)
        return normalize_outcome(entry)


def extract_shovel_ids_from_cmd(cmd: str) -> list[str]:
    text = str(cmd or "")
    if not text:
        return []
    found: list[str] = []
    for m in _SHOVEL_PATH_RE.finditer(text):
        stem = str(m.group(1) or "").strip()
        if stem and stem not in found:
            found.append(stem)
    return found


def extract_shovel_ids_from_actions(actions: list[Any] | None) -> list[str]:
    out: list[str] = []
    for action in actions or []:
        kind = str(getattr(action, "kind", None) or "").strip().lower()
        if kind != "shell":
            continue
        args = getattr(action, "args", None) or {}
        if not isinstance(args, dict):
            continue
        cmd = args.get("cmd") or args.get("command") or args.get("argv")
        if isinstance(cmd, list):
            cmd_text = " ".join(str(x) for x in cmd)
        else:
            cmd_text = str(cmd or "")
        for sid in extract_shovel_ids_from_cmd(cmd_text):
            if sid not in out:
                out.append(sid)
    return out


def enrich_shovel_hit(hit: dict[str, Any], data_dir: str | Path | None) -> dict[str, Any]:
    row = dict(hit)
    sid = str(row.get("id") or "").strip()
    if not sid and row.get("path"):
        # path like shell/foo.sh → foo
        stem = Path(str(row.get("path"))).stem
        sid = stem
        row["id"] = sid
    if data_dir is None or not sid:
        row["outcome"] = normalize_outcome(None)
        return row
    row["outcome"] = get_outcome(data_dir, sid)
    return row


def maybe_record_shell_outcome(
    *,
    data_dir: str | Path | None,
    cmd: str,
    ok: bool,
    message: str = "",
) -> list[dict[str, Any]]:
    """shell 命中铲路径时记账；返回写入的 outcome 列表。"""
    if not data_dir:
        return []
    recorded: list[dict[str, Any]] = []
    for sid in extract_shovel_ids_from_cmd(cmd):
        recorded.append(
            {
                "id": sid,
                "outcome": record_outcome(data_dir, sid, ok=ok, message=message),
            }
        )
    return recorded
