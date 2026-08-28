import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.v1.schemas.settings import (
    DownloadEnrichmentSettings,
    DownloadGenreSettings,
    DownloadLyricsSettings,
    DownloadRefreshSettings,
    DownloadReplayGainSettings,
    DownloadTaggingSettings,
)
from models.audio import AudioInfo, AudioTag
from models.library_management_enrichment import LyricsCandidate, LyricsLookupResult
from services.native import post_import_enrichment_service as module
from services.native.post_import_enrichment_service import PostImportEnrichmentService

TAG = AudioTag(
    title="Aria",
    artist="Glenn Gould",
    album="Goldberg Variations",
    track_number=1,
)
INFO = AudioInfo(
    duration_seconds=180.0,
    bitrate=1000,
    sample_rate=44100,
    channels=2,
    file_format="flac",
    file_size_bytes=1024,
)


def _candidate(**overrides) -> LyricsCandidate:  # noqa: ANN003
    values = {
        "provider_id": 1,
        "track_name": "Aria",
        "artist_name": "Glenn Gould",
        "album_name": "Goldberg Variations",
        "duration_seconds": 180.0,
        "instrumental": False,
        "plain_lyrics": "Plain words",
        "synced_lyrics": "[00:01.00]Synced words",
        "provider_revision": "r1",
    }
    values.update(overrides)
    return LyricsCandidate(**values)


def _service(monkeypatch, *, found=True, candidate=None, settings=None, calls=None):  # noqa: ANN001, ANN202
    monkeypatch.setattr(module, "legacy_audio_projection", lambda _doc: (TAG, INFO))

    async def get_exact_lyrics(**kwargs):  # noqa: ANN003
        if calls is not None:
            calls.append(kwargs)
        return LyricsLookupResult(found=found, candidate=candidate if found else None)

    return PostImportEnrichmentService(
        lambda: (
            settings
            or DownloadEnrichmentSettings(
                lyrics=DownloadLyricsSettings(enabled=True, write_lrc_file=True)
            )
        ),
        SimpleNamespace(get_exact_lyrics=get_exact_lyrics),
        SimpleNamespace(read=lambda _path: object()),
    )


def _track(tmp_path: Path) -> Path:
    path = tmp_path / "01 - Aria.flac"
    path.write_bytes(b"audio")
    return path


@pytest.mark.asyncio
async def test_writes_a_synced_sidecar_beside_the_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _track(tmp_path)
    service = _service(monkeypatch, candidate=_candidate())

    await service.enrich([str(track)])

    # normalize_lrc canonicalises the timestamp to three-digit milliseconds.
    assert track.with_suffix(".lrc").read_text(encoding="utf-8") == (
        "[00:01.000]Synced words\n"
    )


@pytest.mark.asyncio
async def test_plain_text_is_used_when_synced_is_not_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _track(tmp_path)
    service = _service(
        monkeypatch,
        candidate=_candidate(),
        settings=DownloadEnrichmentSettings(
            lyrics=DownloadLyricsSettings(
                enabled=True, write_lrc_file=True, prefer_synced=False
            )
        ),
    )

    await service.enrich([str(track)])

    assert track.with_suffix(".lrc").read_text(encoding="utf-8") == "Plain words\n"


@pytest.mark.asyncio
async def test_a_sidecar_that_shipped_with_the_download_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-fetching would replace lyrics somebody deliberately supplied."""
    track = _track(tmp_path)
    track.with_suffix(".lrc").write_text("[00:02.00]Shipped\n", encoding="utf-8")
    calls: list[dict] = []
    service = _service(monkeypatch, candidate=_candidate(), calls=calls)

    await service.enrich([str(track)])

    assert (
        track.with_suffix(".lrc").read_text(encoding="utf-8") == "[00:02.00]Shipped\n"
    )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        DownloadEnrichmentSettings(),
        DownloadEnrichmentSettings(
            lyrics=DownloadLyricsSettings(enabled=True, write_lrc_file=False)
        ),
    ],
    ids=["lyrics-off", "sidecar-off"],
)
async def test_nothing_is_written_when_the_setting_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings
) -> None:  # noqa: ANN001
    track = _track(tmp_path)
    service = _service(monkeypatch, candidate=_candidate(), settings=settings)

    await service.enrich([str(track)])

    assert not track.with_suffix(".lrc").exists()


@pytest.mark.asyncio
async def test_an_instrumental_gets_no_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _track(tmp_path)
    service = _service(monkeypatch, candidate=_candidate(instrumental=True))

    await service.enrich([str(track)])

    assert not track.with_suffix(".lrc").exists()


@pytest.mark.asyncio
async def test_a_different_recording_of_the_same_title_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better no lyrics than lyrics that drift against the actual audio."""
    track = _track(tmp_path)
    service = _service(monkeypatch, candidate=_candidate(duration_seconds=240.0))

    await service.enrich([str(track)])

    assert not track.with_suffix(".lrc").exists()


@pytest.mark.asyncio
async def test_a_miss_leaves_the_track_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = _track(tmp_path)
    service = _service(monkeypatch, found=False)

    await service.enrich([str(track)])

    assert not track.with_suffix(".lrc").exists()


@pytest.mark.asyncio
async def test_one_failing_track_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The music is already in the library; enrichment must never undo that."""
    good = _track(tmp_path)
    missing = tmp_path / "gone.flac"
    reads: list[Path] = []

    def read(path: Path):  # noqa: ANN202
        reads.append(path)
        if path == missing:
            raise OSError("no such file")
        return object()

    monkeypatch.setattr(module, "legacy_audio_projection", lambda _doc: (TAG, INFO))

    async def get_exact_lyrics(**_kwargs):  # noqa: ANN003
        return LyricsLookupResult(found=True, candidate=_candidate())

    service = PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(
            lyrics=DownloadLyricsSettings(
                enabled=True, write_lrc_file=True, embed_in_tags=False
            )
        ),
        SimpleNamespace(get_exact_lyrics=get_exact_lyrics),
        SimpleNamespace(read=read),
    )

    await service.enrich([str(missing), str(good)])

    assert reads == [missing, good]
    assert good.with_suffix(".lrc").exists()


class _Gain:
    def __init__(self, **values) -> None:  # noqa: ANN003
        self.track_gain_db = values.get("track_gain_db")
        self.track_peak = values.get("track_peak")
        self.album_gain_db = values.get("album_gain_db")
        self.album_peak = values.get("album_peak")


def _document(fmt: str = "flac"):  # noqa: ANN202
    return SimpleNamespace(probe=SimpleNamespace(detected_format=fmt))


def _bare_service(genres=None):  # noqa: ANN001, ANN202
    """No genre normalizer by default, so _desired_fields covers only the field
    under test."""
    return PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(),
        SimpleNamespace(get_exact_lyrics=None),
        SimpleNamespace(read=None),
        None,
        None,
        None,
        None,
        genres,
    )


def _fields(candidate, gain, settings, genre_settings=None):  # noqa: ANN001, ANN202
    return {
        field.name: field.value
        for field in _bare_service()._desired_fields(
            _document(),
            candidate,
            gain,
            settings,
            genre_settings or DownloadGenreSettings(),
        )
    }


def test_loudness_is_written_as_the_four_replaygain_fields() -> None:
    fields = _fields(
        None,
        _Gain(track_gain_db=-7.5, track_peak=0.98, album_gain_db=-8.0, album_peak=1.0),
        DownloadLyricsSettings(enabled=True),
    )

    assert fields == {
        "replaygain_track_gain": -7.5,
        "replaygain_track_peak": 0.98,
        "replaygain_album_gain": -8.0,
        "replaygain_album_peak": 1.0,
    }


def test_per_track_only_analysis_writes_no_album_fields() -> None:
    """album_aware off leaves the album gains unset rather than writing nulls."""
    fields = _fields(
        None,
        _Gain(track_gain_db=-7.5, track_peak=0.98),
        DownloadLyricsSettings(enabled=True),
    )

    assert set(fields) == {"replaygain_track_gain", "replaygain_track_peak"}


def test_lyrics_are_embedded_only_when_the_setting_asks_for_it() -> None:
    candidate = _candidate()

    embedded = _fields(
        candidate, None, DownloadLyricsSettings(enabled=True, embed_in_tags=True)
    )
    assert embedded["lyrics_plain"] == "Plain words"
    assert embedded["lyrics_synced"] == "[00:01.000]Synced words"


def test_synced_lyrics_are_skipped_for_a_container_that_cannot_hold_them() -> None:
    """The managed path gates on the same capability rather than letting the
    write plan come back blocked."""
    fields = {
        field.name: field.value
        for field in _bare_service()._desired_fields(
            _document("m4a"),
            _candidate(),
            None,
            DownloadLyricsSettings(enabled=True, embed_in_tags=True),
            DownloadGenreSettings(),
        )
    }

    assert "lyrics_synced" not in fields
    assert fields["lyrics_plain"] == "Plain words"


def test_plain_only_when_synced_is_not_preferred() -> None:
    fields = _fields(
        _candidate(),
        None,
        DownloadLyricsSettings(enabled=True, embed_in_tags=True, prefer_synced=False),
    )

    assert set(fields) == {"lyrics_plain"}


@pytest.mark.asyncio
async def test_no_tag_write_is_attempted_when_there_is_nothing_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """embed_in_tags off with no loudness result must not touch the audio file."""
    track = _track(tmp_path)
    before = track.read_bytes()
    service = _service(
        monkeypatch,
        candidate=_candidate(),
        settings=DownloadEnrichmentSettings(
            lyrics=DownloadLyricsSettings(
                enabled=True, write_lrc_file=True, embed_in_tags=False
            )
        ),
    )

    await service.enrich([str(track)])

    assert track.read_bytes() == before
    assert not track.with_name(f"{track.name}.songseek-enrich").exists()
    assert track.with_suffix(".lrc").exists()


class _Server:
    def __init__(self, *, configured: bool = True) -> None:
        self._configured = configured
        self.scans = 0

    def is_configured(self) -> bool:
        return self._configured

    async def start_scan(self) -> bool:
        self.scans += 1
        return True

    async def refresh_library(self) -> None:
        self.scans += 1


def _refresh_service(navidrome, jellyfin, refresh):  # noqa: ANN001, ANN202
    return PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(refresh=refresh),
        SimpleNamespace(get_exact_lyrics=None),
        SimpleNamespace(read=None),
        None,
        lambda: navidrome,
        lambda: jellyfin,
    )


@pytest.mark.asyncio
async def test_only_the_selected_media_servers_are_refreshed(tmp_path: Path) -> None:
    navidrome, jellyfin = _Server(), _Server()
    service = _refresh_service(
        navidrome,
        jellyfin,
        DownloadRefreshSettings(enabled=True, navidrome_enabled=True),
    )

    await service.enrich([str(_track(tmp_path))])

    assert (navidrome.scans, jellyfin.scans) == (1, 0)


@pytest.mark.asyncio
async def test_refresh_happens_once_per_publication_not_per_track(
    tmp_path: Path,
) -> None:
    """An album is one import; scanning per track would hammer the server."""
    navidrome = _Server()
    service = _refresh_service(
        navidrome,
        _Server(),
        DownloadRefreshSettings(enabled=True, navidrome_enabled=True),
    )
    tracks = [str(tmp_path / f"{index}.flac") for index in range(4)]
    for path in tracks:
        Path(path).write_bytes(b"audio")

    await service.enrich(tracks)

    assert navidrome.scans == 1


@pytest.mark.asyncio
async def test_an_unconfigured_server_is_skipped(tmp_path: Path) -> None:
    navidrome = _Server(configured=False)
    service = _refresh_service(
        navidrome,
        _Server(),
        DownloadRefreshSettings(enabled=True, navidrome_enabled=True),
    )

    await service.enrich([str(_track(tmp_path))])

    assert navidrome.scans == 0


@pytest.mark.asyncio
async def test_a_failing_refresh_does_not_raise(tmp_path: Path) -> None:
    """The music is already imported; a server that will not answer must not
    turn a finished import into an error."""

    class _Broken(_Server):
        async def start_scan(self) -> bool:
            raise RuntimeError("unreachable")

    jellyfin = _Server()
    service = _refresh_service(
        _Broken(),
        jellyfin,
        DownloadRefreshSettings(
            enabled=True, navidrome_enabled=True, jellyfin_enabled=True
        ),
    )

    await service.enrich([str(_track(tmp_path))])

    # The second target still gets its refresh.
    assert jellyfin.scans == 1


@pytest.mark.asyncio
async def test_nothing_is_refreshed_when_the_setting_is_off(tmp_path: Path) -> None:
    navidrome = _Server()
    service = _refresh_service(
        navidrome, _Server(), DownloadRefreshSettings(navidrome_enabled=True)
    )

    await service.enrich([str(_track(tmp_path))])

    assert navidrome.scans == 0


def test_the_staged_copy_keeps_an_extension_a_write_plan_will_accept() -> None:
    """A plan is refused when a file's extension does not match its container,
    so a staged name ending in anything but the real suffix fails before a
    single tag is written."""
    staged = PostImportEnrichmentService._staged_path(
        Path("/data/Music/Artist/Album/0108 Hallowed Be Thy Name.flac")
    )

    assert staged.suffix == ".flac"
    assert staged.parent == Path("/data/Music/Artist/Album")
    # Hidden, so a media server scanning the folder mid-write skips it.
    assert staged.name.startswith(".")
    assert staged != Path("/data/Music/Artist/Album/0108 Hallowed Be Thy Name.flac")


def test_the_staged_copy_survives_a_dotted_track_name() -> None:
    staged = PostImportEnrichmentService._staged_path(Path("/m/A/B/01 - Track 1.5.mp3"))

    assert staged.suffix == ".mp3"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "The audio writer fsyncs the staged file, which fails with EBADF on "
        "Windows. The write path itself is platform-independent; only this "
        "harness cannot exercise it."
    ),
)
@pytest.mark.asyncio
async def test_replaygain_really_lands_in_the_file(tmp_path: Path) -> None:
    """End to end against a real FLAC, through the actual write pipeline.

    The name-only checks above pass happily while the write still fails: the
    first version of this staged to `<track>.flac.songseek-enrich`, whose
    extension no longer matched its container, so every plan was refused before
    a tag was written. Only writing and reading a real file catches that.
    """
    import shutil as _shutil

    import mutagen

    from infrastructure.audio.metadata_engine import AudioMetadataEngine

    fixture = Path(__file__).parents[2] / "fixtures" / "library" / "flac_full_01.flac"
    track = tmp_path / "0108 Hallowed Be Thy Name.flac"
    _shutil.copy2(fixture, track)

    class _Analysis:
        status = "available"
        reason = None
        tracks = ()

    class _ReplayGain:
        async def analyze(self, paths, *, album_aware):  # noqa: ANN001, ANN202
            self.album_aware = album_aware
            return SimpleNamespace(
                status="available",
                reason=None,
                tracks=(
                    SimpleNamespace(
                        source_path=str(paths[0]),
                        track_gain_db=-7.5,
                        track_peak=0.98,
                        album_gain_db=None,
                        album_peak=None,
                    ),
                ),
            )

    service = PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(
            replaygain=DownloadReplayGainSettings(enabled=True, album_aware=False)
        ),
        SimpleNamespace(get_exact_lyrics=None),
        AudioMetadataEngine(),
        _ReplayGain(),
    )

    await service.enrich([str(track)])

    tags = {
        key.lower(): value
        for key, value in dict(mutagen.File(track).tags).items()
        if "replaygain" in key.lower()
    }
    assert "replaygain_track_gain" in tags
    assert "replaygain_track_peak" in tags
    # album_aware off must not invent album values
    assert "replaygain_album_gain" not in tags
    # the staged copy is cleaned up either way
    assert list(tmp_path.glob(".songseek-enrich*")) == []


def _genre_document(*values: str):  # noqa: ANN202
    return SimpleNamespace(
        probe=SimpleNamespace(detected_format="flac"),
        metadata=SimpleNamespace(strings_for=lambda _name: list(values)),
    )


def _tidy(*values: str, settings=None, normalizer=None):  # noqa: ANN001, ANN202
    from services.native.genre_normalizer import GenreNormalizer

    service = _bare_service(normalizer or GenreNormalizer())
    return service._tidy_genres(
        _genre_document(*values),
        settings or DownloadGenreSettings(enabled=True),
    )


def test_an_alias_is_mapped_to_its_canonical_genre() -> None:
    """Uploaders write the same genre several ways; the inconsistency is what
    makes a library hard to browse."""
    assert _tidy("alt rock") == ("alternative rock",)
    assert _tidy("dnb") == ("drum and bass",)


def test_casing_is_canonicalised() -> None:
    """The vocabulary is MusicBrainz's, which is lowercase by convention, so
    that is what lands in the file."""
    assert _tidy("Hard Rock") == ("hard rock",)


def test_duplicates_that_differ_only_in_casing_collapse() -> None:
    assert _tidy("Rock", "ROCK", "rock") == ("rock",)


def test_values_outside_the_vocabulary_are_dropped() -> None:
    """Years and rip notes end up in genre tags constantly."""
    result = _tidy("Rock", "1982")

    assert result == ("rock",)


def test_the_genre_count_is_capped() -> None:
    result = _tidy(
        "Rock",
        "Metal",
        "Pop",
        "Jazz",
        "Blues",
        "Folk",
        settings=DownloadGenreSettings(enabled=True, maximum_count=2),
    )

    assert result is not None and len(result) == 2


def test_a_track_whose_genres_all_fail_keeps_what_it_had() -> None:
    """Stripping a track to no genres because its vocabulary is unusual is worse
    than leaving it alone."""
    assert _tidy("Zzzzz Not A Genre") is None


def test_genres_already_canonical_are_left_untouched() -> None:
    """No write, so no needless rewrite of a file that is already correct."""
    assert _tidy("rock") is None


def test_a_file_with_no_genres_is_left_alone() -> None:
    assert _tidy() is None


def test_tidying_is_skipped_when_the_setting_is_off() -> None:
    assert _tidy("alt rock", settings=DownloadGenreSettings()) is None


def test_tidying_is_skipped_without_a_normalizer() -> None:
    service = _bare_service(None)

    assert (
        service._tidy_genres(
            _genre_document("alt rock"), DownloadGenreSettings(enabled=True)
        )
        is None
    )


# --- identification, joined to the same write --------------------------------


class _Identification:
    """Stands in for the MusicBrainz round trip."""

    def __init__(self, proposal) -> None:  # noqa: ANN001
        self.proposal = proposal
        self.calls = 0

    async def propose(self, paths, settings):  # noqa: ANN001, ANN202
        self.calls += 1
        return self.proposal


class _Review:
    def __init__(self) -> None:
        self.recorded = []

    async def record(self, proposal) -> None:  # noqa: ANN001
        self.recorded.append(proposal)


def _proposal(status: str, paths=(), **overrides):  # noqa: ANN001, ANN003, ANN202
    from models.audio_metadata import DesiredAudioField
    from services.native.post_import_identification_service import (
        IdentificationProposal,
    )

    return IdentificationProposal(
        status=status,
        score=overrides.pop("score", 0.9),
        album_title="The Colour and the Shape",
        paths=tuple(str(value) for value in paths),
        fields_by_path={
            str(path): (
                DesiredAudioField(
                    name="album", action="set", value="The Colour and the Shape"
                ),
                DesiredAudioField(name="title", action="set", value="Doll"),
            )
            for path in paths
        }
        if status == "applied"
        else {},
        **overrides,
    )


def _tagging_service(identification, review=None, settings=None):  # noqa: ANN001, ANN202
    from infrastructure.audio.metadata_engine import AudioMetadataEngine

    return PostImportEnrichmentService(
        lambda: settings
        or DownloadEnrichmentSettings(
            tagging=DownloadTaggingSettings(enabled=True)
        ),
        SimpleNamespace(get_exact_lyrics=None),
        AudioMetadataEngine(),
        None,
        None,
        None,
        None,
        None,
        identification,
        review,
    )


@pytest.mark.asyncio
async def test_an_uncertain_match_is_flagged_and_writes_nothing(
    tmp_path: Path,
) -> None:
    track = tmp_path / "01.flac"
    track.write_bytes(b"not really audio")
    review = _Review()
    proposal = _proposal("review", score=0.62)
    service = _tagging_service(_Identification(proposal), review)

    await service.enrich([str(track)])

    assert review.recorded == [proposal]
    # The file is untouched: a review is a question, not a pending change.
    assert track.read_bytes() == b"not really audio"


@pytest.mark.asyncio
async def test_a_confident_match_is_not_flagged(tmp_path: Path) -> None:
    track = tmp_path / "01.flac"
    track.write_bytes(b"not really audio")
    review = _Review()
    service = _tagging_service(_Identification(_proposal("applied", [track])), review)

    await service.enrich([str(track)])

    assert review.recorded == []


@pytest.mark.asyncio
async def test_a_failing_identification_does_not_fail_the_import(
    tmp_path: Path,
) -> None:
    track = tmp_path / "01.flac"
    track.write_bytes(b"not really audio")

    class _Broken:
        async def propose(self, paths, settings):  # noqa: ANN001, ANN202
            raise RuntimeError("musicbrainz is down")

    await _tagging_service(_Broken()).enrich([str(track)])


@pytest.mark.asyncio
async def test_the_corrected_title_is_what_lyrics_are_looked_up_by(
    tmp_path: Path,
) -> None:
    """LRCLIB matches on exact strings, so an uploader's "everlong (album
    version)" misses where the release's own "Everlong" hits."""
    track = tmp_path / "01.flac"
    track.write_bytes(b"not really audio")
    asked: list[dict] = []

    async def _get_exact_lyrics(**kwargs):  # noqa: ANN003, ANN202
        asked.append(kwargs)
        return LyricsLookupResult(found=False, candidate=None)

    service = PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(
            lyrics=DownloadLyricsSettings(enabled=True),
            tagging=DownloadTaggingSettings(enabled=True),
        ),
        SimpleNamespace(get_exact_lyrics=_get_exact_lyrics),
        SimpleNamespace(read=lambda path: _document()),
        None,
        None,
        None,
        None,
        None,
        _Identification(_proposal("applied", [track])),
        None,
    )
    service._audio = SimpleNamespace(read=lambda path: _document())
    module.legacy_audio_projection = lambda document: (TAG, INFO)  # noqa: ARG005

    try:
        await service.enrich([str(track)])
    finally:
        from infrastructure.audio.metadata_engine import legacy_audio_projection

        module.legacy_audio_projection = legacy_audio_projection

    assert asked and asked[0]["track_name"] == "Doll"
    assert asked[0]["album_name"] == "The Colour and the Shape"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "The audio writer fsyncs the staged file, which fails with EBADF on "
        "Windows. The write path itself is platform-independent; only this "
        "harness cannot exercise it."
    ),
)
@pytest.mark.asyncio
async def test_an_accepted_match_really_rewrites_the_file(tmp_path: Path) -> None:
    """End to end against a real FLAC: the corrected tags have to survive the
    plan, the staged copy and the swap, not merely be proposed."""
    import shutil as _shutil

    import mutagen

    from infrastructure.audio.metadata_engine import AudioMetadataEngine

    fixture = Path(__file__).parents[2] / "fixtures" / "library" / "flac_full_01.flac"
    track = tmp_path / "01 doll.flac"
    _shutil.copy2(fixture, track)
    original = mutagen.File(track)
    original["album"] = ["colour+shape 1997 XYZ"]
    original["title"] = ["01 doll"]
    original.save()

    service = _tagging_service(_Identification(_proposal("applied", [track])))
    service._audio = AudioMetadataEngine()

    await service.enrich([str(track)])

    tags = mutagen.File(track).tags
    assert tags["album"] == ["The Colour and the Shape"]
    assert tags["title"] == ["Doll"]
    assert list(tmp_path.glob(".songseek-enrich*")) == []


def test_every_proposed_tag_is_one_the_writer_will_actually_accept(
    tmp_path: Path,
) -> None:
    """The plan stage, which is where a bad field name or value shape shows up.

    `_write_tags_blocking` logs a blocked plan and returns, so a field the
    engine does not accept would leave identification silently doing nothing -
    with every unit test still green. This runs the real planner over a real
    file, and needs no fsync, so it runs on every platform.
    """
    import shutil as _shutil

    import mutagen

    from infrastructure.audio.metadata_engine import AudioMetadataEngine
    from models.audio_metadata import (
        AudioWritePolicy,
        DesiredAudioDocument,
        DesiredAudioField,
    )

    fixture = Path(__file__).parents[2] / "fixtures" / "library" / "flac_full_01.flac"
    track = tmp_path / "01 doll.flac"
    _shutil.copy2(fixture, track)
    tags = mutagen.File(track)
    tags["album"] = ["colour+shape 1997 XYZ"]
    tags["title"] = ["01 doll"]
    tags.save()

    engine = AudioMetadataEngine()
    proposed = (
        DesiredAudioField(name="album", action="set", value="The Colour and the Shape"),
        DesiredAudioField(name="album_artist", action="set", value=("Foo Fighters",)),
        DesiredAudioField(name="title", action="set", value="Doll"),
        DesiredAudioField(name="track_number", action="set", value=1),
        DesiredAudioField(name="disc_number", action="set", value=1),
        DesiredAudioField(name="musicbrainz_release_id", action="set", value="rel-1"),
        DesiredAudioField(
            name="musicbrainz_release_group_id", action="set", value="rg-1"
        ),
        DesiredAudioField(
            name="musicbrainz_album_artist_id", action="set", value=("a-1",)
        ),
        DesiredAudioField(name="musicbrainz_recording_id", action="set", value="rec-1"),
        DesiredAudioField(
            name="musicbrainz_release_track_id", action="set", value="rt-1"
        ),
    )

    plan = engine.plan(
        engine.read(track),
        DesiredAudioDocument(fields=proposed),
        AudioWritePolicy(),
    )

    assert list(plan.blockers) == []
    assert plan.requires_write
    assert {mutation.name for mutation in plan.mutations} == {
        field.name for field in proposed
    }


# --- artwork, filled only when it is missing ----------------------------------


class _Projection:
    """Stands in for Cover Art Archive."""

    def __init__(self, embedded=(), external=()) -> None:  # noqa: ANN001
        self.embedded = embedded
        self.external = external
        self.calls: list[dict] = []

    async def inspect_existing_external(self, settings, directory):  # noqa: ANN001, ANN201
        return ()

    async def project(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        return SimpleNamespace(embedded=self.embedded, external=self.external)


def _output(image_type: str = "front", *, content: bytes = b"jpeg-bytes"):  # noqa: ANN202
    return SimpleNamespace(
        image_type=image_type,
        mime_type="image/jpeg",
        format="jpeg",
        description="",
        width=1000,
        height=1000,
        byte_size=len(content),
        sha256="abc",
        content=content,
    )


def _artwork_service(projection, settings=None, artwork_document=None):  # noqa: ANN001, ANN202
    from api.v1.schemas.settings import DownloadArtworkSettings

    return PostImportEnrichmentService(
        lambda: DownloadEnrichmentSettings(
            artwork=settings or DownloadArtworkSettings(enabled=True)
        ),
        SimpleNamespace(get_exact_lyrics=None),
        SimpleNamespace(read=lambda path: artwork_document or _document()),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        projection,
    )


def _album_document(release_id=None, group_id=None, artwork=()):  # noqa: ANN001, ANN202
    """A read document plus the tag projection that goes with it."""
    tag = AudioTag(
        title="Doll",
        artist="Foo Fighters",
        album="The Colour and the Shape",
        track_number=1,
        musicbrainz_release_id=release_id,
        musicbrainz_release_group_id=group_id,
    )
    document = SimpleNamespace(
        probe=SimpleNamespace(detected_format="flac"), artwork=artwork
    )
    return document, tag


async def _enrich_with(service, tag, paths):  # noqa: ANN001, ANN202
    """Runs enrich with the tag projection stubbed, and always puts it back."""
    module.legacy_audio_projection = lambda document: (tag, INFO)  # noqa: ARG005
    try:
        await service.enrich(paths)
    finally:
        from infrastructure.audio.metadata_engine import legacy_audio_projection

        module.legacy_audio_projection = legacy_audio_projection


def _track(directory: Path, name: str = "01.flac") -> Path:
    path = directory / name
    path.write_bytes(b"not really audio")
    return path


@pytest.mark.asyncio
async def test_no_cover_is_fetched_when_the_setting_is_off(tmp_path: Path) -> None:
    from api.v1.schemas.settings import DownloadArtworkSettings

    projection = _Projection()
    document, tag = _album_document(release_id="rel-1")
    service = _artwork_service(
        projection, DownloadArtworkSettings(enabled=False), document
    )

    await _enrich_with(service, tag, [str(_track(tmp_path))])

    assert projection.calls == []


@pytest.mark.asyncio
async def test_no_cover_is_fetched_without_a_release_to_look_it_up_by(
    tmp_path: Path,
) -> None:
    """Cover Art Archive is addressed by MBID. With none on the file and none
    from identification there is nothing to ask for."""
    projection = _Projection()
    document, tag = _album_document()
    service = _artwork_service(projection, artwork_document=document)

    await _enrich_with(service, tag, [str(_track(tmp_path))])

    assert projection.calls == []


@pytest.mark.asyncio
async def test_the_release_on_the_file_is_what_the_cover_is_looked_up_by(
    tmp_path: Path,
) -> None:
    projection = _Projection()
    document, tag = _album_document(release_id="rel-1", group_id="rg-1")
    service = _artwork_service(projection, artwork_document=document)

    await _enrich_with(service, tag, [str(_track(tmp_path))])

    assert len(projection.calls) == 1
    assert projection.calls[0]["release_mbid"] == "rel-1"
    assert projection.calls[0]["release_group_mbid"] == "rg-1"


@pytest.mark.asyncio
async def test_one_cover_lookup_per_album_not_one_per_track(tmp_path: Path) -> None:
    projection = _Projection()
    document, tag = _album_document(release_id="rel-1")
    service = _artwork_service(projection, artwork_document=document)
    tracks = [str(_track(tmp_path, f"{index:02d}.flac")) for index in range(1, 4)]

    await _enrich_with(service, tag, tracks)

    assert len(projection.calls) == 1


@pytest.mark.asyncio
async def test_a_cover_file_is_written_beside_the_album(tmp_path: Path) -> None:
    projection = _Projection(external=(_output(content=b"the-cover"),))
    document, tag = _album_document(release_id="rel-1")
    service = _artwork_service(projection, artwork_document=document)

    await _enrich_with(service, tag, [str(_track(tmp_path))])

    assert (tmp_path / "cover.jpg").read_bytes() == b"the-cover"


@pytest.mark.asyncio
async def test_an_existing_cover_file_is_never_overwritten(tmp_path: Path) -> None:
    projection = _Projection(external=(_output(content=b"the-cover"),))
    document, tag = _album_document(release_id="rel-1")
    service = _artwork_service(projection, artwork_document=document)
    (tmp_path / "cover.jpg").write_bytes(b"the-one-it-came-with")

    await _enrich_with(service, tag, [str(_track(tmp_path))])

    assert (tmp_path / "cover.jpg").read_bytes() == b"the-one-it-came-with"


@pytest.mark.asyncio
async def test_a_failing_cover_lookup_does_not_fail_the_import(
    tmp_path: Path,
) -> None:
    class _Broken:
        async def inspect_existing_external(self, settings, directory):  # noqa: ANN001, ANN201
            return ()

        async def project(self, **kwargs):  # noqa: ANN003, ANN201
            raise RuntimeError("cover art archive is down")

    document, tag = _album_document(release_id="rel-1")

    await _enrich_with(
        _artwork_service(_Broken(), artwork_document=document),
        tag,
        [str(_track(tmp_path))],
    )


def test_a_track_that_already_has_a_cover_keeps_it() -> None:
    """The one it came with is usually ripped from the disc; a Cover Art Archive
    scan of another pressing would be a downgrade dressed as an improvement."""
    current = SimpleNamespace(artwork=(_output("front"),))

    assert PostImportEnrichmentService._desired_artwork(current, _output()) is None


def test_a_track_with_no_cover_gets_the_one_that_was_fetched() -> None:
    current = SimpleNamespace(artwork=())

    desired = PostImportEnrichmentService._desired_artwork(current, _output())

    assert desired is not None
    assert [value.image_type for value in desired] == ["front"]
    assert desired[0].content == b"jpeg-bytes"


def test_a_track_with_only_a_back_cover_still_gets_a_front_one() -> None:
    current = SimpleNamespace(artwork=(_output("back"),))

    desired = PostImportEnrichmentService._desired_artwork(current, _output())

    assert desired is not None
    # The back image survives: the desired set is the complete one, so dropping
    # it here would delete artwork the file already had.
    assert sorted(value.image_type for value in desired) == ["back", "front"]


def test_no_artwork_is_desired_when_none_was_fetched() -> None:
    """None and () mean different things to the writer: None leaves what is
    there, () would clear it."""
    current = SimpleNamespace(artwork=())

    assert PostImportEnrichmentService._desired_artwork(current, None) is None
