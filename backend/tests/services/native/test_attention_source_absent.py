"""Deciding whether a needs-attention attempt still has bytes to clean up.

An attempt whose source cannot be found anywhere used to sit in
`needs_attention` forever: it rechecked every hour, logged "not locatable on the
downloads mount" each time, and held the cleanup service degraded over a file
nobody could find. Deleting the download client's temp folder by hand - the
ordinary way somebody clears space - puts an attempt in exactly that state.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.native.acquisition_cleanup_service import _attention_source_absent


def _attempt(root: Path, *, paths: list[str] | None = None, source: str = "soulseek"):  # noqa: ANN202
    return SimpleNamespace(
        mount_root=str(root),
        source=source,
        workspace_path="",
        materialized_paths=paths or [],
    )


def _materialization(root: Path, *, state: str = "completed", paths=None):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        state=state,
        mount_root=str(root),
        workspace_path="",
        file_paths=paths or [],
    )


# --- the regression -----------------------------------------------------------


def test_a_source_nobody_can_find_is_treated_as_gone(tmp_path: Path) -> None:
    """The stuck case: the client still lists the transfer as completed, but the
    file is not on the mount and the attempt never recorded a path for it."""
    absent = _attention_source_absent(
        _attempt(tmp_path), _materialization(tmp_path, state="completed")
    )

    assert absent is True


def test_a_failed_transfer_with_no_paths_is_also_gone(tmp_path: Path) -> None:
    absent = _attention_source_absent(
        _attempt(tmp_path), _materialization(tmp_path, state="failed")
    )

    assert absent is True


# --- what must still hold it back ---------------------------------------------


def test_an_active_transfer_is_never_called_gone(tmp_path: Path) -> None:
    """Bytes may still be arriving; concluding "gone" would cancel a live
    transfer."""
    absent = _attention_source_absent(
        _attempt(tmp_path), _materialization(tmp_path, state="active")
    )

    assert absent is False


def test_an_unreachable_mount_is_not_evidence_of_anything(tmp_path: Path) -> None:
    """A file is not gone because the disk holding it went away."""
    missing_root = tmp_path / "not-mounted"

    absent = _attention_source_absent(
        _attempt(missing_root), _materialization(missing_root)
    )

    assert absent is False


def test_a_file_that_is_still_there_is_not_gone(tmp_path: Path) -> None:
    track = tmp_path / "01.flac"
    track.write_bytes(b"still here")

    absent = _attention_source_absent(
        _attempt(tmp_path, paths=[str(track)]), _materialization(tmp_path)
    )

    assert absent is False


def test_a_recorded_path_that_has_been_deleted_is_gone(tmp_path: Path) -> None:
    absent = _attention_source_absent(
        _attempt(tmp_path, paths=[str(tmp_path / "deleted.flac")]),
        _materialization(tmp_path),
    )

    assert absent is True


def test_one_surviving_file_out_of_several_holds_the_attempt(tmp_path: Path) -> None:
    survivor = tmp_path / "02.flac"
    survivor.write_bytes(b"still here")

    absent = _attention_source_absent(
        _attempt(tmp_path, paths=[str(tmp_path / "gone.flac"), str(survivor)]),
        _materialization(tmp_path),
    )

    assert absent is False


def test_a_path_escaping_the_mount_is_refused(tmp_path: Path) -> None:
    """Never conclude anything about a path outside the mount it claims to be in."""
    outside = tmp_path.parent / "elsewhere.flac"

    absent = _attention_source_absent(
        _attempt(tmp_path, paths=[str(outside)]), _materialization(tmp_path)
    )

    assert absent is False


@pytest.mark.parametrize("state", ["completed", "failed", "missing"])
def test_the_client_state_alone_no_longer_decides_it(
    tmp_path: Path, state: str
) -> None:
    """Previously only "missing" resolved, so a completed record with no findable
    file rechecked hourly for as long as the row existed."""
    assert _attention_source_absent(
        _attempt(tmp_path), _materialization(tmp_path, state=state)
    )
