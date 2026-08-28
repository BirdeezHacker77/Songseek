"""Answering the identification questions a download left behind.

A flagged import is a question, not a pending change: the files are already in
the library and play fine. Accepting one re-fetches the release and writes its
tags; dismissing one records that the download is fine as it is, so the same
album stops being asked about.

Accepting deliberately re-fetches rather than replaying tags captured at import
time. The answer may come weeks later, MusicBrainz may have corrected the
release since, and the correction is the thing worth writing.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from api.v1.schemas.settings import DownloadEnrichmentSettings
from models.import_review import ImportReviewEntry, ImportReviewPage, ImportReviewStatus
from services.native.post_import_identification_service import (
    IdentificationProposal,
    PostImportIdentificationService,
)

logger = logging.getLogger(__name__)

# How long an answered row is kept. Long enough to see what was decided, short
# enough that the table does not become an archive of every download ever made.
RESOLVED_RETENTION_SECONDS = 30 * 24 * 60 * 60


class ImportReviewError(Exception):
    """Raised when a review cannot be answered, with a reason worth showing."""


class ImportReviewService:
    def __init__(
        self,
        store,  # noqa: ANN001 - ImportReviewStore
        identification: PostImportIdentificationService,
        enrichment,  # noqa: ANN001 - PostImportEnrichmentService
        settings_getter: Callable[[], DownloadEnrichmentSettings],
    ) -> None:
        self._store = store
        self._identification = identification
        self._enrichment = enrichment
        self._settings_getter = settings_getter

    async def record(self, proposal: IdentificationProposal) -> None:
        await self._store.record(
            ImportReviewEntry(
                id=str(uuid.uuid4()),
                status="pending",
                score=proposal.score,
                reason_code=proposal.reason_code,
                local_album_title=proposal.local_album_title,
                local_album_artist_name=proposal.local_album_artist_name,
                album_title=proposal.album_title,
                album_artist_name=proposal.album_artist_name,
                release_mbid=proposal.release_mbid,
                release_group_mbid=proposal.release_group_mbid,
                paths=proposal.paths,
                created_at=time.time(),
            )
        )

    async def list_entries(
        self,
        *,
        status: ImportReviewStatus | None = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> ImportReviewPage:
        return await self._store.list_entries(
            status=status, limit=limit, offset=offset
        )

    async def dismiss(self, entry_id: str) -> bool:
        return await self._store.resolve(entry_id, "dismissed")

    async def accept(self, entry_id: str) -> int:
        """Write the matched release's tags. Returns how many files changed."""

        entry = await self._store.get(entry_id)
        if entry is None:
            raise ImportReviewError("That review no longer exists.")
        if entry.status != "pending":
            raise ImportReviewError("That review has already been answered.")

        paths = [Path(value) for value in entry.paths]
        missing = [str(value) for value in paths if not value.exists()]
        if missing:
            # Loudly, rather than writing to whatever now sits at the old path.
            # Between the import and the answer the album may have been renamed,
            # moved or deleted, and a partial write across a moved album would
            # leave half of it tagged as one release and half as another.
            raise ImportReviewError(
                "Some of those files have moved or been removed since the import, "
                "so the match cannot be applied safely."
            )

        proposal = await self._identification.propose_release(
            paths,
            self._settings_getter().tagging,
            release_mbid=entry.release_mbid,
            release_group_mbid=entry.release_group_mbid,
        )
        if proposal.status != "applied" or not proposal.fields_by_path:
            raise ImportReviewError(
                "That release could not be fetched from MusicBrainz just now."
            )

        written = 0
        for path in paths:
            fields = proposal.fields_by_path.get(str(path))
            if not fields:
                continue
            try:
                if await self._enrichment.apply_tag_fields(path, fields):
                    written += 1
            except Exception:  # noqa: BLE001 - one bad file must not strand the rest
                logger.warning(
                    "import_review.write_failed",
                    extra={"path": str(path)},
                    exc_info=True,
                )
        # Marked answered even when nothing changed: the files already carried
        # the release's tags, which is still an answer to the question asked.
        await self._store.resolve(entry_id, "accepted")
        return written

    async def prune(self) -> int:
        return await self._store.prune_resolved(
            older_than_seconds=RESOLVED_RETENTION_SECONDS
        )
