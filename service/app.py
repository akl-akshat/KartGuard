"""FastAPI service (SRS §6.2).

Lifespan builds the compiled graph with a **Postgres** checkpointer in production (durable
HITL resume, CON-3) and falls back to an in-memory saver only when Postgres is unreachable
(offline/dev) — the fallback lives in the service layer, never in ``agent/``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent.graph import build_graph
from config.settings import settings
from observability.logging import configure_logging
from service.routes import (
    chat,
    escalations,
    health,
    metrics,
    platform,
    portal,
    resolutions,
    resolve,
    tenants,
)

log = logging.getLogger("service")


def _build_graph_with_checkpointer():
    """Postgres checkpointer in production; in-memory fallback if the DB is unreachable."""
    try:
        from agent.checkpointer import postgres_checkpointer

        cm = postgres_checkpointer()
        cp = cm.__enter__()
        return build_graph(checkpointer=cp), cm
    except Exception as exc:  # noqa: BLE001
        from langgraph.checkpoint.memory import MemorySaver  # service-layer fallback only

        log.warning("Postgres checkpointer unavailable (%s); using in-memory saver.",
                    exc.__class__.__name__)
        return build_graph(checkpointer=MemorySaver()), None


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if not settings.use_stub_llm:
        settings.validate_runtime()
    graph, cm = _build_graph_with_checkpointer()
    app.state.graph = graph
    app.state._checkpointer_cm = cm
    from service import demo_seed

    if demo_seed.seed_enabled():  # hosted demos: keep a sample tenant available (RG_SEED_DEMO=1)
        demo_seed.ensure_demo_tenant()
    log.info("ReturnGuard service ready (llm=%s, env=%s)", settings.LLM_PROVIDER, settings.ENVIRONMENT)
    try:
        yield
    finally:
        if cm is not None:
            cm.__exit__(None, None, None)


app = FastAPI(
    title="ReturnGuard",
    version="1.0.0",
    description="Autonomous Returns-Deflection & Resolution Agent",
    lifespan=lifespan,
)

app.include_router(health.router, tags=["health"])
app.include_router(resolve.router, tags=["resolve"])
app.include_router(escalations.router, tags=["escalations"])
app.include_router(resolutions.router, tags=["resolutions"])
app.include_router(metrics.router, tags=["analytics"])
app.include_router(portal.router, tags=["portal"])
app.include_router(chat.router, tags=["chat"])
app.include_router(tenants.router, tags=["tenants"])
app.include_router(platform.router, tags=["platform"])
