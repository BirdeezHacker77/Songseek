"""Mapping one downloaded file to one expected release position.

A single-track download used to reject every well-tagged source. The expected
track sits at position 1 of the single; the file that arrives is the same
recording ripped from the album, carrying that album's position. The two numbers
never matched, so the map failed, the file was rejected as `tag_mismatch`
without even being held for review, and every candidate failed identically
because album rips all carry their album position.

An *untagged* rip passed, because the old fallback only filled the position in
when the file had none. Better tagging made rejection more likely.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from models.audio import AudioInfo, AudioTag
from models.download_manifest import ExpectedTrack
from services.native.file_processor import _slskd_expected_track

PARANOID_ANDROID = ExpectedTrack(
    track_number=1,
    disc_number=1,
    duration_seconds=387.9,
    recording_mbid="9f9cf187-d6f9-437f-9d98-d59cdbd52757",
    title="Paranoid Android",
    release_track_mbid="b724f9e9-cd60-458c-a65f-135a24db370b",
)


def _tag(
    title: str = "Paranoid Android",
    *,
    track_number: int = 2,
    disc_number: int = 1,
    artist: str = "Radiohead",
    recording_mbid: str | None = None,
) -> AudioTag:
    return AudioTag(
        title=title,
        artist=artist,
        album="OK Computer",
        track_number=track_number,
        disc_number=disc_number,
        musicbrainz_recording_id=recording_mbid,
    )


def _info(duration: float = 387.9) -> AudioInfo:
    return AudioInfo(
        duration_seconds=duration,
        bitrate=1411,
        sample_rate=44100,
        channels=2,
        file_format="flac",
        file_size_bytes=267_778_293,
    )


def _map(tag, info, expected, source="02 - Paranoid Android.flac"):  # noqa: ANN001, ANN202
    return _slskd_expected_track(Path(source), tag, info, expected, set())


# --- the regression -----------------------------------------------------------


def test_an_album_rip_satisfies_a_single_track_request() -> None:
    """The exact case that failed: one requested track, a file carrying the
    album position it was ripped at."""
    track, authoritative, conflict = _map(_tag(), _info(), [PARANOID_ANDROID])

    assert conflict is None
    assert track is PARANOID_ANDROID
    assert authoritative


def test_the_same_holds_when_the_position_comes_from_the_filename() -> None:
    """An untagged rip whose number is only in its name."""
    track, _, conflict = _map(
        _tag(track_number=0), _info(), [PARANOID_ANDROID]
    )

    assert conflict is None
    assert track is PARANOID_ANDROID


def test_a_disc_number_from_a_boxset_does_not_reject_a_single_track_request() -> None:
    track, _, conflict = _map(
        _tag(track_number=7, disc_number=2), _info(), [PARANOID_ANDROID]
    )

    assert conflict is None
    assert track is PARANOID_ANDROID


# --- what must still be rejected ----------------------------------------------


def test_a_different_song_is_still_rejected_on_its_title() -> None:
    """Position proving nothing does not mean nothing is checked."""
    track, authoritative, conflict = _map(
        _tag("Subterranean Homesick Alien", track_number=3),
        _info(267.0),
        [PARANOID_ANDROID],
    )

    assert conflict is not None
    assert not authoritative
    assert track is PARANOID_ANDROID  # mapped, but not corroborated


def test_a_different_recording_is_still_rejected_on_its_duration() -> None:
    """A live cut of the right song: same title, wrong length."""
    _, authoritative, conflict = _map(
        _tag(), _info(512.0), [PARANOID_ANDROID]
    )

    assert conflict == "duration conflicts with the exact edition"
    assert not authoritative


def test_a_conflicting_recording_mbid_is_still_rejected() -> None:
    _, authoritative, conflict = _map(
        _tag(recording_mbid="00000000-0000-0000-0000-000000000000"),
        _info(),
        [PARANOID_ANDROID],
    )

    assert conflict == "recording MBID conflicts with the exact edition"
    assert not authoritative


def test_two_files_cannot_claim_the_same_single_track() -> None:
    used: set[tuple[int, int]] = set()
    first = _slskd_expected_track(
        Path("02 - Paranoid Android.flac"), _tag(), _info(), [PARANOID_ANDROID], used
    )
    second = _slskd_expected_track(
        Path("05 - Paranoid Android.flac"), _tag(), _info(), [PARANOID_ANDROID], used
    )

    assert first[2] is None
    assert second[2] == "track position is missing, duplicated, or ambiguous"


# --- albums still map by position ---------------------------------------------


def _album() -> list[ExpectedTrack]:
    return [
        ExpectedTrack(
            track_number=position,
            disc_number=1,
            duration_seconds=duration,
            title=title,
        )
        for position, title, duration in (
            (1, "Airbag", 284.0),
            (2, "Paranoid Android", 387.9),
            (3, "Subterranean Homesick Alien", 267.0),
        )
    ]


def test_an_album_import_still_maps_each_file_to_its_own_position() -> None:
    """The single-track shortcut must not leak into a real album import."""
    track, _, conflict = _map(_tag(), _info(), _album())

    assert conflict is None
    assert track is not None and track.track_number == 2


def test_an_album_file_at_an_unknown_position_is_still_rejected() -> None:
    track, _, conflict = _map(_tag(track_number=99), _info(), _album())

    assert track is None
    assert conflict == "track position is missing, duplicated, or ambiguous"


def test_no_expected_tracks_maps_to_nothing() -> None:
    assert _map(_tag(), _info(), []) == (None, False, None)
