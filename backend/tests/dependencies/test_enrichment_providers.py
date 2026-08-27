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
    providers.get_enrichment_history_store.cache_clear()
    providers.get_enrichment_history_service.cache_clear()
    yield
    providers.get_enrichment_history_store.cache_clear()
    providers.get_enrichment_history_service.cache_clear()


def test_the_history_store_provider_actually_constructs(tmp_path: Path) -> None:
    store = providers.get_enrichment_history_store()

    assert store.db_path == tmp_path / "library.db"


def test_the_history_service_provider_actually_constructs() -> None:
    service = providers.get_enrichment_history_service()

    assert service is not None
