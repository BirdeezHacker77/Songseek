"""AuthStore per-user library root column: migration ratchet + roundtrip.

``library_root_id`` is added by a guarded ALTER rather than being in the CREATE
TABLE, so existing installs pick it up on the next start. Per the house rule for
migrations, the ratchet is tested both for idempotency and for the upgrade path
from a database that predates the column.
"""

import sqlite3
import threading
from pathlib import Path

import pytest

from infrastructure.persistence.auth_store import AuthStore


async def _user(store: AuthStore, user_id: str = "u1") -> None:
    await store.create_user(id=user_id, display_name=f"User {user_id}", role="user")


def test_ratchet_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "library.db"
    lock = threading.Lock()
    AuthStore(db_path, write_lock=lock)
    # Second construction re-runs the guarded ALTER for library_root_id; the
    # duplicate-column OperationalError must stay swallowed.
    AuthStore(db_path, write_lock=lock)

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_users)")}
    finally:
        connection.close()
    assert "library_root_id" in columns


def test_ratchet_upgrades_a_database_that_predates_the_column(tmp_path: Path):
    """The real migration path: a pre-existing auth_users without the column."""
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """CREATE TABLE auth_users (
                   id TEXT PRIMARY KEY,
                   display_name TEXT NOT NULL,
                   email TEXT UNIQUE,
                   avatar_url TEXT,
                   role TEXT NOT NULL DEFAULT 'user',
                   created_at TEXT NOT NULL,
                   last_login_at TEXT
               )"""
        )
        connection.execute(
            "INSERT INTO auth_users (id, display_name, role, created_at) "
            "VALUES ('legacy-1', 'Legacy', 'admin', '2024-01-01T00:00:00Z')"
        )
        connection.commit()
    finally:
        connection.close()

    AuthStore(db_path, write_lock=threading.Lock())

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_users)")}
        row = connection.execute(
            "SELECT library_root_id FROM auth_users WHERE id = 'legacy-1'"
        ).fetchone()
    finally:
        connection.close()
    assert "library_root_id" in columns
    assert row[0] is None  # existing users are unassigned, i.e. first-root fallback


@pytest.mark.asyncio
async def test_assign_and_clear_roundtrip(tmp_path: Path):
    store = AuthStore(tmp_path / "auth.db")
    await _user(store)

    assert await store.get_user_library_root("u1") is None
    assert await store.update_library_root("u1", "root-b") is True
    assert await store.get_user_library_root("u1") == "root-b"

    assert await store.update_library_root("u1", None) is True
    assert await store.get_user_library_root("u1") is None


@pytest.mark.asyncio
async def test_update_reports_false_for_an_unknown_user(tmp_path: Path):
    store = AuthStore(tmp_path / "auth.db")
    assert await store.update_library_root("ghost", "root-a") is False


@pytest.mark.asyncio
async def test_unknown_user_root_reads_as_none(tmp_path: Path):
    store = AuthStore(tmp_path / "auth.db")
    assert await store.get_user_library_root("ghost") is None


@pytest.mark.asyncio
async def test_assignments_map_lists_only_assigned_users(tmp_path: Path):
    store = AuthStore(tmp_path / "auth.db")
    for user_id in ("u1", "u2", "u3"):
        await _user(store, user_id)
    await store.update_library_root("u1", "root-a")
    await store.update_library_root("u2", "root-a")  # roots are not exclusive

    assert await store.get_library_root_assignments() == {"u1": "root-a", "u2": "root-a"}


@pytest.mark.asyncio
async def test_assignment_survives_on_the_user_record(tmp_path: Path):
    """user_to_response reads it off the record, so it must round-trip through
    _to_user rather than only through the dedicated getter."""
    store = AuthStore(tmp_path / "auth.db")
    await _user(store)
    await store.update_library_root("u1", "root-c")

    record = await store.get_user_by_id("u1")

    assert record is not None
    assert record.library_root_id == "root-c"
