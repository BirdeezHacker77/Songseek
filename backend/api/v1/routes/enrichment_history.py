"""What enrichment changed, and putting one of those changes back.

Admin-only: every entry names a path in the shared library, and restoring one
rewrites that file.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.schemas.enrichment_history import (
    EnrichmentHistoryItem,
    EnrichmentHistoryPage,
)
from core.dependencies import get_enrichment_history_service
from infrastructure.msgspec_fastapi import MsgSpecRoute
from middleware import CurrentAdminDep

logger = logging.getLogger(__name__)

router = APIRouter(
    route_class=MsgSpecRoute, prefix="/enrichment-history", tags=["enrichment-history"]
)


@router.get("", response_model=EnrichmentHistoryPage)
async def list_enrichment_history(
    _: CurrentAdminDep,
    limit: int = Query(100, ge=1, le=500),
    service=Depends(get_enrichment_history_service),
):
    entries = await service.list_recent(limit=limit)
    return EnrichmentHistoryPage(
        items=tuple(EnrichmentHistoryItem.from_entry(entry) for entry in entries)
    )


@router.post("/{entry_id}/restore", response_model=EnrichmentHistoryItem)
async def restore_enrichment_change(
    entry_id: str,
    _: CurrentAdminDep,
    service=Depends(get_enrichment_history_service),
):
    try:
        return EnrichmentHistoryItem.from_entry(await service.restore(entry_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, FileNotFoundError) as error:
        # Already restored, or the file has moved since. Both are things the
        # person can act on, so the message is the point of the response.
        raise HTTPException(status_code=409, detail=str(error)) from error
