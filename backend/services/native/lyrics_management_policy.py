"""Shared lyrics write policy for previews and automatic imports."""

from __future__ import annotations

from collections.abc import Mapping

from api.v1.schemas.library_management import LyricsManagementSettings
from models.library_management_enrichment import LyricsProjection

LyricsOutput = tuple[str, str | None]


def _has_text(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list)):
        return any(isinstance(item, str) and bool(item.strip()) for item in value)
    return False


def selected_lyrics_outputs(
    settings: LyricsManagementSettings,
    projection: LyricsProjection,
    *,
    synchronized_supported: bool = True,
) -> tuple[LyricsOutput, ...]:
    outputs: list[LyricsOutput] = []
    if settings.write_plain:
        outputs.append(("lyrics_plain", projection.plain_lyrics))
    if settings.write_synced and (synchronized_supported or not settings.write_plain):
        outputs.append(("lyrics_synced", projection.synced_lyrics))
    return tuple(outputs)


def required_lyrics_outputs_available(
    settings: LyricsManagementSettings,
    projection: LyricsProjection,
    existing: Mapping[str, object],
    *,
    synchronized_supported: bool = True,
) -> bool:
    outputs = selected_lyrics_outputs(
        settings, projection, synchronized_supported=synchronized_supported
    )
    return bool(outputs) and any(
        (settings.preserve_existing and _has_text(existing.get(name)))
        or (projection.status == "available" and _has_text(value))
        for name, value in outputs
    )


def planned_lyrics_outputs(
    settings: LyricsManagementSettings,
    projection: LyricsProjection,
    existing: Mapping[str, object],
    *,
    synchronized_supported: bool = True,
) -> tuple[tuple[str, str], ...]:
    if projection.status != "available":
        return ()
    return tuple(
        (name, value)
        for name, value in selected_lyrics_outputs(
            settings, projection, synchronized_supported=synchronized_supported
        )
        if isinstance(value, str)
        and value
        and not (settings.preserve_existing and _has_text(existing.get(name)))
    )


def lyrics_sidecar_content(
    settings: LyricsManagementSettings,
    projection: LyricsProjection,
) -> str | None:
    """Text for a .lrc file beside the track, or None to write no sidecar.

    Deliberately NOT gated on `synchronized_supported`: that flag describes what
    the audio container can hold in its tags, and a sidecar is a separate file.
    A .lrc is therefore the only way synchronized lyrics survive alongside a
    format that cannot embed them, which is precisely when it is most useful.

    Synchronized text wins when it is available, because a timestamped .lrc
    degrades gracefully - players that cannot use the timings just show the
    lines - whereas plain text cannot be upgraded back into a synced one.
    """
    if not settings.write_sidecar or projection.status != "available":
        return None
    candidates = (
        (settings.write_synced, projection.synced_lyrics),
        (settings.write_plain, projection.plain_lyrics),
    )
    for selected, value in candidates:
        if selected and isinstance(value, str) and value.strip():
            return value if value.endswith("\n") else f"{value}\n"
    return None


def synchronized_lyrics_supported(
    audio_format: str | None, *, wav_tag_policy: str
) -> bool:
    if audio_format == "wav":
        return wav_tag_policy != "riff_info"
    return audio_format in {"flac", "mp3", "ogg", "opus", "wma"}
