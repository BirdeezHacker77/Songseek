"""Answering an identification review."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from api.v1.schemas.settings import DownloadEnrichmentSettings, DownloadTaggingSettings
from infrastructure.persistence.import_review_store import ImportReviewStore
from models.audio_metadata import DesiredAudioField
from services.native.import_review_service import ImportReviewError, ImportReviewService
from services.native.post_import_identification_service import IdentificationProposal


class _Identification:
    def __init__(self, proposal: IdentificationProposal | None = None) -> None:
        self.proposal = proposal
        self.calls: list[dict] = []

    async def propose_release(self, paths, settings, *, release_mbid, release_group_mbid):  # noqa: ANN001, ANN201
        self.calls.append(
            {
                "paths": [str(value) for value in paths],
                "release_mbid": release_mbid,
                "release_group_mbid": release_group_mbid,
            }
        )
        if self.proposal is not None:
            return self.proposal
        return IdentificationProposal(
            status="applied",
            score=0.62,
            release_mbid=release_mbid,
            album_title="The Colour and the Shape",
            fields_by_path={
                str(path): (
                    DesiredAudioField(
                        name="album", action="set", value="The Colour and the Shape"
                    ),
                )
                for path in paths
            },
        )


class _Enrichment:
    def __init__(self, *, writes: bool = True) -> None:
        self.written: list[str] = []
        self._writes = writes

    async def apply_tag_fields(self, path, fields) -> bool:  # noqa: ANN001
        self.written.append(str(path))
        return self._writes


def _service(tmp_path: Path, identification=None, enrichment=None):  # noqa: ANN001, ANN202
    store = ImportReviewStore(
        db_path=tmp_path / "library.db", write_lock=threading.Lock()
    )
    service = ImportReviewService(
        store,
        identification or _Identification(),
        enrichment or _Enrichment(),
        lambda: DownloadEnrichmentSettings(
            tagging=DownloadTaggingSettings(enabled=True)
        ),
    )
    return service, store


async def _flag(service, paths: list[Path]) -> str:
    await service.record(
        IdentificationProposal(
            status="review",
            score=0.62,
            reason_code="CONFLICTING_TRACK_EVIDENCE",
            release_mbid="rel-1",
            release_group_mbid="rg-1",
            album_title="The Colour and the Shape",
            album_artist_name="Foo Fighters",
            local_album_title="colour+shape",
            local_album_artist_name="foo fighters",
            paths=tuple(str(value) for value in paths),
        )
    )
    page = await service.list_entries()
    return page.items[0].id


def _tracks(tmp_path: Path, count: int = 2) -> list[Path]:
    paths = []
    for index in range(1, count + 1):
        path = tmp_path / f"{index:02d}.flac"
        path.write_bytes(b"not really audio")
        paths.append(path)
    return paths


@pytest.mark.asyncio
async def test_a_flagged_import_becomes_a_pending_review(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    entry_id = await _flag(service, _tracks(tmp_path))

    page = await service.list_entries()
    assert page.total == 1
    entry = page.items[0]
    assert entry.id == entry_id
    assert entry.status == "pending"
    # Both sides of the question, or there is nothing to compare.
    assert entry.local_album_title == "colour+shape"
    assert entry.album_title == "The Colour and the Shape"


@pytest.mark.asyncio
async def test_accepting_writes_the_release_and_closes_the_review(
    tmp_path: Path,
) -> None:
    paths = _tracks(tmp_path)
    enrichment = _Enrichment()
    service, _ = _service(tmp_path, enrichment=enrichment)
    entry_id = await _flag(service, paths)

    written = await service.accept(entry_id)

    assert written == 2
    assert enrichment.written == [str(path) for path in paths]
    assert (await service.list_entries()).total == 0


@pytest.mark.asyncio
async def test_accepting_refetches_the_release_rather_than_replaying_old_tags(
    tmp_path: Path,
) -> None:
    """The answer may come weeks later, and MusicBrainz may have corrected the
    release since. The correction is the thing worth writing."""
    paths = _tracks(tmp_path)
    identification = _Identification()
    service, _ = _service(tmp_path, identification=identification)
    entry_id = await _flag(service, paths)

    await service.accept(entry_id)

    assert identification.calls[0]["release_mbid"] == "rel-1"
    assert identification.calls[0]["paths"] == [str(path) for path in paths]


@pytest.mark.asyncio
async def test_accepting_refuses_when_the_album_has_moved(tmp_path: Path) -> None:
    """Half an album tagged as one release and half as another is worse than an
    unanswered question."""
    paths = _tracks(tmp_path)
    enrichment = _Enrichment()
    service, _ = _service(tmp_path, enrichment=enrichment)
    entry_id = await _flag(service, paths)
    paths[1].unlink()

    with pytest.raises(ImportReviewError, match="moved or been removed"):
        await service.accept(entry_id)

    assert enrichment.written == []
    # Still answerable once the files are back.
    assert (await service.list_entries()).total == 1


@pytest.mark.asyncio
async def test_a_release_that_cannot_be_fetched_leaves_the_review_open(
    tmp_path: Path,
) -> None:
    service, _ = _service(
        tmp_path,
        identification=_Identification(IdentificationProposal(status="unmatched")),
    )
    entry_id = await _flag(service, _tracks(tmp_path))

    with pytest.raises(ImportReviewError, match="MusicBrainz"):
        await service.accept(entry_id)

    assert (await service.list_entries()).total == 1


@pytest.mark.asyncio
async def test_accepting_a_download_that_already_matched_still_answers_it(
    tmp_path: Path,
) -> None:
    """Zero writes is a normal accept: the files already carried those tags, and
    the review was only ever asking whether it was the right release."""
    service, _ = _service(tmp_path, enrichment=_Enrichment(writes=False))
    entry_id = await _flag(service, _tracks(tmp_path))

    written = await service.accept(entry_id)

    assert written == 0
    assert (await service.list_entries()).total == 0


@pytest.mark.asyncio
async def test_a_review_cannot_be_answered_twice(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    entry_id = await _flag(service, _tracks(tmp_path))
    await service.accept(entry_id)

    with pytest.raises(ImportReviewError, match="already been answered"):
        await service.accept(entry_id)


@pytest.mark.asyncio
async def test_dismissing_leaves_the_files_alone(tmp_path: Path) -> None:
    enrichment = _Enrichment()
    service, _ = _service(tmp_path, enrichment=enrichment)
    entry_id = await _flag(service, _tracks(tmp_path))

    assert await service.dismiss(entry_id) is True

    assert enrichment.written == []
    assert (await service.list_entries()).total == 0
    assert await service.dismiss(entry_id) is False


@pytest.mark.asyncio
async def test_answering_a_review_that_no_longer_exists_says_so(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ImportReviewError, match="no longer exists"):
        await service.accept("nope")
