from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.exceptions import ResourceNotFoundError
from infrastructure.queue.priority_queue import RequestPriority
from models.identification import ReleaseEditionSearchPage
from services.native.album_edition_finder_service import AlbumEditionFinderService


def _context(*, availability: str = "indexed") -> dict:
    return {
        "album": {"title": "Album", "album_artist_name": "Artist"},
        "tracks": [{"availability": availability}],
        "identity": {"release_group_mbid": "group-1"},
    }


@pytest.mark.asyncio
async def test_search_prefills_local_text_and_performs_no_catalog_mutation() -> None:
    store = SimpleNamespace(
        get_album_identification_context=AsyncMock(return_value=_context())
    )
    provider = SimpleNamespace(
        search_release_editions=AsyncMock(return_value=ReleaseEditionSearchPage())
    )
    service = AlbumEditionFinderService(store, provider)

    query, release_group_mbid, page = await service.search(
        "album-1", query=None, limit=12, offset=0
    )

    assert query == "Artist Album"
    assert release_group_mbid == "group-1"
    assert page.items == []
    store.get_album_identification_context.assert_awaited_once_with("album-1")
    provider.search_release_editions.assert_awaited_once_with(
        "Artist Album", 12, 0, RequestPriority.USER_INITIATED
    )
    assert list(vars(store)) == ["get_album_identification_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, _context(availability="missing")])
async def test_search_rejects_missing_or_trackless_albums(context: dict | None) -> None:
    store = SimpleNamespace(
        get_album_identification_context=AsyncMock(return_value=context)
    )
    provider = SimpleNamespace(search_release_editions=AsyncMock())

    with pytest.raises(ResourceNotFoundError):
        await AlbumEditionFinderService(store, provider).search(
            "album-1", query="query", limit=12, offset=0
        )

    provider.search_release_editions.assert_not_awaited()
