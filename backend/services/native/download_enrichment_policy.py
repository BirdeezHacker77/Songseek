"""Translate plain download-enrichment settings into the shapes the import
pipeline already speaks.

Library Management profiles describe what an organization pass would do to the
library you already have, so changing one invalidates a root's activation and
demands a fresh dry run over every existing file. Enrichment on the way in has
no existing state to preview - the files are arriving now - so it is configured
as an ordinary setting instead, and mapped here onto the structs the planner and
writer consume. Keeping the translation in one place means the import path has
exactly one notion of "the lyrics settings", not two.
"""

from __future__ import annotations

from api.v1.schemas.library_management import (
    ArtworkManagementSettings,
    GenreManagementSettings,
    LyricsManagementSettings,
    ReplayGainManagementSettings,
)
from api.v1.schemas.settings import (
    DownloadArtworkSettings,
    DownloadEnrichmentSettings,
    DownloadGenreSettings,
    DownloadLyricsSettings,
    DownloadReplayGainSettings,
)


def import_lyrics_settings(
    settings: DownloadLyricsSettings,
) -> LyricsManagementSettings:
    return LyricsManagementSettings(
        enabled=settings.enabled,
        provider=settings.provider,
        # `prefer_synced` is about the sidecar too, so it must not gate embedding
        # on its own - embedding off means no tag writes of either form.
        write_plain=settings.embed_in_tags,
        write_synced=settings.embed_in_tags and settings.prefer_synced,
        write_sidecar=settings.write_lrc_file,
        # Never hold an import because a track has no lyrics. Instrumentals,
        # interludes and obscure releases would park the queue indefinitely, and
        # a missing lyric is not a reason to refuse music.
        required=False,
        # Fill only what is empty: a download that already carries lyrics keeps
        # the ones it came with rather than having them replaced.
        preserve_existing=True,
    )


def import_replaygain_settings(
    settings: DownloadReplayGainSettings,
) -> ReplayGainManagementSettings:
    return ReplayGainManagementSettings(
        enabled=settings.enabled,
        # Never overwrite gains a release already carries; only supply missing ones.
        mode="fill_missing",
        album_aware=settings.album_aware,
        # Analysis failing is not a reason to refuse the music.
        required=False,
    )


def import_genre_settings(
    settings: DownloadGenreSettings,
) -> GenreManagementSettings:
    """The normalizer's own settings shape, filled from the plain ones.

    `sources` is empty on purpose: this tidies the genres already on the file
    and never reaches out to MusicBrainz, ListenBrainz or Last.fm. The
    normalizer reads only the vocabulary, alias and allow/deny rules below.
    """
    return GenreManagementSettings(
        enabled=settings.enabled,
        sources=[],
        mode="replace",
        canonicalize=settings.canonicalize,
        maximum_count=settings.maximum_count,
        denylist=list(settings.denylist),
    )


def import_artwork_settings(
    settings: DownloadArtworkSettings,
) -> ArtworkManagementSettings:
    """The projection service's own settings shape, filled from the plain ones.

    Everything here is set to fill rather than improve. `local_files` stays in
    the provider list so a cover already sitting beside the album is recognised
    as artwork that exists, rather than being ignored and then written over.
    """
    return ArtworkManagementSettings(
        embedded_enabled=settings.embed_in_tags,
        external_enabled=settings.save_cover_file,
        providers=[
            "cover_art_archive_release",
            "cover_art_archive_release_group",
            "local_files",
            "embedded",
        ],
        image_types=["front"],
        embedded_front_only=True,
        external_front_only=True,
        minimum_width=settings.minimum_width,
        minimum_height=settings.minimum_width,
        # Never touch a cover file that is already there.
        overwrite_external_files=False,
        never_replace_with_smaller=True,
    )


def import_enrichment(
    settings: DownloadEnrichmentSettings,
) -> tuple[LyricsManagementSettings, ReplayGainManagementSettings]:
    return (
        import_lyrics_settings(settings.lyrics),
        import_replaygain_settings(settings.replaygain),
    )
