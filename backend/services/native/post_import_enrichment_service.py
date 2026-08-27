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
import time
import uuid
import msgspec
from collections.abc import Callable, Sequence
from pathlib import Path

from api.v1.schemas.settings import (
    DownloadEnrichmentSettings,
    DownloadGenreSettings,
    DownloadLyricsSettings,
    DownloadRefreshSettings,
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
from models.enrichment_history import EnrichmentHistoryEntry
from models.library_management_enrichment import LyricsCandidate
from repositories.protocols.lrclib import LrclibRepositoryProtocol
from models.library_management_genres import GenreCandidate
from services.native.download_enrichment_policy import import_genre_settings
from services.native.genre_normalizer import GenreNormalizer, fold_genre
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
        navidrome_getter: Callable[[], object] | None = None,
        jellyfin_getter: Callable[[], object] | None = None,
        history: object | None = None,
        genres: GenreNormalizer | None = None,
    ) -> None:
        self._settings_getter = settings_getter
        self._lrclib = lrclib
        self._audio = audio
        self._replaygain = replaygain
        self._navidrome_getter = navidrome_getter
        self._jellyfin_getter = jellyfin_getter
        self._history = history
        self._genres = genres

    async def enrich(self, paths: Sequence[str]) -> None:
        try:
            settings = self._settings_getter()
        except Exception:  # noqa: BLE001 - never fail a completed import
            logger.warning("post_import_enrichment.settings_unavailable")
            return
        lyrics_settings, replaygain_settings = settings.lyrics, settings.replaygain
        refresh_settings = settings.refresh
        if (
            not lyrics_settings.enabled
            and not replaygain_settings.enabled
            and not refresh_settings.enabled
        ):
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
                    genre_settings=settings.genres,
                )
            except Exception:  # noqa: BLE001 - one bad track must not stop the rest
                logger.warning(
                    "post_import_enrichment.tags_failed",
                    extra={"path": str(track)},
                    exc_info=True,
                )
        # Last, so the servers are told about finished files rather than ones
        # still being rewritten - and once per publication, not once per track.
        if refresh_settings.enabled and tracks:
            await self._refresh_media_servers(refresh_settings)

    # --- media servers --------------------------------------------------------

    async def _refresh_media_servers(self, settings: DownloadRefreshSettings) -> None:
        targets = (
            ("navidrome", settings.navidrome_enabled, self._navidrome_getter),
            ("jellyfin", settings.jellyfin_enabled, self._jellyfin_getter),
        )
        for name, enabled, getter in targets:
            if not enabled or getter is None:
                continue
            try:
                repository = getter()
                if not repository.is_configured():
                    logger.info(
                        "post_import_enrichment.refresh_skipped", extra={"target": name}
                    )
                    continue
                await self._start_scan(name, repository)
                logger.info(
                    "post_import_enrichment.refresh_requested", extra={"target": name}
                )
            except Exception:  # noqa: BLE001 - a refresh failing is not an import failing
                logger.warning(
                    "post_import_enrichment.refresh_failed", extra={"target": name}
                )

    @staticmethod
    async def _start_scan(name: str, repository) -> None:  # noqa: ANN001
        # Both are fire-and-forget on the server side: a request only means the
        # scan was accepted, so there is nothing useful to wait for.
        if name == "navidrome":
            await repository.start_scan()
        else:
            await repository.refresh_library()

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
                    "post_import_enrichment.failed",
                    extra={"path": str(track)},
                    exc_info=True,
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
        genre_settings: DownloadGenreSettings,
    ) -> None:
        if candidate is not None and not lyrics_settings.embed_in_tags:
            candidate = None
        tidy_genres = genre_settings.enabled and self._genres is not None
        if candidate is None and gain is None and not tidy_genres:
            return
        entry = await asyncio.to_thread(
            self._write_tags_blocking,
            path,
            candidate,
            gain,
            lyrics_settings,
            genre_settings,
        )
        # Recorded from the async side: the write itself runs in a worker thread,
        # and the store is async. Best-effort on purpose - the write already
        # succeeded, and losing the undo entry is worth less than raising over a
        # file that is now correct.
        if entry is not None and self._history is not None:
            try:
                await self._history.record(entry)
            except Exception:  # noqa: BLE001 - the write stands either way
                logger.warning(
                    "post_import_enrichment.history_not_recorded",
                    extra={"path": str(path)},
                    exc_info=True,
                )

    def _write_tags_blocking(
        self,
        path: Path,
        candidate: LyricsCandidate | None,
        gain: object | None,
        lyrics_settings: DownloadLyricsSettings,
        genre_settings: DownloadGenreSettings,
    ):  # noqa: ANN201
        # Written to a copy beside the track and swapped in, so a failure midway
        # leaves the imported file exactly as it was. copy2 carries the mode and
        # timestamps across, matching what the managed writer preserves.
        staged = self._staged_path(path)
        try:
            shutil.copy2(path, staged)
            current = self._audio.read(staged)
            fields = self._desired_fields(
                current, candidate, gain, lyrics_settings, genre_settings
            )
            if not fields:
                return None
            plan = self._audio.plan(
                current, DesiredAudioDocument(fields=tuple(fields)), _WRITE_POLICY
            )
            if plan.blockers:
                logger.info(
                    "post_import_enrichment.write_blocked",
                    extra={"path": str(path), "blockers": list(plan.blockers)},
                )
                return None
            if not plan.requires_write:
                return None
            # Captured from the untouched copy, immediately before the write, so
            # the recorded state is exactly what the file had. Recording after
            # the swap would snapshot the change instead of what preceded it.
            snapshot = self._audio.snapshot(staged)
            self._audio.apply(staged, plan)
            os.replace(staged, path)
            staged = None  # consumed by the swap
            logger.info(
                "post_import_enrichment.tags_written", extra={"path": str(path)}
            )
            return self._history_entry(path, plan, snapshot, candidate, gain)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    @staticmethod
    def _history_entry(path: Path, plan, snapshot, candidate, gain):  # noqa: ANN001, ANN205
        """What the file looked like before this write, ready to be restored."""
        kinds: list[str] = []
        if candidate is not None:
            kinds.append("lyrics")
        if gain is not None:
            kinds.append("replaygain")
        if any(value.name == "genre" for value in plan.mutations):
            kinds.append("genres")
        return EnrichmentHistoryEntry(
            id=str(uuid.uuid4()),
            file_path=str(path),
            kinds=tuple(kinds),
            changed_fields=tuple(sorted(value.name for value in plan.mutations)),
            snapshot_json=msgspec.json.encode(snapshot).decode(),
            created_at=time.time(),
        )

    def _tidy_genres(
        self,
        current,
        settings: DownloadGenreSettings,  # noqa: ANN001
    ) -> tuple[str, ...] | None:
        """Normalized genres for this file, or None to leave them alone.

        Works only from what the file already carries - no provider is consulted.
        Uploaders write "Hard Rock", "hard-rock" and "Rock; Metal; 1982" for the
        same record, and it is the inconsistency rather than the shortage that
        makes a library hard to browse.
        """
        if not settings.enabled or self._genres is None:
            return None
        existing = tuple(current.metadata.strings_for("genre"))
        if not existing:
            return None
        policy = import_genre_settings(settings)
        kept: list[str] = []
        seen: set[str] = set()
        for value in existing:
            normalized = self._genres.normalize(
                GenreCandidate(
                    display_name=value,
                    folded_name=fold_genre(value),
                    provider="existing_local",
                    provider_entity="audio_tag",
                ),
                policy,
                require_canonical_vocabulary=settings.known_genres_only,
            )
            if normalized is None or normalized.folded_name in seen:
                continue
            seen.add(normalized.folded_name)
            kept.append(normalized.display_name)
        result = tuple(kept[: max(1, settings.maximum_count)])
        # Nothing survived: leave the file's own genres rather than stripping a
        # track down to none because its vocabulary is unusual.
        if not result or result == existing:
            return None
        return result

    @staticmethod
    def _staged_path(path: Path) -> Path:
        """Where the rewritten copy lives before it is swapped in.

        The suffix has to survive: a write plan is refused outright when a
        file's extension does not match its detected container, so a staged name
        ending in anything else fails before a single tag is written.

        The leading dot keeps it out of the way of a media server scanning the
        album folder during the moment it exists.
        """
        return path.with_name(f".songseek-enrich.{path.name}")

    def _desired_fields(
        self,
        current,  # noqa: ANN001 - ReadAudioDocument
        candidate: LyricsCandidate | None,
        gain: object | None,
        lyrics_settings: DownloadLyricsSettings,
        genre_settings: DownloadGenreSettings,
    ) -> list[DesiredAudioField]:
        fields: list[DesiredAudioField] = []
        tidied = self._tidy_genres(current, genre_settings)
        if tidied is not None:
            fields.append(DesiredAudioField(name="genre", action="set", value=tidied))
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
