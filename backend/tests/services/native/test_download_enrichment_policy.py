from api.v1.schemas.settings import (
    DownloadEnrichmentSettings,
    DownloadLyricsSettings,
    DownloadReplayGainSettings,
)
from services.native.download_enrichment_policy import (
    import_enrichment,
    import_lyrics_settings,
    import_replaygain_settings,
)
from services.native.lyrics_management_policy import lyrics_sidecar_content
from models.library_management_enrichment import LyricsProjection


def test_embedding_off_still_writes_a_sidecar() -> None:
    """The combination the simple settings exist to make possible: a .lrc beside
    the track and nothing written into the file itself."""
    settings = import_lyrics_settings(
        DownloadLyricsSettings(
            enabled=True,
            embed_in_tags=False,
            prefer_synced=True,
            write_lrc_file=True,
        )
    )

    assert settings.write_plain is False
    assert settings.write_synced is False
    assert settings.write_sidecar is True

    projection = LyricsProjection(
        status="available",
        plain_lyrics="Plain",
        synced_lyrics="[00:01.000]Synced",
    )
    assert (
        lyrics_sidecar_content(settings, projection, prefer_synced=True)
        == "[00:01.000]Synced\n"
    )


def test_embedding_on_without_synced_writes_only_plain_tags() -> None:
    settings = import_lyrics_settings(
        DownloadLyricsSettings(enabled=True, embed_in_tags=True, prefer_synced=False)
    )

    assert settings.write_plain is True
    assert settings.write_synced is False


def test_downloads_never_hold_on_missing_lyrics_or_gain() -> None:
    """Instrumentals and obscure releases would otherwise park the queue."""
    lyrics = import_lyrics_settings(DownloadLyricsSettings(enabled=True))
    replaygain = import_replaygain_settings(DownloadReplayGainSettings(enabled=True))

    assert lyrics.required is False
    assert replaygain.required is False


def test_existing_lyrics_and_gain_on_a_download_are_kept() -> None:
    lyrics = import_lyrics_settings(DownloadLyricsSettings(enabled=True))
    replaygain = import_replaygain_settings(DownloadReplayGainSettings(enabled=True))

    assert lyrics.preserve_existing is True
    assert replaygain.mode == "fill_missing"


def test_disabled_settings_translate_to_disabled_enrichment() -> None:
    lyrics, replaygain = import_enrichment(DownloadEnrichmentSettings())

    assert lyrics.enabled is False
    assert replaygain.enabled is False
