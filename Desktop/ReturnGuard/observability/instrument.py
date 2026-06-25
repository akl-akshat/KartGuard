"""Node instrumentation: a span + a structured log line per node (NFR-OBS-1/2)."""

from __future__ import annotations

import functools
import logging
from typing import Callable

from observability.tracing import get_tracer

log = logging.getLogger("agent.node")


def instrument(node_name: str, fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapped(state):
        request_id = state.get("request_id")
        order_id = state.get("order_id")
        with get_tracer().span(node_name, request_id=request_id, order_id=order_id):
            result = fn(state)
        outcome = (result.get("status") if isinstance(result, dict) else None) or (
            ",".join(result.keys()) if isinstance(result, dict) else "ok"
        )
        log.info("node", extra={"request_id": request_id, "order_id": order_id,
                                "node": node_name, "outcome": outcome})
        return result

    return wrapped
