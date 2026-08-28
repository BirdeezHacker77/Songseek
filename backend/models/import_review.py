"""One download whose MusicBrainz match was too uncertain to apply."""

from __future__ import annotations

from typing import Literal

import msgspec

ImportReviewStatus = Literal["pending", "accepted", "dismissed"]


class ImportReviewEntry(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    status: ImportReviewStatus
    # The evidence engine's score for the release below. Kept so the list can be
    # ordered by how close the call was - a 0.68 is worth a person's attention in
    # a way a 0.51 is not.
    score: float
    reason_code: str
    # What the files say about themselves right now, which is what is actually in
    # the library until somebody accepts the match.
    local_album_title: str
    local_album_artist_name: str
    # What MusicBrainz thinks it is.
    album_title: str
    album_artist_name: str
    release_mbid: str | None
    release_group_mbid: str | None
    # The files this covers. Stored rather than re-derived because the review may
    # be answered long after the import, and the album may have been renamed or
    # moved since - in which case accepting must fail loudly rather than write to
    # whatever now sits at the old path.
    paths: tuple[str, ...]
    created_at: float
    resolved_at: float | None = None


class ImportReviewPage(msgspec.Struct, frozen=True, kw_only=True):
    items: tuple[ImportReviewEntry, ...] = ()
    total: int = 0
