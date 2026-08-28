"""Identification of a finished download, and the gate that decides what to do.

The scoring expectations here were read off the real evidence engine rather than
guessed. That matters: the engine's own `score` sits at 1.0 even for an album
containing a track that is plainly a different song, so a gate built on it would
have auto-applied exactly the matches it exists to catch.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from api.v1.schemas.settings import DownloadTaggingSettings
from infrastructure.audio.metadata_engine import AudioMetadataEngine
from models.identification import AlbumCandidate, CandidateTrack, GroupingTrack
from services.native.album_evidence_engine import AlbumEvidenceEngine
from services.native.post_import_identification_service import (
    PostImportIdentificationService,
)

RELEASE_TITLES = ("Doll", "Monkey Wrench", "Hey, Johnny Park!")
FIXTURE = Path(__file__).parents[2] / "fixtures" / "library" / "flac_full_01.flac"


class _Recall:
    """Stands in for MusicBrainz. Records how it was called."""

    def __init__(self, candidates: list[AlbumCandidate] | None = None) -> None:
        self.candidates = candidates if candidates is not None else [_candidate()]
        self.calls: list[dict] = []

    async def recall(self, tracks, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.calls.append({"tracks": len(tracks), **kwargs})
        return self.candidates


class _Failing:
    async def recall(self, tracks, **kwargs):  # noqa: ANN001, ANN003, ANN201
        raise RuntimeError("musicbrainz is down")


def _candidate(titles: tuple[str, ...] = RELEASE_TITLES) -> AlbumCandidate:
    return AlbumCandidate(
        release_group_mbid="rg-colour-shape",
        release_mbid="rel-colour-shape",
        album_title="The Colour and the Shape",
        album_artist_name="Foo Fighters",
        artist_mbid="artist-foo-fighters",
        release_type="album",
        release_date="1997-05-20",
        tracks=[
            CandidateTrack(
                title=title,
                position=position,
                disc_number=1,
                absolute_position=position,
                duration_seconds=0.3,
                recording_mbid=f"rec-{position}",
                release_track_mbid=f"rt-{position}",
            )
            for position, title in enumerate(titles, start=1)
        ],
    )


def _local(
    titles: tuple[str, ...],
    *,
    album: str = "the colour and the shape",
    artist: str = "Foo Fighters",
) -> list[GroupingTrack]:
    return [
        GroupingTrack(
            local_track_id=f"/music/{position:02d}.flac",
            root_id="",
            relative_path=f"{position:02d}.flac",
            title=title,
            artist_name=artist,
            album_title=album,
            album_artist_name=artist,
            track_number=position,
            disc_number=1,
            duration_seconds=0.3,
        )
        for position, title in enumerate(titles, start=1)
    ]


def _service(recall=None, tracks: list[GroupingTrack] | None = None):  # noqa: ANN001, ANN202
    """A service whose file reading is replaced by ready-made tracks.

    The read path has its own test against real files below; these cases are
    about the gate, and building them from tags would say more about mutagen
    than about the decision under test.
    """

    service = PostImportIdentificationService(
        AudioMetadataEngine(), recall or _Recall(), AlbumEvidenceEngine()
    )
    if tracks is not None:

        async def _tracks(paths):  # noqa: ANN001, ANN202
            return tracks

        service._local_tracks = _tracks  # type: ignore[method-assign]
    return service


ENABLED = DownloadTaggingSettings(enabled=True)


# --- the gate -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "titles", "album", "expected_status", "expected_confidence"),
    [
        (
            "every track and the album line up",
            ("doll", "monkey wrench", "hey, johnny park!"),
            "the colour and the shape",
            "applied",
            1.0,
        ),
        (
            "a partial download of a real release still applies",
            ("doll", "monkey wrench"),
            "the colour and the shape",
            "applied",
            1.0,
        ),
        (
            "a single downloaded track identifies its release",
            ("doll",),
            "the colour and the shape",
            "applied",
            1.0,
        ),
        (
            "one track is a different song",
            ("doll", "wrong song", "hey, johnny park!"),
            "the colour and the shape",
            "review",
            0.8,
        ),
        (
            "two tracks are different songs",
            ("doll", "wrong song", "other thing"),
            "the colour and the shape",
            "review",
            0.6,
        ),
        (
            "nothing but the album name matches",
            ("a", "b", "c"),
            "the colour and the shape",
            "unmatched",
            0.4,
        ),
        (
            "the download carries a track the release does not",
            ("doll", "monkey wrench", "hey, johnny park!", "bonus"),
            "the colour and the shape",
            "review",
            0.833,
        ),
        (
            "the tracks match but the album is named something else",
            ("doll", "monkey wrench", "hey, johnny park!"),
            "greatest hits",
            "review",
            0.8,
        ),
    ],
)
async def test_the_gate_decides_by_how_much_of_the_release_lined_up(
    case: str,
    titles: tuple[str, ...],
    album: str,
    expected_status: str,
    expected_confidence: float,
) -> None:
    service = _service(tracks=_local(titles, album=album))

    proposal = await service.propose([Path("/music/01.flac")], ENABLED)

    assert proposal.status == expected_status, case
    assert proposal.score == pytest.approx(expected_confidence, abs=0.001), case


@pytest.mark.asyncio
async def test_a_contradicted_album_is_never_applied_however_high_it_scores() -> None:
    """The engine scores this 1.0. One track is a different song.

    This is the case the whole gate exists for: a threshold read off the
    engine's own score would write this release's titles and positions over a
    track that is not on it.
    """
    service = _service(
        tracks=_local(("doll", "wrong song", "hey, johnny park!"))
    )

    proposal = await service.propose(
        [Path("/music/01.flac")],
        DownloadTaggingSettings(enabled=True, auto_accept_score=0.5),
    )

    assert proposal.status == "review"
    assert proposal.fields_by_path == {}


@pytest.mark.asyncio
async def test_a_review_carries_no_tag_writes() -> None:
    service = _service(tracks=_local(("doll", "wrong song", "other thing")))

    proposal = await service.propose([Path("/music/01.flac")], ENABLED)

    assert proposal.status == "review"
    assert proposal.fields_by_path == {}
    # It still has to say what it thought it was, or the review is unanswerable.
    assert proposal.release_mbid == "rel-colour-shape"
    assert proposal.album_title == "The Colour and the Shape"
    assert proposal.local_album_title == "the colour and the shape"


@pytest.mark.asyncio
async def test_the_thresholds_are_honoured() -> None:
    tracks = _local(("doll", "wrong song", "hey, johnny park!"))  # 0.8 confidence

    lenient = await _service(tracks=tracks).propose(
        [Path("/music/01.flac")],
        DownloadTaggingSettings(enabled=True, auto_accept_score=0.9, review_score=0.9),
    )
    strict = await _service(tracks=tracks).propose(
        [Path("/music/01.flac")],
        DownloadTaggingSettings(enabled=True, auto_accept_score=0.95, review_score=0.9),
    )

    assert lenient.status == "unmatched"
    assert strict.status == "unmatched"


def test_a_review_floor_above_the_accept_score_is_clamped() -> None:
    """Otherwise there is a band that is too good to flag and too poor to write,
    and every download landing in it would silently do nothing."""
    settings = DownloadTaggingSettings(auto_accept_score=0.6, review_score=0.9)

    assert settings.review_score == 0.6


# --- not reaching out at all --------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_is_looked_up_when_the_setting_is_off() -> None:
    recall = _Recall()
    service = _service(recall, tracks=_local(RELEASE_TITLES))

    proposal = await service.propose(
        [Path("/music/01.flac")], DownloadTaggingSettings(enabled=False)
    )

    assert proposal.status == "unmatched"
    assert recall.calls == []


@pytest.mark.asyncio
async def test_a_download_with_no_album_tags_is_not_searched_for() -> None:
    recall = _Recall()
    service = _service(recall, tracks=_local(("doll",), album="", artist=""))

    proposal = await service.propose([Path("/music/01.flac")], ENABLED)

    assert proposal.status == "unmatched"
    assert proposal.reason_code == "NO_TAGS"
    assert recall.calls == []


@pytest.mark.asyncio
async def test_a_provider_failure_leaves_the_import_alone() -> None:
    service = _service(_Failing(), tracks=_local(RELEASE_TITLES))

    proposal = await service.propose([Path("/music/01.flac")], ENABLED)

    assert proposal.status == "unmatched"


@pytest.mark.asyncio
async def test_one_lookup_per_publication_not_one_per_track() -> None:
    """MusicBrainz allows a request a second; an album is many files."""
    recall = _Recall()
    service = _service(recall, tracks=_local(RELEASE_TITLES))

    await service.propose(
        [Path(f"/music/{index:02d}.flac") for index in range(1, 4)], ENABLED
    )

    assert len(recall.calls) == 1


# --- an accepted review -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_accepted_release_is_written_whatever_it_scored() -> None:
    """Once somebody has been asked and answered, their answer is the decision."""
    recall = _Recall()
    service = _service(recall, tracks=_local(("doll", "wrong song", "other thing")))

    proposal = await service.propose_release(
        [Path("/music/01.flac")],
        ENABLED,
        release_mbid="rel-colour-shape",
        release_group_mbid="rg-colour-shape",
    )

    assert proposal.status == "applied"
    assert proposal.score < ENABLED.auto_accept_score
    assert proposal.fields_by_path
    assert recall.calls[0]["exact_release_mbid"] == "rel-colour-shape"


@pytest.mark.asyncio
async def test_a_release_that_cannot_be_fetched_is_not_applied() -> None:
    service = _service(_Recall(candidates=[]), tracks=_local(RELEASE_TITLES))

    proposal = await service.propose_release(
        [Path("/music/01.flac")],
        ENABLED,
        release_mbid="rel-colour-shape",
        release_group_mbid=None,
    )

    assert proposal.status == "unmatched"
    assert proposal.reason_code == "RELEASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_group_fallback_refuses_a_candidate_from_another_group() -> None:
    """Accepting must apply the release that was flagged, not whatever recall
    happens to rank first now."""
    other = _candidate()
    other.release_group_mbid = "rg-something-else"
    service = _service(_Recall(candidates=[other]), tracks=_local(RELEASE_TITLES))

    proposal = await service.propose_release(
        [Path("/music/01.flac")],
        ENABLED,
        release_mbid=None,
        release_group_mbid="rg-colour-shape",
    )

    assert proposal.status == "unmatched"


# --- what actually gets written -----------------------------------------------


@pytest.mark.asyncio
async def test_the_proposed_tags_come_from_the_release(tmp_path: Path) -> None:
    """Read from real files, so the tag names and shapes are the real ones."""
    paths = _album(tmp_path, ("doll", "monkey wrench", "hey, johnny park!"))
    service = _service()

    proposal = await service.propose(paths, ENABLED)

    assert proposal.status == "applied"
    first = {
        field.name: field.value for field in proposal.fields_by_path[str(paths[0])]
    }
    assert first["album"] == "The Colour and the Shape"
    assert first["album_artist"] == ("Foo Fighters",)
    assert first["title"] == "Doll"
    assert first["track_number"] == 1
    assert first["disc_number"] == 1
    assert first["musicbrainz_release_id"] == "rel-colour-shape"
    assert first["musicbrainz_release_group_id"] == "rg-colour-shape"
    assert first["musicbrainz_recording_id"] == "rec-1"
    assert first["musicbrainz_release_track_id"] == "rt-1"


@pytest.mark.asyncio
async def test_identifiers_can_be_written_without_touching_the_titles(
    tmp_path: Path,
) -> None:
    paths = _album(tmp_path, ("doll", "monkey wrench", "hey, johnny park!"))

    proposal = await _service().propose(
        paths,
        DownloadTaggingSettings(enabled=True, rewrite_titles=False),
    )

    names = {field.name for field in proposal.fields_by_path[str(paths[0])]}
    assert "musicbrainz_release_id" in names
    # Positions go with the titles: somebody who asked for their tags to be left
    # alone did not ask for their track numbers to be renumbered.
    assert names.isdisjoint({"album", "album_artist", "title", "track_number"})


@pytest.mark.asyncio
async def test_titles_can_be_corrected_without_writing_identifiers(
    tmp_path: Path,
) -> None:
    paths = _album(tmp_path, ("doll", "monkey wrench", "hey, johnny park!"))

    proposal = await _service().propose(
        paths,
        DownloadTaggingSettings(enabled=True, write_identifiers=False),
    )

    names = {field.name for field in proposal.fields_by_path[str(paths[0])]}
    assert "title" in names
    assert not any(name.startswith("musicbrainz") for name in names)


def _album(directory: Path, titles: tuple[str, ...]) -> list[Path]:
    """A download's worth of real FLACs, tagged the way an uploader would.

    The MusicBrainz tags the fixture ships with are cleared: a Soulseek download
    does not carry them, and three copies of one fixture all claiming the same
    recording id is a contradiction the evidence engine rightly refuses.
    """
    import mutagen

    paths: list[Path] = []
    for position, title in enumerate(titles, start=1):
        path = directory / f"{position:02d} {title}.flac"
        shutil.copy2(FIXTURE, path)
        audio = mutagen.File(path)
        for key in list(audio.keys()):
            if key.lower().startswith("musicbrainz") or key.lower() == "acoustid_id":
                del audio[key]
        audio["title"] = [title]
        audio["artist"] = ["Foo Fighters"]
        audio["album"] = ["the colour and the shape"]
        audio["albumartist"] = ["Foo Fighters"]
        audio["tracknumber"] = [str(position)]
        audio["discnumber"] = ["1"]
        audio.save()
        paths.append(path)
    return paths


# --- a single-track download identifies its release ---------------------------


def _one_matched_track(release_size: int):  # noqa: ANN202
    """One downloaded file, matched cleanly, against a release of N tracks."""
    from models.identification import CandidateEvidence, TrackEvidence

    return CandidateEvidence(
        release_group_mbid="rg",
        release_mbid="rel",
        album_title="Paranoid Android",
        album_artist_name="Radiohead",
        album_title_classification="supported",
        album_artist_classification="supported",
        track_evidence=[TrackEvidence(local_track_id="1", classification="supported")],
        unmatched_expected_tracks=[f"t{i}" for i in range(release_size - 1)],
    )


@pytest.mark.parametrize("release_size", [1, 3, 5, 10, 12, 50])
def test_the_size_of_the_release_does_not_change_a_matched_track(
    release_size: int,
) -> None:
    """The regression, seen in production as `status=unmatched score=0.25`.

    Counting the release's other tracks against the download measured
    completeness, not identity: one file matching one track of a ten-track
    release scored 0.25 and fell below even the review threshold, so a
    single-track download could never be identified - and the bigger the album
    the song came from, the more certain the failure.

    Not having downloaded the other nine says nothing about whether this is the
    right release.
    """
    confidence = PostImportIdentificationService.confidence(
        _one_matched_track(release_size)
    )

    assert confidence == pytest.approx(1.0)


def test_a_single_track_that_contradicts_the_release_is_still_doubted() -> None:
    """Ignoring the tracks we did not download must not make everything certain."""
    from models.identification import CandidateEvidence, TrackEvidence

    evidence = CandidateEvidence(
        release_group_mbid="rg",
        release_mbid="rel",
        album_title_classification="supported",
        album_artist_classification="supported",
        track_evidence=[
            TrackEvidence(local_track_id="1", classification="contradictory")
        ],
        unmatched_expected_tracks=["a"] * 9,
    )

    confidence = PostImportIdentificationService.confidence(evidence)

    # Between the thresholds: asked about, never applied.
    assert ENABLED.review_score <= confidence < ENABLED.auto_accept_score
