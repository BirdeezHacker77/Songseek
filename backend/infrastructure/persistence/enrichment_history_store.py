"""Restorable history for enrichment writes.

Enrichment used to be additive only - lyrics and loudness fill blanks, and a
wrong value costs nothing. Rewriting a track's metadata replaces what the file
arrived with, so every write records the tags as they were first. The stored
snapshot is exactly what the audio engine's `restore` consumes, so putting a
file back is replaying it rather than reconstructing anything.

Entries are pruned by age: this is an undo window, not an archive, and the
snapshots are large enough that keeping them forever would dwarf the database.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import msgspec

from infrastructure.persistence._database import PersistenceBase
from models.enrichment_history import EnrichmentHistoryEntry


class EnrichmentHistoryStore(PersistenceBase):
    def __init__(self, db_path: Path, write_lock: threading.Lock) -> None:
        super().__init__(db_path, write_lock)

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS enrichment_write_history (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    kinds_json TEXT NOT NULL DEFAULT '[]',
                    changed_fields_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL,
                    restored_at REAL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_enrichment_history_created
                    ON enrichment_write_history(created_at);
                CREATE INDEX IF NOT EXISTS idx_enrichment_history_path
                    ON enrichment_write_history(file_path);
                """
            )
            conn.commit()
        finally:
            conn.close()

    async def record(self, entry: EnrichmentHistoryEntry) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO enrichment_write_history "
                "(id,file_path,kinds_json,changed_fields_json,snapshot_json,"
                " restored_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    entry.id,
                    entry.file_path,
                    msgspec.json.encode(entry.kinds).decode(),
                    msgspec.json.encode(entry.changed_fields).decode(),
                    entry.snapshot_json,
                    entry.restored_at,
                    entry.created_at,
                ),
            )

        await self._write(operation)

    async def list_recent(self, *, limit: int = 100) -> list[EnrichmentHistoryEntry]:
        def operation(conn: sqlite3.Connection) -> list[EnrichmentHistoryEntry]:
            rows = conn.execute(
                "SELECT * FROM enrichment_write_history "
                "ORDER BY created_at DESC, id LIMIT ?",
                (max(1, min(500, limit)),),
            ).fetchall()
            return [_row_to_entry(row) for row in rows]

        return await self._read(operation)

    async def get(self, entry_id: str) -> EnrichmentHistoryEntry | None:
        def operation(conn: sqlite3.Connection) -> EnrichmentHistoryEntry | None:
            row = conn.execute(
                "SELECT * FROM enrichment_write_history WHERE id=?", (entry_id,)
            ).fetchone()
            return _row_to_entry(row) if row is not None else None

        return await self._read(operation)

    async def mark_restored(self, entry_id: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now

        def operation(conn: sqlite3.Connection) -> bool:
            # Only an entry that has not been restored already, so a double click
            # cannot roll a file back past the state somebody wanted.
            return (
                conn.execute(
                    "UPDATE enrichment_write_history SET restored_at=? "
                    "WHERE id=? AND restored_at IS NULL",
                    (timestamp, entry_id),
                ).rowcount
                > 0
            )

        return await self._write(operation)

    async def prune(
        self, *, older_than_seconds: float, now: float | None = None
    ) -> int:
        timestamp = time.time() if now is None else now

        def operation(conn: sqlite3.Connection) -> int:
            return conn.execute(
                "DELETE FROM enrichment_write_history WHERE created_at<=?",
                (timestamp - older_than_seconds,),
            ).rowcount

        return await self._write(operation)


def _row_to_entry(row: sqlite3.Row) -> EnrichmentHistoryEntry:
    return EnrichmentHistoryEntry(
        id=str(row["id"]),
        file_path=str(row["file_path"]),
        kinds=tuple(msgspec.json.decode(row["kinds_json"], type=list[str])),
        changed_fields=tuple(
            msgspec.json.decode(row["changed_fields_json"], type=list[str])
        ),
        snapshot_json=str(row["snapshot_json"]),
        restored_at=row["restored_at"],
        created_at=float(row["created_at"]),
    )
