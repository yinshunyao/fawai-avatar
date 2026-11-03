# term:运行时版本  doc:doc/功能/运行时版本/说明.md
"""产品代码存档：快照 / 列表 / 删除 / 激活（供保活进程切换后重启）。"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_RESTART = 75
EXIT_ACTIVATE = 76

_SKIP_DIR_NAMES = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", "data", "node_modules"})
_SKIP_FILE_NAMES = frozenset({".env", ".DS_Store"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_release_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def package_root() -> Path:
    """当前运行中的 fawai_avatar 包所在工程根（含 fawai_avatar/ 的上一级）。"""
    return Path(__file__).resolve().parents[2]


def package_dir() -> Path:
    return Path(__file__).resolve().parents[1]


class ReleaseStore:
    """拷贝式产品存档；与工作区「版本管理」分离。"""

    def __init__(self, releases_dir: str | Path) -> None:
        self._root = Path(releases_dir)
        self._history = self._root / "history"
        self._index = self._root / "index.jsonl"
        self._command = self._root / "command.json"
        self._active = self._root / "active.json"
        self._lock = threading.RLock()
        self._root.mkdir(parents=True, exist_ok=True)
        self._history.mkdir(parents=True, exist_ok=True)
        if not self._index.exists():
            self._index.touch()

    @property
    def releases_dir(self) -> Path:
        return self._root

    def status(self) -> dict[str, Any]:
        active = self.read_active()
        return {
            "package_root": str(package_root()),
            "package_dir": str(package_dir()),
            "active": active,
            "keeper": os.environ.get("FAWAI_KEEPER", "").strip() in {"1", "true", "yes", "on"},
            "pending_command": self.read_command(),
            "count": len(self.list_releases(limit=500)),
        }

    def snapshot(self, *, message: str = "", label: str = "") -> str:
        release_id = _new_release_id()
        dest = self._history / release_id / "fawai_avatar"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = package_dir()
        shutil.copytree(src, dest, ignore=self._ignore)
        # 附带工程元数据，便于辨认
        meta_src = package_root() / "pyproject.toml"
        if meta_src.is_file():
            shutil.copy2(meta_src, dest.parent / "pyproject.toml")
        manifest: dict[str, Any] = {
            "id": release_id,
            "created_at": _utc_now_iso(),
            "message": (message or "").strip(),
            "label": (label or "").strip(),
            "source_package": str(src),
        }
        (dest.parent / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self._lock:
            with self._index.open("a", encoding="utf-8") as f:
                f.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        return release_id

    def list_releases(self, *, limit: int = 50) -> list[dict[str, Any]]:
        entries = self._read_index()
        entries.reverse()
        return entries[: max(0, limit)]

    def get(self, release_id: str) -> dict[str, Any]:
        path = self._history / release_id
        manifest_path = path / "manifest.json"
        exists = path.is_dir() and (path / "fawai_avatar").is_dir()
        manifest = None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {"id": release_id, "exists": exists, "manifest": manifest, "path": str(path)}

    def delete(self, release_id: str) -> bool:
        with self._lock:
            path = self._history / release_id
            if not path.is_dir():
                return False
            shutil.rmtree(path)
            entries = [e for e in self._read_index() if e.get("id") != release_id]
            with self._index.open("w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True

    def request_restart(self) -> dict[str, Any]:
        cmd = {"action": "restart", "requested_at": _utc_now_iso()}
        self._write_command(cmd)
        return cmd

    def request_activate(self, release_id: str) -> dict[str, Any]:
        info = self.get(release_id)
        if not info["exists"]:
            raise ValueError(f"unknown release: {release_id}")
        cmd = {
            "action": "activate",
            "release_id": release_id,
            "requested_at": _utc_now_iso(),
        }
        self._write_command(cmd)
        return cmd

    def apply_activate(self, release_id: str) -> dict[str, Any]:
        """将存档覆盖到当前包目录；调用方应随后重启进程。"""
        info = self.get(release_id)
        if not info["exists"]:
            raise ValueError(f"unknown release: {release_id}")
        src = Path(info["path"]) / "fawai_avatar"
        dst = package_dir()
        # 先把当前树备份成自动存档，避免误覆盖不可恢复
        auto_id = self.snapshot(message=f"auto:before-activate:{release_id}", label="auto-backup")
        tmp = dst.parent / f".fawai_avatar_swap_{secrets.token_hex(4)}"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp, ignore=self._ignore)
        backup = dst.parent / f".fawai_avatar_old_{secrets.token_hex(4)}"
        dst.rename(backup)
        try:
            tmp.rename(dst)
        except Exception:
            backup.rename(dst)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        active = {
            "release_id": release_id,
            "activated_at": _utc_now_iso(),
            "backup_release_id": auto_id,
        }
        self._active.write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.clear_command()
        return active

    def read_command(self) -> dict[str, Any] | None:
        if not self._command.is_file():
            return None
        try:
            data = json.loads(self._command.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def clear_command(self) -> None:
        if self._command.is_file():
            self._command.unlink()

    def read_active(self) -> dict[str, Any] | None:
        if not self._active.is_file():
            return None
        try:
            data = json.loads(self._active.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _write_command(self, cmd: dict[str, Any]) -> None:
        with self._lock:
            self._command.write_text(json.dumps(cmd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_index(self) -> list[dict[str, Any]]:
        if not self._index.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self._index.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
        return out

    @staticmethod
    def _ignore(directory: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            if name in _SKIP_DIR_NAMES or name in _SKIP_FILE_NAMES:
                skipped.add(name)
            elif name.startswith(".") and name not in {".keep"}:
                # 跳过隐藏垃圾；保留显式需要的可见文件
                if name not in {".gitignore"}:
                    skipped.add(name)
        return skipped


def releases_from_env(data_dir: str | Path | None = None) -> ReleaseStore:
    if data_dir is None:
        data_dir = os.environ.get("FAWAI_DATA_DIR") or "data"
    return ReleaseStore(Path(data_dir) / "runtime_releases")
