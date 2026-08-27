"""Enrichment for downloads that Library Management never touches.

Library Management enriches the files it imports, but it only runs when a root
assignment is enabled with an automatic trigger. With it off - which is the
ordinary case for someone who wants lyrics and nothing else - a download is
published unmanaged and no enrichment happens at all, however the settings are
configured.

This runs after such a publication, from the track's own tags, so lyrics do not
depend on any of the profile, activation and dry-run machinery.

Deliberately additive: it writes a .lrc beside the track and never modifies the
audio file. A failure here must not fail an import that has already succeeded -
the music is in the library either way - so everything is caught and logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from api.v1.schemas.settings import DownloadEnrichmentSettings, DownloadLyricsSettings
from infrastructure.audio.metadata_engine import (
    AudioMetadataEngine,
    legacy_audio_projection,
)
from infrastructure.audio.lyrics import normalize_lrc
from repositories.protocols.lrclib import LrclibRepositoryProtocol

logger = logging.getLogger(__name__)

# LRCLIB matches on a whole number of seconds and tolerates a small drift; the
# managed path applies the same tolerance through its projection service.
_DURATION_TOLERANCE_SECONDS = 2.0


class PostImportEnrichmentService:
    def __init__(
        self,
        settings_getter: Callable[[], DownloadEnrichmentSettings],
        lrclib: LrclibRepositoryProtocol,
        audio: AudioMetadataEngine,
    ) -> None:
        self._settings_getter = settings_getter
        self._lrclib = lrclib
        self._audio = audio

    async def enrich(self, paths: Sequence[str]) -> None:
        try:
            settings = self._settings_getter()
        except Exception:  # noqa: BLE001 - never fail a completed import
            logger.warning("post_import_enrichment.settings_unavailable")
            return
        lyrics = settings.lyrics
        if not lyrics.enabled or not lyrics.write_lrc_file:
            return
        for raw in paths:
            try:
                await self._write_lyrics_sidecar(Path(raw), lyrics)
            except Exception:  # noqa: BLE001 - one bad track must not stop the rest
                logger.warning(
                    "post_import_enrichment.failed", extra={"path": str(raw)}
                )

    async def _write_lyrics_sidecar(
        self, path: Path, settings: DownloadLyricsSettings
    ) -> None:
        sidecar = path.with_suffix(".lrc")
        # A download that shipped its own .lrc keeps it; re-fetching would
        # replace lyrics somebody deliberately supplied.
        if await asyncio.to_thread(sidecar.exists):
            return
        document = await asyncio.to_thread(self._audio.read, path)
        tag, info = legacy_audio_projection(document)
        if not tag.title or not tag.artist:
            return
        result = await self._lrclib.get_exact_lyrics(
            track_name=tag.title,
            artist_name=tag.artist,
            album_name=tag.album or "",
            duration_seconds=int(round(info.duration_seconds)),
        )
        candidate = result.candidate if result.found else None
        if candidate is None or candidate.instrumental:
            return
        if abs(candidate.duration_seconds - info.duration_seconds) > (
            _DURATION_TOLERANCE_SECONDS
        ):
            # A different recording of the same title; better no lyrics than
            # lyrics that drift out of time against the actual audio.
            return
        text = self._select(candidate, settings)
        if text is None:
            return
        await asyncio.to_thread(
            sidecar.write_text, text, encoding="utf-8", newline="\n"
        )
        logger.info("post_import_enrichment.lyrics_written", extra={"path": str(path)})

    @staticmethod
    def _select(candidate, settings: DownloadLyricsSettings) -> str | None:  # noqa: ANN001
        synced = normalize_lrc(candidate.synced_lyrics or "")
        plain = (candidate.plain_lyrics or "").strip() or None
        ordered = (synced, plain) if settings.prefer_synced else (plain, synced)
        for value in ordered:
            if value:
                return value if value.endswith("\n") else f"{value}\n"
        return None
