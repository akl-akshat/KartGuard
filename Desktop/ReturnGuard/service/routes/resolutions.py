"""Resolution read endpoints: list/filter and full detail."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agent.deps import get_deps
from service.schemas import ResolveResponse, summarize_resolution

router = APIRouter()


@router.get("/resolutions", response_model=list[ResolveResponse])
def list_resolutions(status: str | None = Query(None), limit: int = Query(100, le=500)):
    rows = get_deps().repo.list_resolutions(status=status, limit=limit)
    return [summarize_resolution(r) for r in rows]


@router.get("/resolutions/{request_id}")
def get_resolution(request_id: str) -> dict:
    row = get_deps().repo.get_resolution(request_id)
    if not row:
        raise HTTPException(status_code=404, detail="resolution not found")
    return row  # full detail incl. rationale + trace link
