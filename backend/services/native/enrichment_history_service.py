"""Read and undo enrichment writes."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import msgspec

from infrastructure.audio.metadata_engine import AudioMetadataEngine
from infrastructure.persistence.enrichment_history_store import EnrichmentHistoryStore
from models.audio_metadata import SemanticTagSnapshot
from models.enrichment_history import EnrichmentHistoryEntry

logger = logging.getLogger(__name__)

# An undo window rather than an archive: the snapshots are large, and a change
# nobody has objected to within a month is a change they wanted.
HISTORY_RETENTION_SECONDS = 30 * 24 * 60 * 60


class EnrichmentHistoryService:
    def __init__(
        self, store: EnrichmentHistoryStore, audio: AudioMetadataEngine
    ) -> None:
        self._store = store
        self._audio = audio

    async def list_recent(self, *, limit: int = 100) -> list[EnrichmentHistoryEntry]:
        return await self._store.list_recent(limit=limit)

    async def restore(self, entry_id: str) -> EnrichmentHistoryEntry:
        entry = await self._store.get(entry_id)
        if entry is None:
            raise LookupError("That enrichment history entry no longer exists.")
        if entry.restored_at is not None:
            raise ValueError("That change has already been restored.")
        path = Path(entry.file_path)
        if not await asyncio.to_thread(path.is_file):
            raise FileNotFoundError(
                "The file has moved or been removed since it was enriched."
            )
        snapshot = msgspec.json.decode(
            entry.snapshot_json.encode(), type=SemanticTagSnapshot
        )
        await asyncio.to_thread(self._audio.restore, path, snapshot)
        # Claimed after the write so a failure leaves the entry restorable; the
        # store only marks one that has not been claimed already, so a double
        # click cannot roll a file back past the state somebody wanted.
        await self._store.mark_restored(entry_id)
        logger.info("enrichment_history.restored", extra={"path": entry.file_path})
        return entry

    async def prune(self) -> int:
        return await self._store.prune(older_than_seconds=HISTORY_RETENTION_SECONDS)
