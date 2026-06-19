"""Checkpointer factory.

Production MUST use the **Postgres** checkpointer so a paused escalation survives a
process restart (CON-3, FR-HIL-5, NFR-REL-1). This module deliberately contains no
in-memory saver — the offline/test path injects an in-memory saver from the test code,
so the production ``agent/`` package never references one (a pitfall guard checks this).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from config.settings import settings


@contextmanager
def postgres_checkpointer(dsn: str | None = None) -> Iterator[Any]:
    """Yield a set-up Postgres checkpointer (creates its tables on first use).

    Use inside the FastAPI lifespan / worker startup::

        with postgres_checkpointer() as cp:
            graph = build_graph(checkpointer=cp)
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    dsn = dsn or settings.DATABASE_URL
    with PostgresSaver.from_conn_string(dsn) as cp:
        cp.setup()
        yield cp
