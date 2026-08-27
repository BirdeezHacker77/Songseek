import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from infrastructure.persistence.enrichment_history_store import EnrichmentHistoryStore
from models.enrichment_history import EnrichmentHistoryEntry
from services.native.enrichment_history_service import EnrichmentHistoryService

SNAPSHOT = '{"snapshot_version": 1}'


def _service(tmp_path: Path, *, restores: list | None = None):  # noqa: ANN202
    store = EnrichmentHistoryStore(
        db_path=tmp_path / "library.db", write_lock=threading.Lock()
    )

    def restore(path, snapshot):  # noqa: ANN001, ANN202
        if restores is not None:
            restores.append((path, snapshot))

    return EnrichmentHistoryService(store, SimpleNamespace(restore=restore)), store


async def _record(store, path: Path, entry_id: str = "one") -> None:  # noqa: ANN001
    await store.record(
        EnrichmentHistoryEntry(
            id=entry_id,
            file_path=str(path),
            kinds=("replaygain",),
            changed_fields=("replaygain_track_gain",),
            snapshot_json=SNAPSHOT,
            created_at=100.0,
        )
    )


@pytest.mark.asyncio
async def test_restoring_hands_the_snapshot_back_to_the_audio_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = tmp_path / "track.flac"
    track.write_bytes(b"audio")
    restores: list = []
    service, store = _service(tmp_path, restores=restores)
    await _record(store, track)

    monkeypatch.setattr(
        "services.native.enrichment_history_service.msgspec.json.decode",
        lambda *_args, **_kwargs: "decoded-snapshot",
    )

    await service.restore("one")

    assert restores == [(track, "decoded-snapshot")]
    entry = await store.get("one")
    assert entry is not None and entry.restored_at is not None


@pytest.mark.asyncio
async def test_a_change_cannot_be_restored_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = tmp_path / "track.flac"
    track.write_bytes(b"audio")
    service, store = _service(tmp_path)
    await _record(store, track)
    monkeypatch.setattr(
        "services.native.enrichment_history_service.msgspec.json.decode",
        lambda *_args, **_kwargs: "decoded-snapshot",
    )
    await service.restore("one")

    with pytest.raises(ValueError, match="already been restored"):
        await service.restore("one")


@pytest.mark.asyncio
async def test_a_file_that_moved_is_refused_rather_than_guessed_at(
    tmp_path: Path,
) -> None:
    """Restoring writes tags into a path; if it is gone, there is nothing safe
    to guess at."""
    service, store = _service(tmp_path)
    await _record(store, tmp_path / "gone.flac")

    with pytest.raises(FileNotFoundError):
        await service.restore("one")

    # Still restorable later, in case the file comes back.
    entry = await store.get("one")
    assert entry is not None and entry.restored_at is None


@pytest.mark.asyncio
async def test_an_unknown_entry_is_reported_clearly(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)

    with pytest.raises(LookupError):
        await service.restore("never-existed")
