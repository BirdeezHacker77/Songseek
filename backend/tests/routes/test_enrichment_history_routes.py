"""The enrichment-history endpoints, against the real store.

The list response is deliberately not the stored entry: that carries
`snapshot_json`, a complete tag snapshot per file, and a hundred of those is
megabytes the browser has no use for. A test that asserted on a hand-built
struct would not notice it leaking back in.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from api.v1.schemas.enrichment_history import EnrichmentHistoryItem
from infrastructure.persistence.enrichment_history_store import EnrichmentHistoryStore
from models.enrichment_history import EnrichmentHistoryEntry

BIG_SNAPSHOT = '{"snapshot_version": 1, "values": "' + ("x" * 4096) + '"}'


@pytest.fixture
def store(tmp_path: Path) -> EnrichmentHistoryStore:
    return EnrichmentHistoryStore(
        db_path=tmp_path / "library.db", write_lock=threading.Lock()
    )


def _entry(entry_id: str = "one") -> EnrichmentHistoryEntry:
    return EnrichmentHistoryEntry(
        id=entry_id,
        file_path="/music/Foo Fighters/The Colour and the Shape/01 Doll.flac",
        kinds=("tags", "lyrics"),
        changed_fields=("album", "title", "lyrics_plain"),
        snapshot_json=BIG_SNAPSHOT,
        created_at=100.0,
    )


@pytest.mark.asyncio
async def test_a_history_row_carries_what_the_ui_shows(
    store: EnrichmentHistoryStore,
) -> None:
    await store.record(_entry())

    item = EnrichmentHistoryItem.from_entry((await store.list_recent())[0])

    assert item.file_path.endswith("01 Doll.flac")
    assert item.kinds == ("tags", "lyrics")
    assert item.changed_fields == ("album", "title", "lyrics_plain")
    assert item.restored_at is None


@pytest.mark.asyncio
async def test_the_snapshot_never_reaches_the_response(
    store: EnrichmentHistoryStore,
) -> None:
    """Only restore reads it, server-side."""
    import msgspec

    await store.record(_entry())

    item = EnrichmentHistoryItem.from_entry((await store.list_recent())[0])
    encoded = msgspec.json.encode(item).decode()

    assert "snapshot" not in encoded
    assert "xxxx" not in encoded


@pytest.mark.asyncio
async def test_a_restored_row_reports_when(store: EnrichmentHistoryStore) -> None:
    await store.record(_entry())
    await store.mark_restored("one", now=500.0)

    item = EnrichmentHistoryItem.from_entry((await store.list_recent())[0])

    assert item.restored_at == 500.0
