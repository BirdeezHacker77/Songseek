from pathlib import Path
from types import SimpleNamespace

import pytest

from api.v1.schemas.settings import DownloadEnrichmentSettings, DownloadLyricsSettings
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
            lyrics=DownloadLyricsSettings(enabled=True, write_lrc_file=True)
        ),
        SimpleNamespace(get_exact_lyrics=get_exact_lyrics),
        SimpleNamespace(read=read),
    )

    await service.enrich([str(missing), str(good)])

    assert reads == [missing, good]
    assert good.with_suffix(".lrc").exists()
