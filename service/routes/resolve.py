"""Resolve endpoints: POST /resolve (sync) and POST /resolve/stream (SSE)."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from agent.deps import get_deps
from agent.runner import is_paused, run_config
from agent.state import initial_state
from service.schemas import ResolveRequest, ResolveResponse, summarize, summarize_resolution

router = APIRouter()


@router.post("/resolve", response_model=ResolveResponse)
def resolve(req: ResolveRequest, request: Request) -> ResolveResponse:
    graph = request.app.state.graph
    repo = get_deps().repo

    # Idempotency (API-1): a known request_id returns its existing resolution, no re-run.
    existing = repo.get_resolution(req.request_id)
    if existing:
        return summarize_resolution(existing)

    cfg = run_config(req.request_id)
    state = initial_state(req.request_id, req.issue_text, req.channel, req.order_id, req.customer_id)
    t0 = time.perf_counter()
    out = graph.invoke(state, cfg)
    paused = is_paused(graph, cfg)

    # Record latency on the persisted resolution for the analytics summary (FR-RPT-1).
    if not paused:
        row = repo.get_resolution(req.request_id)
        if row is not None:
            row["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            repo.save_resolution(row)
    return summarize(out, paused=paused)


@router.post("/resolve/stream")
async def resolve_stream(req: ResolveRequest, request: Request) -> EventSourceResponse:
    graph = request.app.state.graph
    cfg = run_config(req.request_id)
    state = initial_state(req.request_id, req.issue_text, req.channel, req.order_id, req.customer_id)

    async def event_gen():
        for chunk in graph.stream(state, cfg, stream_mode="updates"):
            for node, update in chunk.items():
                yield {"event": "node", "data": json.dumps({"node": node, "update": _safe(update)})}
        yield {"event": "done", "data": json.dumps({"paused": is_paused(graph, cfg)})}

    return EventSourceResponse(event_gen())


def _safe(update) -> dict:
    if not isinstance(update, dict):
        return {"value": str(update)}
    out = {}
    for k, v in update.items():
        if k == "messages":
            continue
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
