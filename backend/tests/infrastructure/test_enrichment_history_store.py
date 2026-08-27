import threading
from pathlib import Path

import pytest

from infrastructure.persistence.enrichment_history_store import EnrichmentHistoryStore
from models.enrichment_history import EnrichmentHistoryEntry


@pytest.fixture
def store(tmp_path: Path) -> EnrichmentHistoryStore:
    return EnrichmentHistoryStore(
        db_path=tmp_path / "library.db", write_lock=threading.Lock()
    )


def _entry(entry_id: str, *, created_at: float = 100.0) -> EnrichmentHistoryEntry:
    return EnrichmentHistoryEntry(
        id=entry_id,
        file_path=f"/data/Music/Artist/Album/{entry_id}.flac",
        kinds=("lyrics", "replaygain"),
        changed_fields=("lyrics_plain", "replaygain_track_gain"),
        snapshot_json='{"snapshot_version": 1}',
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_a_recorded_write_can_be_read_back(store: EnrichmentHistoryStore):
    await store.record(_entry("one"))

    entry = await store.get("one")

    assert entry is not None
    assert entry.kinds == ("lyrics", "replaygain")
    assert entry.changed_fields == ("lyrics_plain", "replaygain_track_gain")
    assert entry.snapshot_json == '{"snapshot_version": 1}'
    assert entry.restored_at is None


@pytest.mark.asyncio
async def test_history_reads_newest_first(store: EnrichmentHistoryStore):
    await store.record(_entry("older", created_at=100.0))
    await store.record(_entry("newer", created_at=200.0))

    assert [entry.id for entry in await store.list_recent()] == ["newer", "older"]


@pytest.mark.asyncio
async def test_a_change_can_only_be_restored_once(store: EnrichmentHistoryStore):
    """Otherwise a second click rolls the file back past the state somebody
    deliberately restored it to."""
    await store.record(_entry("one"))

    assert await store.mark_restored("one", now=500.0) is True
    assert await store.mark_restored("one", now=600.0) is False

    entry = await store.get("one")
    assert entry is not None and entry.restored_at == 500.0


@pytest.mark.asyncio
async def test_pruning_keeps_the_undo_window_and_drops_the_rest(
    store: EnrichmentHistoryStore,
):
    day = 24 * 60 * 60
    await store.record(_entry("ancient", created_at=1_000.0))
    await store.record(_entry("recent", created_at=1_000.0 + 29 * day))

    removed = await store.prune(older_than_seconds=30 * day, now=1_000.0 + 30 * day)

    assert removed == 1
    assert [entry.id for entry in await store.list_recent()] == ["recent"]


@pytest.mark.asyncio
async def test_a_missing_entry_reads_as_absent(store: EnrichmentHistoryStore):
    assert await store.get("never-existed") is None
    assert await store.mark_restored("never-existed") is False
