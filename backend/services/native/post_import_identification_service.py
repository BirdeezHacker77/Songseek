"""Match a finished download against MusicBrainz and propose corrected tags.

A download is named however the uploader felt like naming it. Two copies of the
same album arrive as "Foo Fighters - The Colour And The Shape" and "foo
fighters-colour+shape-1997-XYZ", and a media server browses them as two
different records. Matching the release against MusicBrainz and writing its own
spelling back is what collapses them into one.

The risk runs the other way: writing the *wrong* release's tags is worse than
writing nothing, because the file no longer says what it actually is. So the
proposal is gated on the evidence engine's score - the same scorer the
identification pipeline uses - and only a match at or above the auto-accept
score is applied. Between the two scores the download imports untouched and is
flagged, so a person decides. Below the review score nothing happens at all: a
poor match is noise, not a question worth asking.

Nothing here can fail an import. Identification is a network call to a service
that may be slow, rate limited or down, and the music is already in the library
either way.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import msgspec

from api.v1.schemas.settings import DownloadTaggingSettings
from infrastructure.audio.metadata_engine import (
    AudioMetadataEngine,
    legacy_audio_projection,
)
from models.audio_metadata import DesiredAudioField
from models.identification import CandidateEvidence, GroupingTrack
from services.native.album_candidate_service import AlbumCandidateService
from services.native.album_evidence_engine import AlbumEvidenceEngine

logger = logging.getLogger(__name__)

ProposalStatus = Literal["applied", "review", "unmatched"]


class IdentificationProposal(msgspec.Struct, frozen=True, kw_only=True):
    """What identification decided about one publication.

    `fields_by_path` is only populated for an applied proposal - a review is a
    question, and carrying a half-approved set of tag writes around invites
    something downstream applying them by accident.
    """

    status: ProposalStatus
    score: float = 0.0
    reason_code: str = ""
    release_mbid: str | None = None
    release_group_mbid: str | None = None
    album_title: str = ""
    album_artist_name: str = ""
    local_album_title: str = ""
    local_album_artist_name: str = ""
    paths: tuple[str, ...] = ()
    fields_by_path: dict[str, tuple[DesiredAudioField, ...]] = {}


_UNMATCHED = IdentificationProposal(status="unmatched")


class PostImportIdentificationService:
    def __init__(
        self,
        audio: AudioMetadataEngine,
        candidates: AlbumCandidateService,
        engine: AlbumEvidenceEngine,
    ) -> None:
        self._audio = audio
        self._candidates = candidates
        self._engine = engine

    async def propose(
        self, paths: Sequence[Path], settings: DownloadTaggingSettings
    ) -> IdentificationProposal:
        if not settings.enabled or not paths:
            return _UNMATCHED
        tracks = await self._local_tracks(paths)
        if not tracks:
            return _UNMATCHED
        # Without an album and an artist there is nothing to search on, and the
        # recall step would fall back to per-track recording searches that cost
        # a request each for a download that is unlikely to match anyway.
        if not any(track.album_title for track in tracks) or not any(
            track.album_artist_name or track.artist_name for track in tracks
        ):
            return IdentificationProposal(status="unmatched", reason_code="NO_TAGS")

        try:
            # One recall per publication, not per track: this is an album's worth
            # of files, and MusicBrainz is rate limited to a request a second.
            candidates = await self._candidates.recall(tracks, explicit=True)
            decision = self._engine.decide(tracks, candidates)
        except Exception:  # noqa: BLE001 - the music is imported either way
            logger.warning("post_import_identification.failed", exc_info=True)
            return _UNMATCHED

        best = self._best(decision.candidates)
        if best is None:
            return IdentificationProposal(
                status="unmatched", reason_code=decision.reason_code
            )

        confidence = self.confidence(best)
        local_title = best.local_album_title or tracks[0].album_title
        local_artist = best.local_album_artist_name or tracks[0].album_artist_name
        common = dict(
            score=confidence,
            reason_code=decision.reason_code,
            release_mbid=best.release_mbid,
            release_group_mbid=best.release_group_mbid,
            album_title=best.album_title,
            album_artist_name=best.album_artist_name,
            local_album_title=local_title,
            local_album_artist_name=local_artist,
            paths=tuple(str(value) for value in paths),
        )

        # Both gates, not either. The outcome is the engine's hard judgement -
        # "identified" already means no track contradicted the release and no
        # second release was nearly as good - and the confidence is how much of
        # it lined up. An album with one track that is plainly a different song
        # still scores well and must never be written automatically.
        applied = (
            decision.outcome == "identified"
            and confidence >= settings.auto_accept_score
        )
        if applied:
            return IdentificationProposal(
                status="applied",
                fields_by_path=self._fields(best, settings),
                **common,
            )
        if confidence >= settings.review_score:
            return IdentificationProposal(status="review", **common)
        return IdentificationProposal(status="unmatched", **common)

    @staticmethod
    def confidence(evidence: CandidateEvidence) -> float:
        """How much of this release lined up, as a fraction of what was checked.

        The evidence engine's own `score` is an assignment cost, and it sits at
        1.0 for an album with a track that is plainly a different song - useful
        for ranking two candidates against each other, useless as "how sure are
        we". This counts the checks instead: every track, plus the album title
        and the album artist. It is the number the thresholds are set against,
        so it has to mean something a person can read.
        """
        supported = sum(
            1 for value in evidence.track_evidence if value.classification == "supported"
        )
        # The release may have tracks the download does not, and the download may
        # have tracks the release does not. Both count against it, so the
        # denominator is whichever side has more.
        expected = supported + len(evidence.unmatched_expected_tracks)
        checks = max(len(evidence.track_evidence), expected) + 2
        matched = (
            supported
            + (1 if evidence.album_title_classification == "supported" else 0)
            + (1 if evidence.album_artist_classification == "supported" else 0)
        )
        return matched / checks if checks else 0.0

    async def propose_release(
        self,
        paths: Sequence[Path],
        settings: DownloadTaggingSettings,
        *,
        release_mbid: str | None,
        release_group_mbid: str | None,
    ) -> IdentificationProposal:
        """The tags for a release a person has chosen, whatever the score.

        No gate here on purpose. The score exists to decide what to do without
        asking; once somebody has been asked and answered, their answer is the
        decision, and a 0.55 they confirmed is worth more than a 0.95 they never
        saw.
        """
        tracks = await self._local_tracks(paths)
        if not tracks:
            return _UNMATCHED
        candidates = await self._candidates.recall(
            tracks, explicit=True, exact_release_mbid=release_mbid
        )
        if release_mbid is None and release_group_mbid is not None:
            # Fall back to the release group when the flagged match never carried
            # a specific release - accept only a candidate from that same group,
            # never whatever recall happens to rank first now.
            candidates = [
                value
                for value in candidates
                if value.release_group_mbid == release_group_mbid
            ]
        if not candidates:
            return IdentificationProposal(
                status="unmatched", reason_code="RELEASE_UNAVAILABLE"
            )
        evidence = self._engine.evaluate_candidate(tracks, candidates[0])
        return IdentificationProposal(
            status="applied",
            score=self.confidence(evidence),
            reason_code=evidence.reason_code,
            release_mbid=evidence.release_mbid,
            release_group_mbid=evidence.release_group_mbid,
            album_title=evidence.album_title,
            album_artist_name=evidence.album_artist_name,
            paths=tuple(str(value) for value in paths),
            fields_by_path=self._fields(evidence, settings),
        )

    # --- reading --------------------------------------------------------------

    async def _local_tracks(self, paths: Sequence[Path]) -> list[GroupingTrack]:
        tracks: list[GroupingTrack] = []
        for path in paths:
            try:
                document = await asyncio.to_thread(self._audio.read, path)
            except Exception:  # noqa: BLE001 - one unreadable file is not a failure
                logger.info(
                    "post_import_identification.unreadable", extra={"path": str(path)}
                )
                continue
            tag, info = legacy_audio_projection(document)
            tracks.append(
                GroupingTrack(
                    # The path is the identity here. There is no library row yet -
                    # this runs on files that have just been published - and the
                    # evidence engine only needs the ids to be stable and unique.
                    local_track_id=str(path),
                    root_id="",
                    relative_path=path.name,
                    title=tag.title,
                    artist_name=tag.artist,
                    album_title=tag.album,
                    album_artist_name=tag.album_artist or tag.artist,
                    artist_sort_name=tag.artist_sort,
                    album_artist_sort_name=tag.album_artist_sort,
                    track_number=tag.track_number,
                    disc_number=tag.disc_number,
                    duration_seconds=info.duration_seconds,
                    recording_mbid=tag.musicbrainz_recording_id,
                    release_mbid=tag.musicbrainz_release_id,
                    release_group_mbid=tag.musicbrainz_release_group_id,
                    release_track_mbid=tag.musicbrainz_release_track_id,
                    is_compilation=tag.compilation,
                )
            )
        return tracks

    @classmethod
    def _best(
        cls, candidates: Sequence[CandidateEvidence]
    ) -> CandidateEvidence | None:
        """The strongest evidence, whatever the outcome.

        Read even for a rejected decision on purpose: an ambiguous or
        contradictory result still says how much lined up, and that is what
        decides whether the download is worth showing somebody.
        """
        supported = [value for value in candidates if value.reason_code == "SUPPORTED"]
        pool = supported or list(candidates)
        if not pool:
            return None
        return max(pool, key=cls.confidence)

    # --- proposed tags --------------------------------------------------------

    def _fields(
        self, evidence: CandidateEvidence, settings: DownloadTaggingSettings
    ) -> dict[str, tuple[DesiredAudioField, ...]]:
        album = self._album_fields(evidence, settings)
        by_path: dict[str, tuple[DesiredAudioField, ...]] = {}
        for track in evidence.track_evidence:
            fields = list(album)
            if settings.write_identifiers:
                for name, value in (
                    ("musicbrainz_recording_id", track.recording_mbid),
                    ("musicbrainz_release_track_id", track.release_track_mbid),
                ):
                    if value:
                        fields.append(
                            DesiredAudioField(name=name, action="set", value=value)
                        )
            if settings.rewrite_titles and track.candidate_track_title:
                fields.append(
                    DesiredAudioField(
                        name="title", action="set", value=track.candidate_track_title
                    )
                )
            # Position comes from the release rather than the filename, which is
            # what fixes a download whose tracks are numbered by the uploader's
            # own ordering - or not numbered at all. Gated with the titles:
            # somebody who only wants identifiers written has said they want the
            # human-readable tags left alone, and a position is one of those.
            if settings.rewrite_titles:
                for name, value in (
                    ("track_number", track.candidate_track_position),
                    ("disc_number", track.candidate_disc_number),
                ):
                    if value:
                        fields.append(
                            DesiredAudioField(name=name, action="set", value=value)
                        )
            if fields:
                by_path[track.local_track_id] = tuple(fields)
        return by_path

    @staticmethod
    def _album_fields(
        evidence: CandidateEvidence, settings: DownloadTaggingSettings
    ) -> tuple[DesiredAudioField, ...]:
        fields: list[DesiredAudioField] = []
        if settings.rewrite_titles:
            if evidence.album_title:
                fields.append(
                    DesiredAudioField(
                        name="album", action="set", value=evidence.album_title
                    )
                )
            if evidence.album_artist_name:
                fields.append(
                    DesiredAudioField(
                        name="album_artist",
                        action="set",
                        value=(evidence.album_artist_name,),
                    )
                )
        if settings.write_identifiers:
            for name, value in (
                ("musicbrainz_release_group_id", evidence.release_group_mbid),
                ("musicbrainz_release_id", evidence.release_mbid),
            ):
                if value:
                    fields.append(
                        DesiredAudioField(name=name, action="set", value=value)
                    )
            if evidence.artist_mbid:
                fields.append(
                    DesiredAudioField(
                        name="musicbrainz_album_artist_id",
                        action="set",
                        value=(evidence.artist_mbid,),
                    )
                )
        return tuple(fields)
