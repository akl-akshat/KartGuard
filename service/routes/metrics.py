"""Analytics endpoint (FR-RPT-1) + the operator dashboard (UI-2, stretch)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from agent.deps import get_deps
from service.metrics import compute_summary

router = APIRouter()
_DASHBOARD = Path(__file__).resolve().parents[1] / "static" / "dashboard.html"


@router.get("/metrics/summary")
def metrics_summary() -> dict:
    return compute_summary(get_deps().repo)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return _DASHBOARD.read_text(encoding="utf-8")
