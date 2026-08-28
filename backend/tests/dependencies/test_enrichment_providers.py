"""These providers are only ever reached at container startup.

A missing import inside one is invisible to every other test and to the linter -
the codebase deliberately annotates lazily imported types as strings, so
undefined-name checking cannot be enabled repo-wide without churning hundreds of
deliberate sites. Importing the provider symbol proves nothing either; the body
only runs when it is called. So these call them.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.dependencies import service_providers as providers


@pytest.fixture(autouse=True)
def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "core.config.get_settings",
        lambda: SimpleNamespace(
            library_db_path=tmp_path / "library.db",
            cache_dir=tmp_path / "cache",
        ),
    )
    _clear()
    yield
    _clear()


def _clear() -> None:
    for provider in (
        providers.get_enrichment_history_store,
        providers.get_enrichment_history_service,
        providers.get_import_review_store,
        providers.get_import_review_service,
        providers.get_post_import_identification_service,
        providers.get_post_import_enrichment_service,
    ):
        provider.cache_clear()


def test_the_history_store_provider_actually_constructs(tmp_path: Path) -> None:
    store = providers.get_enrichment_history_store()

    assert store.db_path == tmp_path / "library.db"


def test_the_history_service_provider_actually_constructs() -> None:
    service = providers.get_enrichment_history_service()

    assert service is not None


def test_the_review_store_provider_actually_constructs(tmp_path: Path) -> None:
    store = providers.get_import_review_store()

    assert store.db_path == tmp_path / "library.db"


def test_the_review_service_provider_actually_constructs() -> None:
    assert providers.get_import_review_service() is not None


def test_the_identification_provider_actually_constructs() -> None:
    assert providers.get_post_import_identification_service() is not None


def test_the_enrichment_provider_actually_constructs() -> None:
    """It now builds four other providers on the way, any of which could fail
    the same way the history store did - at startup, in the container, only."""
    assert providers.get_post_import_enrichment_service() is not None


@pytest.mark.asyncio
async def test_the_review_service_can_reach_the_writer_it_was_given_lazily() -> None:
    """The two services hold each other, so the enrichment side is resolved on
    use rather than at construction. A broken indirection there would surface
    only when somebody accepted a review - which is exactly the kind of thing
    that gets found in production instead of here.

    Calling it with no fields is enough: it reaches the real method and returns
    its real "nothing to write" answer, without needing a file on disk.
    """
    service = providers.get_import_review_service()

    wrote = await service._enrichment.apply_tag_fields(Path("/nope/missing.flac"), ())

    assert wrote is False
