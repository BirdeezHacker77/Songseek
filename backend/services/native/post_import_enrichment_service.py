"""Enrichment for downloads that Library Management never touches.

Library Management enriches the files it imports, but it only runs when a root
assignment is enabled with an automatic trigger. With it off - which is the
ordinary case for someone who wants lyrics and loudness and nothing else - a
download is published unmanaged and no enrichment happens at all, however the
settings are configured.

This runs after such a publication, from the track's own tags, so enrichment does
not depend on profiles, activation or dry runs. It is invoked only for unmanaged
publications; a managed import has already enriched itself and must not be done
twice.

A failure here must never fail an import that has already succeeded - the music
is in the library either way - so everything is caught and logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from api.v1.schemas.settings import (
    DownloadEnrichmentSettings,
    DownloadLyricsSettings,
    DownloadReplayGainSettings,
)
from infrastructure.audio.lyrics import normalize_lrc
from infrastructure.audio.metadata_engine import (
    AudioMetadataEngine,
    legacy_audio_projection,
)
from models.audio_metadata import (
    AudioWritePolicy,
    DesiredAudioDocument,
    DesiredAudioField,
)
from models.library_management_enrichment import LyricsCandidate
from repositories.protocols.lrclib import LrclibRepositoryProtocol
from services.native.lyrics_management_policy import synchronized_lyrics_supported
from services.native.replaygain_analysis_service import ReplayGainAnalysisService

logger = logging.getLogger(__name__)

# LRCLIB matches on whole seconds and tolerates a small drift; the managed path
# applies the same tolerance through its projection service.
_DURATION_TOLERANCE_SECONDS = 2.0
_WRITE_POLICY = AudioWritePolicy()


class PostImportEnrichmentService:
    def __init__(
        self,
        settings_getter: Callable[[], DownloadEnrichmentSettings],
        lrclib: LrclibRepositoryProtocol,
        audio: AudioMetadataEngine,
        replaygain: ReplayGainAnalysisService | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._lrclib = lrclib
        self._audio = audio
        self._replaygain = replaygain

    async def enrich(self, paths: Sequence[str]) -> None:
        try:
            settings = self._settings_getter()
        except Exception:  # noqa: BLE001 - never fail a completed import
            logger.warning("post_import_enrichment.settings_unavailable")
            return
        lyrics_settings, replaygain_settings = settings.lyrics, settings.replaygain
        if not lyrics_settings.enabled and not replaygain_settings.enabled:
            return

        tracks = [Path(value) for value in paths]
        candidates = (
            await self._lyrics_candidates(tracks, lyrics_settings)
            if lyrics_settings.enabled
            else {}
        )
        gains = (
            await self._gains(tracks, replaygain_settings)
            if replaygain_settings.enabled
            else {}
        )
        for track in tracks:
            try:
                await self._write_tags(
                    track,
                    candidate=candidates.get(track),
                    gain=gains.get(track),
                    lyrics_settings=lyrics_settings,
                )
            except Exception:  # noqa: BLE001 - one bad track must not stop the rest
                logger.warning(
                    "post_import_enrichment.tags_failed", extra={"path": str(track)}
                )

    # --- lyrics ---------------------------------------------------------------

    async def _lyrics_candidates(
        self, tracks: Sequence[Path], settings: DownloadLyricsSettings
    ) -> dict[Path, LyricsCandidate]:
        found: dict[Path, LyricsCandidate] = {}
        for track in tracks:
            try:
                candidate = await self._lookup(track, settings)
            except Exception:  # noqa: BLE001 - one bad track must not stop the rest
                logger.warning(
                    "post_import_enrichment.failed", extra={"path": str(track)}
                )
                continue
            if candidate is None:
                continue
            found[track] = candidate
            if settings.write_lrc_file:
                text = self._sidecar_text(candidate, settings)
                if text is not None:
                    await asyncio.to_thread(
                        track.with_suffix(".lrc").write_text,
                        text,
                        encoding="utf-8",
                        newline="\n",
                    )
                    logger.info(
                        "post_import_enrichment.lyrics_written",
                        extra={"path": str(track)},
                    )
        return found

    async def _lookup(
        self, path: Path, settings: DownloadLyricsSettings
    ) -> LyricsCandidate | None:
        # A download that shipped its own .lrc already has lyrics somebody chose;
        # leave the track alone rather than replacing them.
        if await asyncio.to_thread(path.with_suffix(".lrc").exists):
            return None
        document = await asyncio.to_thread(self._audio.read, path)
        tag, info = legacy_audio_projection(document)
        if not tag.title or not tag.artist:
            return None
        result = await self._lrclib.get_exact_lyrics(
            track_name=tag.title,
            artist_name=tag.artist,
            album_name=tag.album or "",
            duration_seconds=int(round(info.duration_seconds)),
        )
        candidate = result.candidate if result.found else None
        if candidate is None or candidate.instrumental:
            return None
        if abs(candidate.duration_seconds - info.duration_seconds) > (
            _DURATION_TOLERANCE_SECONDS
        ):
            # A different recording of the same title; better no lyrics than
            # lyrics that drift out of time against the actual audio.
            return None
        return candidate

    @staticmethod
    def _sidecar_text(
        candidate: LyricsCandidate, settings: DownloadLyricsSettings
    ) -> str | None:
        synced = normalize_lrc(candidate.synced_lyrics or "")
        plain = (candidate.plain_lyrics or "").strip() or None
        ordered = (synced, plain) if settings.prefer_synced else (plain, synced)
        for value in ordered:
            if value:
                return value if value.endswith("\n") else f"{value}\n"
        return None

    # --- loudness -------------------------------------------------------------

    async def _gains(
        self, tracks: Sequence[Path], settings: DownloadReplayGainSettings
    ) -> dict[Path, object]:
        if self._replaygain is None or not tracks:
            return {}
        try:
            analysis = await self._replaygain.analyze(
                [path.resolve() for path in tracks], album_aware=settings.album_aware
            )
        except Exception:  # noqa: BLE001 - loudness is optional, the music is not
            logger.warning("post_import_enrichment.replaygain_failed")
            return {}
        if analysis.status != "available":
            logger.info(
                "post_import_enrichment.replaygain_unavailable",
                extra={"reason": analysis.reason},
            )
            return {}
        by_path = {Path(value.source_path): value for value in analysis.tracks}
        return {
            track: by_path[key]
            for track in tracks
            if (key := track.resolve()) in by_path
        }

    # --- writing --------------------------------------------------------------

    async def _write_tags(
        self,
        path: Path,
        *,
        candidate: LyricsCandidate | None,
        gain: object | None,
        lyrics_settings: DownloadLyricsSettings,
    ) -> None:
        if candidate is None and gain is None:
            return
        if candidate is not None and not lyrics_settings.embed_in_tags:
            candidate = None
            if gain is None:
                return
        await asyncio.to_thread(
            self._write_tags_blocking, path, candidate, gain, lyrics_settings
        )

    def _write_tags_blocking(
        self,
        path: Path,
        candidate: LyricsCandidate | None,
        gain: object | None,
        lyrics_settings: DownloadLyricsSettings,
    ) -> None:
        # Written to a copy beside the track and swapped in, so a failure midway
        # leaves the imported file exactly as it was. copy2 carries the mode and
        # timestamps across, matching what the managed writer preserves.
        staged = path.with_name(f"{path.name}.songseek-enrich")
        try:
            shutil.copy2(path, staged)
            current = self._audio.read(staged)
            fields = self._desired_fields(current, candidate, gain, lyrics_settings)
            if not fields:
                return
            plan = self._audio.plan(
                current, DesiredAudioDocument(fields=tuple(fields)), _WRITE_POLICY
            )
            if plan.blockers:
                logger.info(
                    "post_import_enrichment.write_blocked",
                    extra={"path": str(path), "blockers": list(plan.blockers)},
                )
                return
            if not plan.requires_write:
                return
            self._audio.apply(staged, plan)
            os.replace(staged, path)
            staged = None  # consumed by the swap
            logger.info(
                "post_import_enrichment.tags_written", extra={"path": str(path)}
            )
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    @staticmethod
    def _desired_fields(
        current,  # noqa: ANN001 - ReadAudioDocument
        candidate: LyricsCandidate | None,
        gain: object | None,
        lyrics_settings: DownloadLyricsSettings,
    ) -> list[DesiredAudioField]:
        fields: list[DesiredAudioField] = []
        if candidate is not None:
            plain = (candidate.plain_lyrics or "").strip() or None
            if plain:
                fields.append(
                    DesiredAudioField(name="lyrics_plain", action="set", value=plain)
                )
            synced = normalize_lrc(candidate.synced_lyrics or "")
            # Only where the container can actually represent it; the managed path
            # gates on exactly the same capability rather than letting the plan
            # come back blocked.
            if (
                synced
                and lyrics_settings.prefer_synced
                and synchronized_lyrics_supported(
                    current.probe.detected_format,
                    wav_tag_policy=_WRITE_POLICY.wav_tag_policy,
                )
            ):
                fields.append(
                    DesiredAudioField(name="lyrics_synced", action="set", value=synced)
                )
        if gain is not None:
            for name, value in (
                ("replaygain_track_gain", getattr(gain, "track_gain_db", None)),
                ("replaygain_track_peak", getattr(gain, "track_peak", None)),
                ("replaygain_album_gain", getattr(gain, "album_gain_db", None)),
                ("replaygain_album_peak", getattr(gain, "album_peak", None)),
            ):
                if value is not None:
                    fields.append(
                        DesiredAudioField(name=name, action="set", value=value)
                    )
        return fields
