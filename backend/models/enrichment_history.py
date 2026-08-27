"""One restorable enrichment write."""

from __future__ import annotations

import msgspec


class EnrichmentHistoryEntry(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    file_path: str
    # What was applied - lyrics, replaygain, genres, metadata - so the history
    # reads as something a person recognises rather than a list of tag names.
    kinds: tuple[str, ...]
    changed_fields: tuple[str, ...]
    # A serialized SemanticTagSnapshot of the file BEFORE the write. Kept as text
    # rather than a decoded struct because restoring hands it straight back to
    # the audio engine, and because its shape is the engine's to change.
    snapshot_json: str
    created_at: float
    restored_at: float | None = None
