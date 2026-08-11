"""Read-only MusicBrainz edition discovery for a local album."""

from core.exceptions import ResourceNotFoundError
from infrastructure.persistence.native_library_store import NativeLibraryStore
from infrastructure.queue.priority_queue import RequestPriority
from models.identification import ReleaseEditionSearchPage
from repositories.protocols.identification import IdentificationProviderProtocol


class AlbumEditionFinderService:
    def __init__(
        self,
        store: NativeLibraryStore,
        provider: IdentificationProviderProtocol,
    ) -> None:
        self._store = store
        self._provider = provider

    async def search(
        self,
        album_id: str,
        *,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[str, str | None, ReleaseEditionSearchPage]:
        context = await self._store.get_album_identification_context(album_id)
        if context is None:
            raise ResourceNotFoundError("Library album not found.")
        if not any(track["availability"] == "indexed" for track in context["tracks"]):
            raise ResourceNotFoundError("Library album has no indexed tracks.")

        album = context["album"]
        effective_query = " ".join((query or "").split())
        if not effective_query:
            effective_query = " ".join(
                value
                for value in (
                    album.get("album_artist_name") or "",
                    album.get("title") or "",
                )
                if value
            )
        page = await self._provider.search_release_editions(
            effective_query,
            limit,
            offset,
            RequestPriority.USER_INITIATED,
        )
        identity = context["identity"] or {}
        return effective_query, identity.get("release_group_mbid"), page
