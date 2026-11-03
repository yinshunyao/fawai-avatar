# term:版本管理  tool:version_*  doc:doc/功能/版本管理/说明.md
from __future__ import annotations

import json
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SKIP_DIR_NAMES = frozenset({".git", ".venv", "__pycache__", ".env"})
_SKIP_FILE_NAMES = frozenset({".env"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_version_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(4)}"


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/")


class VersionStore:
    """拷贝式版本库：快照、查询、回退、修剪。"""

    def __init__(self, versions_dir: str | Path, work_root: str | Path) -> None:
        self._versions_dir = Path(versions_dir)
        self._work_root = Path(work_root).resolve()
        self._history_dir = self._versions_dir / "history"
        self._index_path = self._versions_dir / "index.jsonl"
        self._lock = threading.RLock()
        self._versions_dir.mkdir(parents=True, exist_ok=True)
        self._history_dir.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._index_path.touch()

    @property
    def versions_dir(self) -> Path:
        return self._versions_dir

    @property
    def work_root(self) -> Path:
        return self._work_root

    def snapshot(
        self,
        paths: list[str] | None = None,
        *,
        message: str = "",
        recursive_dirs: bool = True,
    ) -> str:
        if not paths:
            raise ValueError("snapshot requires explicit non-empty paths")

        rel_files = self._collect_snapshot_paths(paths, recursive_dirs=recursive_dirs)
        version_id = _new_version_id()
        version_dir = self._history_dir / version_id
        files_dir = version_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        for rel in rel_files:
            src = self._work_root / rel
            dst = files_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        manifest: dict[str, Any] = {
            "id": version_id,
            "created_at": _utc_now_iso(),
            "message": message,
            "paths": rel_files,
        }
        self._write_manifest(version_dir, manifest)
        self._append_index(manifest)
        return version_id

    def snapshot_before_write(
        self,
        paths: list[str],
        message: str = "auto:before-write",
    ) -> str:
        return self.snapshot(paths, message=message, recursive_dirs=False)

    def list_versions(
        self,
        *,
        path_prefix: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        entries = self._read_index_newest_first()
        if path_prefix is not None:
            norm_prefix = _norm_rel(path_prefix.strip("/"))

            def matches(entry: dict[str, Any]) -> bool:
                for p in entry.get("paths") or []:
                    rp = _norm_rel(p)
                    if not norm_prefix:
                        return True
                    if rp == norm_prefix or rp.startswith(norm_prefix + "/"):
                        return True
                return False

            entries = [e for e in entries if matches(e)]
        return entries[: max(0, limit)]

    def get(self, version_id: str) -> dict[str, Any]:
        version_dir = self._history_dir / version_id
        exists = version_dir.is_dir()
        manifest = self._read_manifest(version_dir) if exists else None
        file_list: list[str] = []
        if manifest is not None:
            file_list = list(manifest.get("paths") or [])
        elif exists:
            files_root = version_dir / "files"
            if files_root.is_dir():
                for p in sorted(files_root.rglob("*")):
                    if p.is_file():
                        file_list.append(_norm_rel(str(p.relative_to(files_root))))
        return {
            "manifest": manifest,
            "exists": exists,
            "files": file_list,
        }

    def delete(self, version_id: str) -> bool:
        with self._lock:
            version_dir = self._history_dir / version_id
            if not version_dir.is_dir():
                return False
            shutil.rmtree(version_dir)
            self._rewrite_index_without(version_id)
            return True

    def rollback(
        self,
        version_id: str,
        *,
        paths: list[str] | None = None,
    ) -> dict[str, list[str]]:
        info = self.get(version_id)
        if not info["exists"] or info["manifest"] is None:
            raise ValueError(f"unknown or corrupt version: {version_id}")

        manifest_paths = [_norm_rel(p) for p in info["manifest"].get("paths") or []]
        if paths is not None:
            wanted = {_norm_rel(p) for p in paths}
            to_restore = [p for p in manifest_paths if p in wanted]
        else:
            to_restore = manifest_paths

        files_dir = self._history_dir / version_id / "files"
        restored: list[str] = []
        missing: list[str] = []

        for rel in to_restore:
            src = files_dir / rel
            if not src.is_file():
                missing.append(rel)
                continue
            dst = self._work_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)

        return {"restored": restored, "missing": missing}

    def prune(self, keep: int) -> list[str]:
        if keep < 0:
            raise ValueError("keep must be >= 0")
        entries = self._read_index_newest_first()
        to_drop = entries[keep:]
        removed: list[str] = []
        for entry in to_drop:
            vid = entry.get("id")
            if isinstance(vid, str) and self.delete(vid):
                removed.append(vid)
        return removed

    def _collect_snapshot_paths(
        self,
        paths: list[str],
        *,
        recursive_dirs: bool,
    ) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []

        def add_file(rel: str) -> None:
            rel = _norm_rel(rel)
            if rel in seen:
                return
            seen.add(rel)
            ordered.append(rel)

        for raw in paths:
            target = (self._work_root / raw).resolve()
            try:
                target.relative_to(self._work_root)
            except ValueError as exc:
                raise ValueError(f"path outside work_root: {raw}") from exc

            if not target.exists():
                raise FileNotFoundError(raw)

            if target.is_file():
                if self._should_skip_path(target):
                    continue
                add_file(_norm_rel(str(target.relative_to(self._work_root))))
                continue

            if not target.is_dir():
                continue

            if not recursive_dirs:
                raise ValueError(f"directory requires recursive_dirs or list files: {raw}")

            for file_path in target.rglob("*"):
                if not file_path.is_file():
                    continue
                if self._path_has_skipped_part(file_path):
                    continue
                rel = _norm_rel(str(file_path.relative_to(self._work_root)))
                add_file(rel)

        return ordered

    def _should_skip_path(self, path: Path) -> bool:
        if path.name in _SKIP_FILE_NAMES:
            return True
        return self._path_has_skipped_part(path)

    def _path_has_skipped_part(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self._work_root)
        except ValueError:
            return True
        for part in rel.parts:
            if part in _SKIP_DIR_NAMES:
                return True
        return False

    def _manifest_path(self, version_dir: Path) -> Path:
        return version_dir / "manifest.json"

    def _write_manifest(self, version_dir: Path, manifest: dict[str, Any]) -> None:
        path = self._manifest_path(version_dir)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_manifest(self, version_dir: Path) -> dict[str, Any] | None:
        path = self._manifest_path(version_dir)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _append_index(self, manifest: dict[str, Any]) -> None:
        line = json.dumps(manifest, ensure_ascii=False)
        with self._lock:
            with self._index_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _read_index_lines(self) -> list[dict[str, Any]]:
        if not self._index_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self._index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
        return entries

    def _read_index_newest_first(self) -> list[dict[str, Any]]:
        entries = self._read_index_lines()
        entries.reverse()
        return entries

    def _rewrite_index_without(self, version_id: str) -> None:
        entries = self._read_index_lines()
        kept = [e for e in entries if e.get("id") != version_id]
        with self._lock:
            with self._index_path.open("w", encoding="utf-8") as f:
                for entry in kept:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import tempfile

    WORK_ROOT = tempfile.mkdtemp(prefix="fawai-version-work-")
    VERSIONS_DIR = tempfile.mkdtemp(prefix="fawai-version-store-")

    root = Path(WORK_ROOT)
    (root / "notes").mkdir()
    sample = root / "notes" / "hello.txt"
    sample.write_text("v1\n", encoding="utf-8")

    store = VersionStore(VERSIONS_DIR, WORK_ROOT)
    vid1 = store.snapshot(["notes/hello.txt"], message="initial")
    assert vid1.count("-") >= 2

    sample.write_text("v2\n", encoding="utf-8")
    vid2 = store.snapshot_before_write(["notes/hello.txt"])

    versions = store.list_versions(limit=10)
    assert len(versions) >= 2
    assert versions[0]["id"] == vid2

    detail = store.get(vid1)
    assert detail["exists"] and detail["manifest"]["message"] == "initial"

    sample.write_text("broken\n", encoding="utf-8")
    result = store.rollback(vid1)
    assert result["restored"] == ["notes/hello.txt"]
    assert sample.read_text(encoding="utf-8") == "v1\n"

    prefixed = store.list_versions(path_prefix="notes/", limit=5)
    assert all(any(p.startswith("notes/") for p in e.get("paths", [])) for e in prefixed)

    assert store.delete(vid2) is True
    assert store.get(vid2)["exists"] is False

    vid3 = store.snapshot(["notes"], message="dir snap")
    pruned = store.prune(keep=1)
    assert vid3 in pruned or vid1 in pruned

    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    shutil.rmtree(VERSIONS_DIR, ignore_errors=True)
    print("versioning self-check ok")
