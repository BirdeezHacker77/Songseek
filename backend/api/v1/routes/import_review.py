"""Identification reviews: downloads whose MusicBrainz match was uncertain.

Admin-only, because answering one rewrites tags on files in the shared library.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.schemas.import_review import ImportReviewAcceptResponse
from core.dependencies import get_import_review_service
from infrastructure.msgspec_fastapi import MsgSpecRoute
from middleware import CurrentAdminDep
from models.import_review import ImportReviewPage
from services.native.import_review_service import ImportReviewError

logger = logging.getLogger(__name__)

router = APIRouter(
    route_class=MsgSpecRoute, prefix="/import-review", tags=["import-review"]
)


@router.get("", response_model=ImportReviewPage)
async def list_import_reviews(
    _: CurrentAdminDep,
    status: str = Query("pending", pattern="^(pending|accepted|dismissed|all)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service=Depends(get_import_review_service),
):
    return await service.list_entries(
        status=None if status == "all" else status, limit=limit, offset=offset
    )


@router.post("/{entry_id}/accept", response_model=ImportReviewAcceptResponse)
async def accept_import_review(
    entry_id: str,
    _: CurrentAdminDep,
    service=Depends(get_import_review_service),
):
    try:
        written = await service.accept(entry_id)
    except ImportReviewError as error:
        # The reasons are all things the person can act on - the album moved, the
        # review was already answered, MusicBrainz is unreachable - so the message
        # is the point of the response.
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ImportReviewAcceptResponse(files_written=written)


@router.post("/{entry_id}/dismiss", response_model=ImportReviewAcceptResponse)
async def dismiss_import_review(
    entry_id: str,
    _: CurrentAdminDep,
    service=Depends(get_import_review_service),
):
    if not await service.dismiss(entry_id):
        raise HTTPException(
            status_code=409, detail="That review has already been answered."
        )
    return ImportReviewAcceptResponse(files_written=0)
