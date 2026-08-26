"""Admin per-user library root routes.

Assigning a root decides where that user's downloads land on disk, so the write
endpoint validates the id against configured roots rather than trusting the body -
an unknown id would otherwise be stored and then silently ignored at import time,
leaving an admin convinced they had separated a user who was still sharing a root.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI

from api.v1.routes import auth as auth_routes
from core.dependencies.auth_providers import get_auth_service
from core.dependencies.service_providers import get_library_policy_resolver
from middleware import _get_current_admin, _get_current_user
from tests.helpers import build_test_client, mock_admin_user, mock_user


def _roots(*ids: str):
    return SimpleNamespace(
        settings=SimpleNamespace(
            library_roots=[
                SimpleNamespace(id=root_id, label=root_id.upper(), path=f"/data/{root_id}")
                for root_id in ids
            ]
        )
    )


def _app(auth, *, roots=("root-a", "root-b"), admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_auth_service] = lambda: auth
    app.dependency_overrides[get_library_policy_resolver] = lambda: _roots(*roots)
    if admin:
        app.dependency_overrides[_get_current_admin] = mock_admin_user
        app.dependency_overrides[_get_current_user] = lambda: mock_user(role="admin")
    return app


def test_list_library_roots_reports_configured_roots_and_holders():
    auth = AsyncMock()
    auth.get_library_root_assignments.return_value = {"u2": "root-a", "u1": "root-a"}

    resp = build_test_client(_app(auth)).get("/auth/admin/library-roots")

    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == ["root-a", "root-b"]
    assert body[0]["label"] == "ROOT-A"
    assert body[0]["path"] == "/data/root-a"
    assert body[0]["assigned_user_ids"] == ["u1", "u2"]  # sorted, not insertion order
    assert body[1]["assigned_user_ids"] == []


def test_set_library_root_assigns_a_configured_root():
    auth = AsyncMock()
    auth.set_library_root.return_value = True

    resp = build_test_client(_app(auth)).put(
        "/auth/admin/users/u1/library-root", json={"library_root_id": "root-b"}
    )

    assert resp.status_code == 204
    auth.set_library_root.assert_awaited_once_with("u1", "root-b")


def test_set_library_root_clears_assignment_with_null():
    auth = AsyncMock()
    auth.set_library_root.return_value = True

    resp = build_test_client(_app(auth)).put(
        "/auth/admin/users/u1/library-root", json={"library_root_id": None}
    )

    assert resp.status_code == 204
    auth.set_library_root.assert_awaited_once_with("u1", None)


def test_blank_root_id_is_treated_as_clearing_not_as_an_id():
    """The picker's "Default (first root)" option posts an empty string."""
    auth = AsyncMock()
    auth.set_library_root.return_value = True

    resp = build_test_client(_app(auth)).put(
        "/auth/admin/users/u1/library-root", json={"library_root_id": "   "}
    )

    assert resp.status_code == 204
    auth.set_library_root.assert_awaited_once_with("u1", None)


def test_unknown_root_is_rejected_before_it_reaches_the_store():
    auth = AsyncMock()

    resp = build_test_client(_app(auth)).put(
        "/auth/admin/users/u1/library-root", json={"library_root_id": "root-gone"}
    )

    assert resp.status_code == 400
    auth.set_library_root.assert_not_awaited()


def test_unknown_user_returns_404():
    auth = AsyncMock()
    auth.set_library_root.return_value = False  # no row updated

    resp = build_test_client(_app(auth)).put(
        "/auth/admin/users/nope/library-root", json={"library_root_id": "root-a"}
    )

    assert resp.status_code == 404


def test_library_root_routes_require_admin():
    auth = AsyncMock()
    client = build_test_client(_app(auth, admin=False))

    assert client.get("/auth/admin/library-roots").status_code == 401
    assert (
        client.put(
            "/auth/admin/users/u1/library-root", json={"library_root_id": "root-a"}
        ).status_code
        == 401
    )
    auth.set_library_root.assert_not_awaited()
