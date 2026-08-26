"""Per-user library roots: a download imports into the root assigned to its requester.

The invariant that makes separate per-user copies possible is dedup scoping. Before
per-user roots, position-dedup asked "does the library hold this (album, disc, track)
anywhere?" and dropped the import if so. With one root per user that answer is wrong:
another user holding the track is not a duplicate of *this* user's request, and
suppressing it would leave the second user with nothing in their root while the import
reported success against a file they cannot see.

These tests cover both directions - a different user must NOT dedupe, the same user
still MUST - plus the fallbacks that keep single-root installs behaving exactly as
before.
"""

import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from infrastructure.audio.tagger import AudioTagger
from infrastructure.persistence.library_db import LibraryDB
from models.download_manifest import DownloadManifest, ExpectedFile
from services.native.file_processor import FileProcessor
from services.native.library_manager import LibraryManager
from services.native.naming import NamingTemplateEngine
from tests.helpers import make_test_import_publisher

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "library"
_FLAC = FIXTURES / "flac_full_01.flac"  # Radiohead / OK Computer, disc 1 track 1
_TEMPLATE = "{albumartist}/{album} ({year})/{disc:02d}{track:02d} {title}.{ext}"
_REL = "Radiohead/OK Computer (1997)/0101 Airbag.flac"


class _StubClient:
    def __init__(self, downloads_root: Path) -> None:
        self._root = downloads_root

    async def get_file_path(self, handle, remote_filename: str, size: int | None = None):
        return self._root / remote_filename.replace("\\", "/").lstrip("/")


def _make(tmp_path: Path, assignments: dict[str, str], *, root_ids=("root-a", "root-b")):
    """FileProcessor wired to two library roots and a user -> root assignment map."""
    downloads = tmp_path / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    roots = {root_id: tmp_path / f"library-{root_id}" for root_id in root_ids}
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)

    db_path = tmp_path / "library.db"
    manager = LibraryManager(LibraryDB(db_path=db_path, write_lock=threading.Lock()))
    original_attributions = manager.get_attributions_for_paths

    def locate(file_path: str) -> tuple[str | None, str | None]:
        resolved = Path(file_path)
        for root_id, root_path in roots.items():
            try:
                return root_id, resolved.relative_to(root_path).as_posix()
            except ValueError:
                continue
        return None, None

    def with_root(row):
        if row is None:
            return None
        value = dict(row)
        value["root_id"], value["relative_path"] = locate(value["file_path"])
        return value

    async def get_file_at_position(
        release_group_mbid, disc_number, track_number, *, root_id=None
    ):
        # Queried directly rather than through LibraryManager: the legacy helper
        # returns only the earliest row at the slot, which cannot express "the row
        # in THIS root" once two roots hold the same track. The target catalog does
        # this filtering in SQL against its own root_id column.
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT * FROM library_files WHERE release_group_mbid = ? "
                "AND disc_number = ? AND track_number = ? AND deleted_at IS NULL "
                "ORDER BY imported_at",
                (release_group_mbid.lower(), disc_number, track_number),
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            candidate = with_root(row)
            if root_id is None or candidate["root_id"] == root_id:
                return candidate
        return None

    async def get_attributions_for_paths(paths):
        rows = await original_attributions(paths)
        return {path: with_root(row) for path, row in rows.items()}

    manager.get_file_at_position = get_file_at_position
    manager.get_attributions_for_paths = get_attributions_for_paths

    async def resolve_user_root(user_id: str) -> str | None:
        return assignments.get(user_id)

    processor = FileProcessor(
        AudioTagger(),
        naming_engine=NamingTemplateEngine(),
        library_manager=manager,
        library_paths=[roots[root_id] for root_id in root_ids],
        client=_StubClient(downloads),
        slskd_downloads_path=downloads,
        verify_downloads=False,
        library_root_ids=list(root_ids),
        publish_import_bundle=make_test_import_publisher(manager, roots),
        policy_revision_getter=lambda: "test-policy",
        user_root_resolver=resolve_user_root,
    )
    return processor, roots, downloads


def _manifest(user_id: str | None, *, task_id: str) -> DownloadManifest:
    return DownloadManifest(
        task_id=task_id,
        source_username="peer",
        release_group_mbid="rg-1",
        artist_name="Radiohead",
        album_title="OK Computer",
        naming_template=_TEMPLATE,
        target_files=[ExpectedFile(filename=f"{task_id}.flac", size=1)],
        expected_tracks=[],
        year=1997,
        origin="user",
        requested_by_user_id=user_id,
    )


async def _download_as(processor, downloads: Path, user_id: str | None, task_id: str):
    shutil.copy(_FLAC, downloads / f"{task_id}.flac")
    return await processor.process_downloaded(_manifest(user_id, task_id=task_id))


@pytest.mark.asyncio
async def test_each_user_imports_into_their_own_root(tmp_path: Path):
    """The case the feature exists for: two users request the same album and each
    ends up with their own copy, under their own root."""
    processor, roots, downloads = _make(
        tmp_path, {"user-1": "root-a", "user-2": "root-b"}
    )

    first = await _download_as(processor, downloads, "user-1", "t1")
    second = await _download_as(processor, downloads, "user-2", "t2")

    assert first.failed == [] and second.failed == []
    assert first.succeeded == [str(roots["root-a"] / _REL)]
    assert second.succeeded == [str(roots["root-b"] / _REL)]
    assert (roots["root-a"] / _REL).exists()
    assert (roots["root-b"] / _REL).exists()


@pytest.mark.asyncio
async def test_same_user_repull_still_dedupes(tmp_path: Path):
    """Scoping dedup by root must not disable it. A second pull by the SAME user
    resolves to the file already in their root instead of writing a second copy."""
    processor, roots, downloads = _make(tmp_path, {"user-1": "root-a"})

    first = await _download_as(processor, downloads, "user-1", "t1")
    second = await _download_as(processor, downloads, "user-1", "t2")

    target = roots["root-a"] / _REL
    assert first.succeeded == [str(target)]
    assert second.succeeded == [str(target)]  # same file, not a duplicate
    assert len(list(roots["root-a"].rglob("*.flac"))) == 1


@pytest.mark.asyncio
async def test_unassigned_user_falls_back_to_first_root(tmp_path: Path):
    """No assignment is not an error - it is every install before this feature."""
    processor, roots, downloads = _make(tmp_path, {})

    result = await _download_as(processor, downloads, "user-nobody", "t1")

    assert result.succeeded == [str(roots["root-a"] / _REL)]
    assert not (roots["root-b"] / _REL).exists()


@pytest.mark.asyncio
async def test_manifest_without_user_falls_back_to_first_root(tmp_path: Path):
    """Manifests written before requested_by_user_id existed carry None."""
    processor, roots, downloads = _make(tmp_path, {"user-1": "root-b"})

    result = await _download_as(processor, downloads, None, "t1")

    assert result.succeeded == [str(roots["root-a"] / _REL)]


@pytest.mark.asyncio
async def test_assignment_to_removed_root_falls_back(tmp_path: Path):
    """Roots live in preferences and can be deleted after assignment. A dangling id
    must not strand the download - it falls back to the first root."""
    processor, roots, downloads = _make(tmp_path, {"user-1": "root-deleted"})

    result = await _download_as(processor, downloads, "user-1", "t1")

    assert result.failed == []
    assert result.succeeded == [str(roots["root-a"] / _REL)]
