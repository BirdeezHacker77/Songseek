"""Downloads whose MusicBrainz match was close but not certain.

Identification writes tags only when the evidence is strong. Everything between
"probably this release" and "definitely this release" lands here instead, so the
import completes and a person answers the question later - or never, which is
also a fine outcome for a download that plays perfectly well as it is.

Rows are kept until they are answered, unlike the enrichment history: a pending
question that quietly expired would be worse than no question at all. Answered
rows are pruned by age.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import msgspec

from infrastructure.persistence._database import PersistenceBase
from models.import_review import ImportReviewEntry, ImportReviewPage, ImportReviewStatus


class ImportReviewStore(PersistenceBase):
    def __init__(self, db_path: Path, write_lock: threading.Lock) -> None:
        super().__init__(db_path, write_lock)

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS import_identification_review (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    score REAL NOT NULL DEFAULT 0,
                    reason_code TEXT NOT NULL DEFAULT '',
                    local_album_title TEXT NOT NULL DEFAULT '',
                    local_album_artist_name TEXT NOT NULL DEFAULT '',
                    album_title TEXT NOT NULL DEFAULT '',
                    album_artist_name TEXT NOT NULL DEFAULT '',
                    release_mbid TEXT,
                    release_group_mbid TEXT,
                    paths_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    resolved_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_import_review_status
                    ON import_identification_review(status, created_at);
                """
            )
            conn.commit()
        finally:
            conn.close()

    async def record(self, entry: ImportReviewEntry) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO import_identification_review "
                "(id,status,score,reason_code,local_album_title,"
                " local_album_artist_name,album_title,album_artist_name,"
                " release_mbid,release_group_mbid,paths_json,created_at,resolved_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry.id,
                    entry.status,
                    entry.score,
                    entry.reason_code,
                    entry.local_album_title,
                    entry.local_album_artist_name,
                    entry.album_title,
                    entry.album_artist_name,
                    entry.release_mbid,
                    entry.release_group_mbid,
                    msgspec.json.encode(entry.paths).decode(),
                    entry.created_at,
                    entry.resolved_at,
                ),
            )

        await self._write(operation)

    async def list_entries(
        self,
        *,
        status: ImportReviewStatus | None = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> ImportReviewPage:
        capped = max(1, min(200, limit))
        start = max(0, offset)

        def operation(conn: sqlite3.Connection) -> ImportReviewPage:
            where, parameters = ("", ())
            if status is not None:
                where, parameters = ("WHERE status=?", (status,))
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM import_identification_review {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = conn.execute(
                "SELECT * FROM import_identification_review "
                f"{where} ORDER BY score DESC, created_at DESC, id LIMIT ? OFFSET ?",
                (*parameters, capped, start),
            ).fetchall()
            return ImportReviewPage(
                items=tuple(_row_to_entry(row) for row in rows), total=total
            )

        return await self._read(operation)

    async def get(self, entry_id: str) -> ImportReviewEntry | None:
        def operation(conn: sqlite3.Connection) -> ImportReviewEntry | None:
            row = conn.execute(
                "SELECT * FROM import_identification_review WHERE id=?", (entry_id,)
            ).fetchone()
            return _row_to_entry(row) if row is not None else None

        return await self._read(operation)

    async def resolve(
        self,
        entry_id: str,
        status: ImportReviewStatus,
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now

        def operation(conn: sqlite3.Connection) -> bool:
            # Only a pending row, so answering twice cannot re-apply a match that
            # was already accepted or reopen one that was dismissed.
            return (
                conn.execute(
                    "UPDATE import_identification_review "
                    "SET status=?, resolved_at=? WHERE id=? AND status='pending'",
                    (status, timestamp, entry_id),
                ).rowcount
                > 0
            )

        return await self._write(operation)

    async def prune_resolved(
        self, *, older_than_seconds: float, now: float | None = None
    ) -> int:
        timestamp = time.time() if now is None else now

        def operation(conn: sqlite3.Connection) -> int:
            return conn.execute(
                "DELETE FROM import_identification_review "
                "WHERE status!='pending' AND resolved_at IS NOT NULL AND resolved_at<=?",
                (timestamp - older_than_seconds,),
            ).rowcount

        return await self._write(operation)


def _row_to_entry(row: sqlite3.Row) -> ImportReviewEntry:
    return ImportReviewEntry(
        id=str(row["id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        score=float(row["score"]),
        reason_code=str(row["reason_code"]),
        local_album_title=str(row["local_album_title"]),
        local_album_artist_name=str(row["local_album_artist_name"]),
        album_title=str(row["album_title"]),
        album_artist_name=str(row["album_artist_name"]),
        release_mbid=row["release_mbid"],
        release_group_mbid=row["release_group_mbid"],
        paths=tuple(msgspec.json.decode(row["paths_json"], type=list[str])),
        created_at=float(row["created_at"]),
        resolved_at=row["resolved_at"],
    )
