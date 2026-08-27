from pathlib import Path
from types import SimpleNamespace

import pytest

from api.v1.schemas.settings import (
    DownloadEnrichmentSettings,
    DownloadLyricsSettings,
    DownloadRefreshSettings,
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


def _fields(candidate, gain, settings):  # noqa: ANN001, ANN202
    return {
        field.name: field.value
        for field in PostImportEnrichmentService._desired_fields(
            _document(), candidate, gain, settings
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
        for field in PostImportEnrichmentService._desired_fields(
            _document("m4a"),
            _candidate(),
            None,
            DownloadLyricsSettings(enabled=True, embed_in_tags=True),
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
