"""Liveness / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict:
    graph_ready = getattr(request.app.state, "graph", None) is not None
    return {"status": "ready" if graph_ready else "starting", "graph": graph_ready}
