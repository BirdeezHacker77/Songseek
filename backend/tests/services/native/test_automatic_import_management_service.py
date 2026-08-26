import copy
import hashlib
import shutil
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import msgspec
import pytest

from api.v1.schemas.library_management import (
    PICARD_ORGANIZER_PROFILE_ID,
    LibraryManagementRootAssignment,
    profile_revision,
)
from core.exceptions import (
    AutomaticManagementHoldError,
    ExternalServiceError,
    PathLimitExceededError,
    ScriptValidationError,
)
from infrastructure.audio.metadata_engine import (
    AudioMetadataEngine,
    legacy_audio_projection,
)
from models.audio import AudioTag
from models.library_management import (
    INSUFFICIENT_SPACE,
    METADATA_UNAVAILABLE,
    PATH_TOO_LONG,
    PROFILE_CHANGED,
    SCRIPT_VALIDATION_FAILED,
    TRACK_NOT_MAPPED,
    LibraryManagementImportBundle,
    LibraryManagementImportFile,
)
from models.library_management_planning import (
    naming_policy_revision,
    pin_library_management_profile,
)
from models.library_management_artwork import ArtworkOutput, ArtworkProjection
from models.library_management_enrichment import (
    LyricsProjection,
    ReplayGainAnalysis,
    ReplayGainTrackResult,
)
from repositories.musicbrainz_management_models import MbManagementRelease
from services.native.automatic_import_management_service import (
    AutomaticImportManagementService,
)
from services.native.library_management_profile_service import (
    LibraryManagementProfileService,
)
from tests.services.native.test_library_management_planner import (
    FIXTURES,
    _configured,
    _planner,
)


def _service(  # noqa: ANN001, ANN202
    tmp_path: Path, preferences, store, *, lyrics=None, replaygain=None
):
    planner = _planner(tmp_path, store, preferences)
    profiles = LibraryManagementProfileService(preferences)
    return (
        AutomaticImportManagementService(
            profiles,
            planner,
            planner._canonical,
            planner._effective,
            planner._genres,
            planner._artwork,
            planner._audio,
            planner._write_planner,
            planner._naming,
            planner._tagging,
            lyrics=lyrics,
            replaygain=replaygain,
        ),
        planner,
    )


def _activate(
    preferences,
    policy_revision: str,
    *,
    stale: bool = False,
    acquisitions: bool = True,
    drop_imports: bool = False,
) -> None:
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    pinned = pin_library_management_profile(settings, profile)
    settings.root_assignments = [
        LibraryManagementRootAssignment(
            root_id="root-1",
            profile_id=profile.id,
            enabled=True,
            automatic_acquisitions=acquisitions,
            automatic_drop_imports=drop_imports,
            activation_profile_revision=(
                "stale-profile" if stale else profile_revision(profile)
            ),
            activation_policy_revision=policy_revision,
            activation_settings_revision=current.settings_revision,
            activation_naming_policy_revision=naming_policy_revision(pinned),
            activation_preview_token="confirmed",
            activation_preview_hash="confirmed-hash",
            activation_confirmed_at=100.0,
        )
    ]
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )


def _bundle(
    tmp_path: Path,
    catalog_source: Path,
    policy_revision: str,
    *,
    origin: str = "acquisition",
    authoritative: bool = True,
) -> LibraryManagementImportBundle:
    source = tmp_path / f"incoming-{origin}.flac"
    shutil.copy2(catalog_source, source)
    _tag, info = legacy_audio_projection(AudioMetadataEngine().read(source))
    return LibraryManagementImportBundle(
        idempotency_key=f"{origin}:automatic-preparation",
        origin=origin,
        policy_revision=policy_revision,
        files=(
            LibraryManagementImportFile(
                ordinal=0,
                input_path=str(source),
                destination_root_id="root-1",
                destination_relative_path="Incoming/01 Original.flac",
                tag=AudioTag(
                    title="Aria",
                    artist="Glenn Gould",
                    album="Goldberg Variations",
                    album_artist="Johann Sebastian Bach; Glenn Gould",
                    disc_number=1,
                    track_number=1,
                ),
                info=info,
                release_group_mbid="dcff25f1-702d-3b5e-b0da-d48172e6e62a",
                release_mbid="aff0622e-7bd3-4fb6-9ca3-0fa19dd2340b",
                recording_mbid="33333333-3333-4333-8333-333333333333",
                release_track_mbid="22222222-2222-4222-8222-222222222222",
                medium_position=1,
                release_track_position=1,
                authoritative_mapping=authoritative,
                confidence=1.0,
                source="download",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_acquisition_toggle_pins_full_projection_but_drop_toggle_is_independent(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)

    acquisition = await service.prepare(_bundle(tmp_path, source, policy_revision))
    acquisition_repeat = await service.prepare(
        _bundle(tmp_path, source, policy_revision)
    )
    drop = await service.prepare(
        _bundle(tmp_path, source, policy_revision, origin="drop_import")
    )

    managed = acquisition.files[0]
    assert managed.pinned_profile is not None
    assert managed.desired_document is not None
    assert managed.metadata_snapshot_id is not None
    assert managed.release_track_mbid == "22222222-2222-4222-8222-222222222222"
    assert managed.baseline_relative_path == "Incoming/01 Original.flac"
    assert managed.destination_relative_path == (
        "Johann Sebastian Bach; Glenn Gould/Goldberg Variations, BWV 988 (1982)/01 - Aria.flac"
    )
    assert acquisition_repeat.idempotency_key == acquisition.idempotency_key
    assert msgspec.to_builtins(drop) == msgspec.to_builtins(
        _bundle(tmp_path, source, policy_revision, origin="drop_import")
    )


@pytest.mark.asyncio
async def test_acquisition_and_drop_import_share_the_same_dynamic_multi_disc_path(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision, drop_imports=True)
    service, planner = _service(tmp_path, preferences, store)
    release = msgspec.json.decode(
        (FIXTURES / "musicbrainz" / "management_release.json").read_bytes(),
        type=MbManagementRelease,
    )
    second = copy.deepcopy(release.media[0])
    second.position = 2
    second.tracks[0].id = "99999999-9999-4999-8999-999999999999"
    second.tracks[0].recording.id = "88888888-8888-4888-8888-888888888888"
    release.media.append(second)
    planner._canonical._musicbrainz.get_canonical_release.return_value = release

    acquisition = await service.prepare(_bundle(tmp_path, source, policy_revision))
    drop = await service.prepare(
        _bundle(tmp_path, source, policy_revision, origin="drop_import")
    )

    expected = (
        "Johann Sebastian Bach; Glenn Gould/Goldberg Variations, BWV 988 (1982)/"
        "CD 01/01 - Aria.flac"
    )
    assert acquisition.files[0].destination_relative_path == expected
    assert drop.files[0].destination_relative_path == expected


@pytest.mark.asyncio
async def test_automatic_import_preserves_embedded_artwork_without_replacement(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    profile.artwork.embedded_enabled = True
    profile.artwork.external_enabled = False
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)
    service._artwork.project = AsyncMock(
        return_value=ArtworkProjection(preserved_existing=True)
    )
    bundle = _bundle(tmp_path, source, policy_revision)
    existing = AudioMetadataEngine().read(Path(bundle.files[0].input_path)).artwork

    prepared = await service.prepare(bundle)

    assert prepared.files[0].desired_document is not None
    assert prepared.files[0].desired_document.artwork == existing


@pytest.mark.asyncio
async def test_automatic_import_coalesces_identical_existing_external_artwork(
    tmp_path: Path,
) -> None:
    root, source, preferences, store, _settings, policy_revision = _configured(tmp_path)
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    profile.artwork.embedded_enabled = False
    profile.artwork.external_enabled = True
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)
    artwork = b"managed external artwork"
    artwork_sha256 = hashlib.sha256(artwork).hexdigest()
    service._artwork.project = AsyncMock(
        return_value=ArtworkProjection(
            external=(
                ArtworkOutput(
                    output_kind="external",
                    image_type="front",
                    content=artwork,
                    mime_type="image/jpeg",
                    format="jpeg",
                    width=1200,
                    height=1200,
                    byte_size=len(artwork),
                    sha256=artwork_sha256,
                    source="cover_art_archive_release",
                    source_candidate_id="exact-release-front",
                    source_is_exact_release=True,
                ),
            )
        )
    )
    bundle = _bundle(tmp_path, source, policy_revision)

    initial = await service.prepare(bundle)
    artifact = initial.files[0].artifacts[0]
    destination = root / artifact.destination_relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(artwork)

    identical = await service.prepare(bundle)
    destination.write_bytes(b"different existing artwork")
    different = await service.prepare(bundle)

    assert identical.files[0].artifacts == ()
    assert len(different.files[0].artifacts) == 1
    assert different.files[0].artifacts[0].source_fingerprint == artwork_sha256


@pytest.mark.asyncio
async def test_automatic_import_holds_stale_activation_and_unmapped_files(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision, stale=True)
    service, _planner_value = _service(tmp_path, preferences, store)

    with pytest.raises(AutomaticManagementHoldError) as stale:
        await service.prepare(_bundle(tmp_path, source, policy_revision))
    assert stale.value.reason_code == PROFILE_CHANGED

    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    settings.root_assignments[0].activation_profile_revision = profile_revision(profile)
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    with pytest.raises(AutomaticManagementHoldError) as unmapped:
        await service.prepare(
            _bundle(tmp_path, source, policy_revision, authoritative=False)
        )
    assert unmapped.value.reason_code == TRACK_NOT_MAPPED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("path_limit", PATH_TOO_LONG),
        ("script", SCRIPT_VALIDATION_FAILED),
    ],
)
async def test_automatic_import_classifies_script_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_reason: str,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)

    if failure_kind == "path_limit":
        error = PathLimitExceededError(
            "Rendered relative path exceeds the configured path limit.",
            script_name="Naming",
            line=1,
            column=1,
        )

        def reject(*_args, **_kwargs):  # noqa: ANN202
            raise error

        monkeypatch.setattr(service._naming, "format_management_path", reject)
    else:
        error = ScriptValidationError(
            "A custom tag exceeds its name or value-count bound.",
            script_name="tagging input",
            line=1,
            column=1,
        )

        def reject(*_args, **_kwargs):  # noqa: ANN202
            raise error

        monkeypatch.setattr(service._tagging, "apply", reject)

    with pytest.raises(AutomaticManagementHoldError) as held:
        await service.prepare(_bundle(tmp_path, source, policy_revision))

    assert held.value.reason_code == expected_reason


@pytest.mark.asyncio
async def test_required_musicbrainz_failure_holds_whole_automatic_unit(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision)
    service, planner = _service(tmp_path, preferences, store)
    planner._canonical._musicbrainz.get_canonical_release.side_effect = (
        ExternalServiceError("provider unavailable")
    )

    with pytest.raises(AutomaticManagementHoldError) as held:
        await service.prepare(_bundle(tmp_path, source, policy_revision))

    assert held.value.reason_code == METADATA_UNAVAILABLE


@pytest.mark.asyncio
async def test_optional_genre_outage_preserves_local_value_and_records_warning(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    profile.genres.sources = ["listenbrainz"]
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)

    prepared = await service.prepare(_bundle(tmp_path, source, policy_revision))

    request = prepared.files[0]
    genre = next(
        field for field in request.desired_document.fields if field.name == "genre"
    )
    assert request.management_warnings == ("genre:listenbrainz",)
    assert genre.action == "unchanged"


@pytest.mark.asyncio
async def test_automatic_import_pins_replaygain_before_publication(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    profile.enrichment.replaygain.enabled = True
    profile.enrichment.replaygain.mode = "replace"
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    _activate(preferences, policy_revision)
    bundle = _bundle(tmp_path, source, policy_revision)
    replaygain = AsyncMock()
    replaygain.analyze.return_value = ReplayGainAnalysis(
        status="available",
        analyzer_version="loudgain 0.6.8",
        tracks=(
            ReplayGainTrackResult(
                source_path=bundle.files[0].input_path,
                track_gain_db=1.25,
                track_peak=0.5,
                album_gain_db=2.5,
                album_peak=0.6,
            ),
        ),
    )
    service, _planner_value = _service(
        tmp_path, preferences, store, replaygain=replaygain
    )

    prepared = await service.prepare(bundle)
    desired = {
        value.name: value.value for value in prepared.files[0].desired_document.fields
    }

    assert desired["replaygain_track_gain"] == 1.25
    assert desired["replaygain_track_peak"] == 0.5
    assert desired["replaygain_album_gain"] == 2.5
    assert desired["replaygain_album_peak"] == 0.6
    replaygain.analyze.assert_awaited_once_with(
        (Path(bundle.files[0].input_path),), album_aware=True
    )


@pytest.mark.asyncio
async def test_required_replaygain_failure_holds_automatic_unit(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    profile.enrichment.replaygain.enabled = True
    profile.enrichment.replaygain.mode = "replace"
    profile.enrichment.replaygain.required = True
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )
    _activate(preferences, policy_revision)
    replaygain = AsyncMock()
    replaygain.analyze.return_value = ReplayGainAnalysis(
        status="deferred", reason="Analyzer unavailable."
    )
    service, _planner_value = _service(
        tmp_path, preferences, store, replaygain=replaygain
    )

    with pytest.raises(AutomaticManagementHoldError) as held:
        await service.prepare(_bundle(tmp_path, source, policy_revision))

    assert held.value.reason_code == METADATA_UNAVAILABLE


@pytest.mark.asyncio
async def test_automatic_import_holds_before_staging_when_capacity_is_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)
    monkeypatch.setattr(
        "services.native.automatic_import_management_service.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )

    with pytest.raises(AutomaticManagementHoldError) as held:
        await service.prepare(_bundle(tmp_path, source, policy_revision))

    assert held.value.reason_code == INSUFFICIENT_SPACE


def _update_profile(preferences, mutate) -> None:  # noqa: ANN001
    current = preferences.get_library_management_settings()
    settings = preferences.get_library_management_settings_raw()
    profile = next(
        value for value in settings.profiles if value.id == PICARD_ORGANIZER_PROFILE_ID
    )
    mutate(profile)
    preferences.save_library_management_settings_if_current(
        settings, expected_settings_revision=current.settings_revision
    )


def _enable_lyrics(preferences, **overrides) -> None:  # noqa: ANN001
    def mutate(profile) -> None:  # noqa: ANN001
        profile.enrichment.lyrics.enabled = True
        for name, value in overrides.items():
            setattr(profile.enrichment.lyrics, name, value)

    _update_profile(preferences, mutate)


def _enable_sidecar_moves(preferences) -> None:  # noqa: ANN001
    """The shared planner fixture turns sidecar moving off; these cases need it.

    Fetched lyrics deliberately do NOT depend on this setting - it governs
    companion files carried over from the download, not files this app writes.
    """

    def mutate(profile) -> None:  # noqa: ANN001
        profile.organization.move_sidecars = True

    _update_profile(preferences, mutate)


def _lyrics_service(**projection):  # noqa: ANN003, ANN202
    return SimpleNamespace(
        project=AsyncMock(
            return_value=LyricsProjection(status="available", **projection)
        )
    )


def _sidecars(prepared):  # noqa: ANN001, ANN202
    return [
        artifact
        for file in prepared.files
        for artifact in file.artifacts
        if artifact.kind == "sidecar"
    ]


@pytest.mark.asyncio
async def test_fetched_lyrics_are_written_beside_the_renamed_track(
    tmp_path: Path,
) -> None:
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _enable_lyrics(preferences)
    _activate(preferences, policy_revision)
    service, _planner_value = _service(
        tmp_path,
        preferences,
        store,
        lyrics=_lyrics_service(
            plain_lyrics="Provider plain",
            synced_lyrics="[00:01.000]Provider synced",
        ),
    )

    prepared = await service.prepare(_bundle(tmp_path, source, policy_revision))

    audio = PurePosixPath(prepared.files[0].destination_relative_path)
    sidecars = _sidecars(prepared)
    assert [value.destination_relative_path for value in sidecars] == [
        audio.with_suffix(".lrc").as_posix()
    ]
    assert sidecars[0].content == b"[00:01.000]Provider synced\n"


@pytest.mark.asyncio
async def test_a_shipped_lrc_follows_its_track_through_the_rename(
    tmp_path: Path,
) -> None:
    """Lyrics are bound to audio by exact stem, so a companion that keeps its
    source name while the organizer renames the track is silently orphaned."""
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _enable_sidecar_moves(preferences)
    _activate(preferences, policy_revision)
    service, _planner_value = _service(tmp_path, preferences, store)
    bundle = _bundle(tmp_path, source, policy_revision)
    incoming = Path(bundle.files[0].input_path)
    incoming.with_suffix(".lrc").write_bytes(b"[00:02.000]Shipped\n")

    prepared = await service.prepare(bundle)

    audio = PurePosixPath(prepared.files[0].destination_relative_path)
    assert audio.stem != incoming.stem, "fixture must actually rename the track"
    assert [value.destination_relative_path for value in _sidecars(prepared)] == [
        audio.with_suffix(".lrc").as_posix()
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve_existing", [True, False])
async def test_preserve_existing_decides_between_shipped_and_fetched_lyrics(
    tmp_path: Path,
    preserve_existing: bool,
) -> None:
    """Both resolve to the same destination once the shipped file follows the
    rename, so exactly one must survive or the collision guard holds the import.
    """
    _root, source, preferences, store, _settings, policy_revision = _configured(
        tmp_path
    )
    _enable_lyrics(preferences, preserve_existing=preserve_existing)
    _enable_sidecar_moves(preferences)
    _activate(preferences, policy_revision)
    service, _planner_value = _service(
        tmp_path,
        preferences,
        store,
        lyrics=_lyrics_service(synced_lyrics="[00:01.000]Fetched"),
    )
    bundle = _bundle(tmp_path, source, policy_revision)
    Path(bundle.files[0].input_path).with_suffix(".lrc").write_bytes(
        b"[00:02.000]Shipped\n"
    )

    sidecars = _sidecars(await service.prepare(bundle))

    assert len(sidecars) == 1
    if preserve_existing:
        assert sidecars[0].source_path is not None
        assert sidecars[0].content is None
    else:
        assert sidecars[0].content == b"[00:01.000]Fetched\n"
