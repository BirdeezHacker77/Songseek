from api.v1.schemas.library_management import LyricsManagementSettings
from models.library_management_enrichment import LyricsProjection
from services.native.lyrics_management_policy import (
    lyrics_sidecar_content,
    planned_lyrics_outputs,
    required_lyrics_outputs_available,
)


def test_default_policy_replaces_existing_selected_lyrics() -> None:
    settings = LyricsManagementSettings(enabled=True)
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider lyrics",
        synced_lyrics="[00:01.000]Provider lyrics",
    )

    assert planned_lyrics_outputs(
        settings, projection, {"lyrics_plain": "Embedded lyrics"}
    ) == (
        ("lyrics_plain", "Provider lyrics"),
        ("lyrics_synced", "[00:01.000]Provider lyrics"),
    )


def test_preserve_policy_keeps_each_populated_output_independently() -> None:
    settings = LyricsManagementSettings(
        enabled=True,
        write_plain=True,
        write_synced=True,
        preserve_existing=True,
        required=True,
    )
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider plain lyrics",
        synced_lyrics="[00:01.000]Provider synchronized lyrics",
    )
    existing = {"lyrics_plain": "Embedded plain lyrics"}

    assert planned_lyrics_outputs(settings, projection, existing) == (
        ("lyrics_synced", "[00:01.000]Provider synchronized lyrics"),
    )
    assert required_lyrics_outputs_available(settings, projection, existing) is True


def test_preserved_existing_output_satisfies_required_provider_degradation() -> None:
    settings = LyricsManagementSettings(
        enabled=True,
        write_synced=False,
        preserve_existing=True,
        required=True,
    )
    projection = LyricsProjection(status="deferred")

    assert (
        required_lyrics_outputs_available(
            settings, projection, {"lyrics_plain": "Embedded lyrics"}
        )
        is True
    )
    assert (
        required_lyrics_outputs_available(settings, projection, {"lyrics_plain": "   "})
        is False
    )


def test_plain_only_provider_result_satisfies_required_lyrics() -> None:
    settings = LyricsManagementSettings(
        enabled=True,
        write_plain=True,
        write_synced=True,
        required=True,
    )
    projection = LyricsProjection(status="available", plain_lyrics="Provider lyrics")

    assert required_lyrics_outputs_available(settings, projection, {}) is True


def test_synchronized_only_provider_result_satisfies_required_lyrics() -> None:
    settings = LyricsManagementSettings(
        enabled=True,
        write_plain=True,
        write_synced=True,
        required=True,
    )
    projection = LyricsProjection(
        status="available",
        synced_lyrics="[00:01.000]Provider lyrics",
    )

    assert required_lyrics_outputs_available(settings, projection, {}) is True


def test_plain_output_is_the_fallback_when_synchronized_is_unsupported() -> None:
    settings = LyricsManagementSettings(enabled=True, required=True)
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider lyrics",
        synced_lyrics="[00:01.000]Provider lyrics",
    )

    assert planned_lyrics_outputs(
        settings,
        projection,
        {},
        synchronized_supported=False,
    ) == (("lyrics_plain", "Provider lyrics"),)
    assert (
        required_lyrics_outputs_available(
            settings,
            projection,
            {},
            synchronized_supported=False,
        )
        is True
    )


def test_synchronized_only_profile_keeps_unsupported_field_for_capability_gate() -> (
    None
):
    settings = LyricsManagementSettings(
        enabled=True,
        write_plain=False,
        write_synced=True,
    )
    projection = LyricsProjection(
        status="available",
        synced_lyrics="[00:01.000]Provider lyrics",
    )

    assert planned_lyrics_outputs(
        settings,
        projection,
        {},
        synchronized_supported=False,
    ) == (("lyrics_synced", "[00:01.000]Provider lyrics"),)


def test_sidecar_prefers_synchronized_text_and_terminates_the_final_line() -> None:
    settings = LyricsManagementSettings(enabled=True)
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider plain",
        synced_lyrics="[00:01.000]Provider synced",
    )

    assert (
        lyrics_sidecar_content(settings, projection) == "[00:01.000]Provider synced\n"
    )


def test_sidecar_falls_back_to_plain_text_when_no_synchronized_text_exists() -> None:
    settings = LyricsManagementSettings(enabled=True)
    projection = LyricsProjection(status="available", plain_lyrics="Provider plain")

    assert lyrics_sidecar_content(settings, projection) == "Provider plain\n"


def test_sidecar_carries_synchronized_text_a_container_could_not_embed() -> None:
    """A sidecar is a separate file, so container tag limits do not apply to it.

    This is the case that makes sidecars worth writing at all: a WAV under a
    riff_info policy cannot hold SYLT, and the .lrc is the only place the
    synchronized text can survive.
    """
    settings = LyricsManagementSettings(enabled=True)
    projection = LyricsProjection(
        status="available",
        synced_lyrics="[00:01.000]Provider synced",
    )

    assert (
        lyrics_sidecar_content(settings, projection) == "[00:01.000]Provider synced\n"
    )


def test_sidecar_is_skipped_when_disabled_or_unavailable() -> None:
    projection = LyricsProjection(
        status="available",
        synced_lyrics="[00:01.000]Provider synced",
    )

    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(enabled=True, write_sidecar=False), projection
        )
        is None
    )
    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(enabled=True),
            LyricsProjection(status="not_found"),
        )
        is None
    )


def test_sidecar_is_written_even_when_no_lyrics_are_embedded() -> None:
    """Wanting a .lrc and no embedded lyrics is an ordinary choice.

    The tag-output flags must not gate the sidecar, or that combination would
    silently produce no file at all.
    """
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider plain",
        synced_lyrics="[00:01.000]Provider synced",
    )

    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(
                enabled=True, write_synced=False, write_plain=False
            ),
            projection,
            prefer_synced=True,
        )
        == "[00:01.000]Provider synced\n"
    )


def test_sidecar_preference_follows_the_tag_choice_unless_overridden() -> None:
    projection = LyricsProjection(
        status="available",
        plain_lyrics="Provider plain",
        synced_lyrics="[00:01.000]Provider synced",
    )

    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(enabled=True, write_synced=False), projection
        )
        == "Provider plain\n"
    )
    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(enabled=True, write_synced=True),
            projection,
            prefer_synced=False,
        )
        == "Provider plain\n"
    )


def test_sidecar_falls_back_to_the_other_form_when_the_preferred_one_is_missing() -> (
    None
):
    """A caller who prefers plain still gets a synced .lrc rather than none."""
    assert (
        lyrics_sidecar_content(
            LyricsManagementSettings(enabled=True),
            LyricsProjection(
                status="available", synced_lyrics="[00:01.000]Provider synced"
            ),
            prefer_synced=False,
        )
        == "[00:01.000]Provider synced\n"
    )
