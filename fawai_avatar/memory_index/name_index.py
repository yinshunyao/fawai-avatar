"""Everything-style filename / path index (SQLite + FTS5 trigram/unicode)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fawai_avatar.memory_index.common import (
    build_name_match_query,
    iter_files,
    parse_roots,
    prepare_fts_text,
    root_too_broad,
    split_name_query_terms,
)

ProgressFn = Callable[..., None]


class NameIndex:
    """
    ``names.sqlite``::

        files(path PK, name, parent, mtime, size)
        names_fts(path, name, tokenize unicode61)
    """

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
              path TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              parent TEXT NOT NULL,
              mtime REAL NOT NULL DEFAULT 0,
              size INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_name ON files(name COLLATE NOCASE)"
        )
        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS names_fts USING fts5(
              path,
              name,
              tokenize='unicode61'
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def clear(self) -> None:
        self._conn.execute("DELETE FROM files")
        self._conn.execute("DELETE FROM names_fts")
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT count(*) FROM files").fetchone()
        return int(row[0] if row else 0)

    def reindex_roots(
        self,
        roots_raw: str,
        *,
        max_files: int = 200_000,
        on_progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        roots = parse_roots(roots_raw)
        root_list = [str(r) for r in roots]
        if not roots:
            return {"ok": False, "error": "未配置文件名索引目录", "indexed": 0}
        blocked = [root_too_broad(r) for r in roots]
        blocked = [b for b in blocked if b]
        if blocked and all(root_too_broad(r) for r in roots):
            return {"ok": False, "error": blocked[0], "indexed": 0}

        if on_progress:
            on_progress(
                phase="scanning",
                message="清空旧文件名索引…",
                indexed=0,
                roots=root_list,
            )
        self.clear()
        n = 0
        skipped = 0
        t0 = time.time()
        batch: list[tuple[str, str, str, float, int]] = []
        if on_progress:
            on_progress(
                phase="indexing",
                message="扫描并写入文件名…",
                indexed=0,
                roots=root_list,
            )
        for path in iter_files(roots, max_files=max_files):
            try:
                st = path.stat()
                name = path.name
                parent = str(path.parent)
                batch.append(
                    (
                        str(path),
                        name,
                        parent,
                        float(st.st_mtime),
                        int(st.st_size),
                    )
                )
            except OSError:
                skipped += 1
                continue
            if len(batch) >= 400:
                self._flush_batch(batch)
                n += len(batch)
                batch.clear()
                if on_progress and n % 2000 < 400:
                    on_progress(
                        phase="indexing",
                        message=f"已索引文件名 {n}…",
                        indexed=n,
                        roots=root_list,
                    )
        if batch:
            self._flush_batch(batch)
            n += len(batch)
        elapsed = time.time() - t0
        if on_progress:
            on_progress(
                phase="done",
                message=f"文件名索引完成 {n}",
                indexed=n,
                roots=root_list,
                elapsed_sec=round(elapsed, 2),
            )
        return {
            "ok": True,
            "indexed": n,
            "roots": root_list,
            "skipped": skipped,
            "elapsed_sec": round(elapsed, 2),
        }

    def _flush_batch(self, batch: list[tuple[str, str, str, float, int]]) -> None:
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO files(path, name, parent, mtime, size)
            VALUES (?, ?, ?, ?, ?)
            """,
            batch,
        )
        self._conn.executemany(
            "INSERT INTO names_fts(path, name) VALUES (?, ?)",
            [(p, prepare_fts_text(name, p)) for p, name, *_ in batch],
        )
        self._conn.commit()

    def remove_path(self, path: str | Path) -> bool:
        abs_p = str(Path(path))
        cur = self._conn.execute("DELETE FROM files WHERE path = ?", (abs_p,))
        self._conn.execute("DELETE FROM names_fts WHERE path = ?", (abs_p,))
        self._conn.commit()
        return int(cur.rowcount or 0) > 0

    def upsert_path(self, path: str | Path) -> bool:
        """单文件增量写入；失败或不存在则尝试删除旧行。"""
        p = Path(path)
        abs_p = str(p)
        if not p.is_file():
            return self.remove_path(abs_p)
        try:
            st = p.stat()
        except OSError:
            return self.remove_path(abs_p)
        name = p.name
        parent = str(p.parent)
        self._conn.execute("DELETE FROM names_fts WHERE path = ?", (abs_p,))
        self._conn.execute(
            """
            INSERT OR REPLACE INTO files(path, name, parent, mtime, size)
            VALUES (?, ?, ?, ?, ?)
            """,
            (abs_p, name, parent, float(st.st_mtime), int(st.st_size)),
        )
        self._conn.execute(
            "INSERT INTO names_fts(path, name) VALUES (?, ?)",
            (abs_p, prepare_fts_text(name, abs_p)),
        )
        self._conn.commit()
        return True

    def _like_rows(self, needle: str, *, limit: int) -> list[tuple[Any, ...]]:
        like = f"%{needle}%"
        return self._conn.execute(
            """
            SELECT path, name, parent, mtime, size FROM files
            WHERE name LIKE ? COLLATE NOCASE OR path LIKE ? COLLATE NOCASE
            ORDER BY length(name) ASC, name ASC
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()

    def search(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        q = (query or "").strip()
        limit = max(1, min(int(limit or 50), 200))
        if not q:
            return []

        # path → (score, rowdict)
        scored: dict[str, dict[str, Any]] = {}

        def _add(
            path: str,
            name: str,
            parent: str,
            mtime: Any,
            size: Any,
            *,
            source: str,
            bump: float,
        ) -> None:
            cur = scored.get(path)
            if cur is None:
                scored[path] = {
                    "path": path,
                    "name": name,
                    "parent": parent,
                    "mtime": mtime,
                    "size": size,
                    "source": source,
                    "score": float(bump),
                    "_hits": 1,
                }
            else:
                cur["score"] = float(cur.get("score") or 0) + float(bump)
                cur["_hits"] = int(cur.get("_hits") or 0) + 1
                # 保留更高质 source 标签
                if source == "name_like" and cur.get("source") != "name_like":
                    cur["source"] = "name_like"

        # 1) 整串 LIKE（短查询 / 精确文件名片段）
        for path, name, parent, mtime, size in self._like_rows(q, limit=limit):
            bump = 2.0 if q.lower() in str(name).lower() else 1.2
            _add(path, name, parent, mtime, size, source="name_like", bump=bump)

        # 2) 分词 LIKE：自然语言多词不应要求整串出现在文件名里
        terms = split_name_query_terms(q)
        if len(terms) >= 2 or (len(terms) == 1 and terms[0] != q.lower()):
            per = max(limit, min(80, limit * 3))
            for tok in terms:
                for path, name, parent, mtime, size in self._like_rows(tok, limit=per):
                    in_name = tok.lower() in str(name).lower()
                    bump = 1.6 if in_name else 0.9
                    # 数字/标识符略抬
                    if any(ch.isdigit() for ch in tok) or tok.isascii():
                        bump += 0.3
                    _add(
                        path,
                        name,
                        parent,
                        mtime,
                        size,
                        source="name_like_term",
                        bump=bump,
                    )

        # 3) FTS：词间 OR（词内中文仍 AND）
        match = build_name_match_query(q)
        if match:
            try:
                fts_rows = self._conn.execute(
                    """
                    SELECT path, bm25(names_fts) AS rank FROM names_fts
                    WHERE names_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (match, max(limit, min(100, limit * 4))),
                ).fetchall()
            except sqlite3.OperationalError:
                fts_rows = []
            for path, rank in fts_rows:
                row = self._conn.execute(
                    "SELECT path, name, parent, mtime, size FROM files WHERE path = ?",
                    (path,),
                ).fetchone()
                if not row:
                    continue
                bump = 1.0 / (1.0 + abs(float(rank or 0)))
                _add(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    source="name_fts",
                    bump=bump,
                )

        if not scored:
            return []

        # 多词命中数加权：同时沾上 4090+配置 的路径优先于只沾 ssh 的噪声
        term_n = max(1, len(terms))
        items = list(scored.values())
        for it in items:
            hits = int(it.pop("_hits", 1) or 1)
            it["score"] = float(it.get("score") or 0) + 0.35 * min(hits, term_n)

        items.sort(
            key=lambda h: (
                -float(h.get("score") or 0),
                len(str(h.get("name") or "")),
                str(h.get("name") or ""),
            )
        )
        return items[:limit]


def open_name_index(db_path: str | Path) -> NameIndex:
    return NameIndex(db_path)


__all__ = ["NameIndex", "open_name_index"]
