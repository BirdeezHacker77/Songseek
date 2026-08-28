import threading
from pathlib import Path

import pytest

from infrastructure.persistence.import_review_store import ImportReviewStore
from models.import_review import ImportReviewEntry


@pytest.fixture
def store(tmp_path: Path) -> ImportReviewStore:
    return ImportReviewStore(
        db_path=tmp_path / "library.db", write_lock=threading.Lock()
    )


def _entry(entry_id: str, *, score: float = 0.6, created_at: float = 100.0):  # noqa: ANN202
    return ImportReviewEntry(
        id=entry_id,
        status="pending",
        score=score,
        reason_code="CONFLICTING_TRACK_EVIDENCE",
        local_album_title="the colour and the shape",
        local_album_artist_name="foo fighters",
        album_title="The Colour and the Shape",
        album_artist_name="Foo Fighters",
        release_mbid="rel-1",
        release_group_mbid="rg-1",
        paths=("/music/A/01.flac", "/music/A/02.flac"),
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_a_recorded_review_reads_back_whole(store: ImportReviewStore):
    await store.record(_entry("one"))

    entry = await store.get("one")

    assert entry is not None
    assert entry.status == "pending"
    assert entry.paths == ("/music/A/01.flac", "/music/A/02.flac")
    assert entry.album_title == "The Colour and the Shape"
    assert entry.local_album_title == "the colour and the shape"
    assert entry.resolved_at is None


@pytest.mark.asyncio
async def test_the_closest_calls_are_listed_first(store: ImportReviewStore):
    """A 0.68 is worth a person's attention in a way a 0.51 is not."""
    await store.record(_entry("far", score=0.51))
    await store.record(_entry("close", score=0.68))

    page = await store.list_entries()

    assert [entry.id for entry in page.items] == ["close", "far"]
    assert page.total == 2


@pytest.mark.asyncio
async def test_only_pending_reviews_are_listed_by_default(store: ImportReviewStore):
    await store.record(_entry("one"))
    await store.record(_entry("two"))
    await store.resolve("two", "dismissed")

    pending = await store.list_entries()
    everything = await store.list_entries(status=None)

    assert [entry.id for entry in pending.items] == ["one"]
    assert everything.total == 2


@pytest.mark.asyncio
async def test_a_review_can_only_be_answered_once(store: ImportReviewStore):
    await store.record(_entry("one"))

    first = await store.resolve("one", "accepted")
    second = await store.resolve("one", "dismissed")

    assert first is True
    # Otherwise a double click re-applies a match, or reopens one somebody
    # already turned down.
    assert second is False
    entry = await store.get("one")
    assert entry is not None and entry.status == "accepted"


@pytest.mark.asyncio
async def test_answered_reviews_are_pruned_and_pending_ones_are_not(
    store: ImportReviewStore,
):
    await store.record(_entry("answered"))
    await store.record(_entry("waiting"))
    await store.resolve("answered", "dismissed", now=100.0)

    removed = await store.prune_resolved(older_than_seconds=50.0, now=1_000.0)

    assert removed == 1
    # A question that quietly expired would be worse than never asking it.
    assert await store.get("waiting") is not None


@pytest.mark.asyncio
async def test_pruning_spares_a_recently_answered_review(store: ImportReviewStore):
    await store.record(_entry("answered"))
    await store.resolve("answered", "accepted", now=1_000.0)

    removed = await store.prune_resolved(older_than_seconds=500.0, now=1_100.0)

    assert removed == 0
