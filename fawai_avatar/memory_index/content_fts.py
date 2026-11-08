"""Filesystem content FTS5 over configured roots."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fawai_avatar.memory_index.common import (
    build_match_query,
    is_text_path,
    iter_files,
    parse_roots,
    prepare_fts_text,
    read_text_capped,
    root_too_broad,
)

ProgressFn = Callable[..., None]


class ContentFtsIndex:
    """
    ``content.sqlite``::

        docs(path PK, mtime, size)
        content_fts(path, body, tokenize unicode61)
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
            CREATE TABLE IF NOT EXISTS docs (
              path TEXT PRIMARY KEY,
              mtime REAL NOT NULL DEFAULT 0,
              size INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
              path,
              body,
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
        self._conn.execute("DELETE FROM docs")
        self._conn.execute("DELETE FROM content_fts")
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT count(*) FROM docs").fetchone()
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
            return {"ok": False, "error": "未配置 FTS5 索引目录", "indexed": 0}
        if all(root_too_broad(r) for r in roots):
            return {"ok": False, "error": root_too_broad(roots[0]) or "根过宽", "indexed": 0}

        if on_progress:
            on_progress(
                phase="scanning",
                message="清空旧正文索引…",
                indexed=0,
                roots=root_list,
            )
        self.clear()
        n = 0
        skipped = 0
        t0 = time.time()
        if on_progress:
            on_progress(
                phase="indexing",
                message="扫描并写入正文 FTS…",
                indexed=0,
                roots=root_list,
            )
        for path in iter_files(roots, max_files=max_files):
            if not is_text_path(path):
                skipped += 1
                continue
            text = read_text_capped(path)
            if text is None:
                skipped += 1
                continue
            try:
                st = path.stat()
            except OSError:
                skipped += 1
                continue
            abs_p = str(path)
            body = prepare_fts_text(path.name, text[:500_000])
            self._conn.execute(
                "INSERT OR REPLACE INTO docs(path, mtime, size) VALUES (?,?,?)",
                (abs_p, float(st.st_mtime), int(st.st_size)),
            )
            self._conn.execute(
                "INSERT INTO content_fts(path, body) VALUES (?, ?)",
                (abs_p, body),
            )
            n += 1
            if n % 200 == 0:
                self._conn.commit()
                if on_progress:
                    on_progress(
                        phase="indexing",
                        message=f"已索引正文 {n}（跳过 {skipped}）…",
                        indexed=n,
                        roots=root_list,
                    )
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('built_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
        )
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES('roots', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (roots_raw,),
        )
        self._conn.commit()
        if on_progress:
            on_progress(
                phase="indexing",
                message=f"正文 FTS 完成 {n}（跳过 {skipped}）",
                indexed=n,
                roots=root_list,
            )
        return {
            "ok": True,
            "indexed": n,
            "skipped": skipped,
            "roots": root_list,
            "seconds": round(time.time() - t0, 3),
        }

    def remove_path(self, path: str | Path) -> bool:
        abs_p = str(Path(path))
        cur = self._conn.execute("DELETE FROM docs WHERE path = ?", (abs_p,))
        self._conn.execute("DELETE FROM content_fts WHERE path = ?", (abs_p,))
        self._conn.commit()
        return int(cur.rowcount or 0) > 0

    def upsert_path(self, path: str | Path) -> bool:
        """单文件增量写入正文 FTS；非文本或不存在则删除旧行。"""
        p = Path(path)
        abs_p = str(p)
        if not p.is_file() or not is_text_path(p):
            return self.remove_path(abs_p)
        text = read_text_capped(p)
        if text is None:
            return self.remove_path(abs_p)
        try:
            st = p.stat()
        except OSError:
            return self.remove_path(abs_p)
        body = prepare_fts_text(p.name, text[:500_000])
        self._conn.execute("DELETE FROM content_fts WHERE path = ?", (abs_p,))
        self._conn.execute(
            "INSERT OR REPLACE INTO docs(path, mtime, size) VALUES (?,?,?)",
            (abs_p, float(st.st_mtime), int(st.st_size)),
        )
        self._conn.execute(
            "INSERT INTO content_fts(path, body) VALUES (?, ?)",
            (abs_p, body),
        )
        self._conn.commit()
        return True

    def search(self, query: str, *, limit: int = 40) -> list[dict[str, Any]]:
        match = build_match_query(query)
        limit = max(1, min(int(limit or 40), 100))
        if not match:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT path, snippet(content_fts, 1, '「', '」', '…', 12) AS snip,
                       bm25(content_fts) AS rank
                FROM content_fts
                WHERE content_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[dict[str, Any]] = []
        for path, snip, rank in rows:
            out.append(
                {
                    "path": path,
                    "snippet": snip or "",
                    "score": 1.0 / (1.0 + abs(float(rank or 0))),
                    "source": "content_fts",
                }
            )
        return out


def open_content_fts(db_path: str | Path) -> ContentFtsIndex:
    return ContentFtsIndex(db_path)
