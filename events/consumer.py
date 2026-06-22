"""Kafka event worker (FR-EVT-1/3, DR-EVT-1, NFR-PERF-2).

``handle_message`` is the pure, offline-testable core: validate → (idempotently) invoke the
graph → emit the resolution. Malformed messages classify as ``dead_letter`` (the loop routes
them to the DLQ) without crashing. The async ``consume`` loop runs N stateless workers in a
consumer group; idempotency is enforced by request_id so a redelivery never double-executes.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from agent.runner import run_config
from agent.state import initial_state
from config.settings import settings
from db.repository import Repository
from events.schemas import RequestEvent

log = logging.getLogger("events.consumer")

_DONE_STATES = {"resolved", "denied", "info", "escalated", "failed", "not_found"}


def handle_message(payload: dict[str, Any], graph, repo: Repository) -> dict[str, Any]:
    """Process one ingest message. Returns a status dict; never raises on bad input."""
    try:
        ev = RequestEvent(**payload)
    except ValidationError as exc:
        return {"status": "dead_letter", "reason": str(exc)}

    # Idempotency (AC-5): a request already resolved or already escalated is skipped.
    existing = repo.get_resolution(ev.request_id)
    if existing and existing.get("status") in _DONE_STATES:
        return {"status": "duplicate", "request_id": ev.request_id}
    if repo.get_escalation(ev.request_id):
        return {"status": "duplicate_pending", "request_id": ev.request_id}

    state = initial_state(ev.request_id, ev.issue_text, "kafka_event", ev.order_id, ev.customer_id)
    final = graph.invoke(state, run_config(ev.request_id))
    return {"status": "processed", "request_id": ev.request_id,
            "resolution_status": final.get("status"),
            "action_type": (final.get("proposed_action") or {}).get("action_type")}


async def consume(graph) -> None:  # pragma: no cover - requires a live broker
    """Run a stateless consumer-group worker over the ingest topic."""
    import json

    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    from agent.deps import get_deps

    consumer = AIOKafkaConsumer(
        settings.TOPIC_REQUESTS,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.KAFKA_CONSUMER_GROUP,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
    )
    dlq = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
    )
    repo = get_deps().repo
    await consumer.start()
    await dlq.start()
    try:
        async for msg in consumer:
            try:
                result = handle_message(msg.value, graph, repo)
            except Exception as exc:  # noqa: BLE001 - never let one message kill the worker
                result = {"status": "dead_letter", "reason": f"handler error: {exc}"}
            if result["status"] == "dead_letter":
                await dlq.send_and_wait(settings.TOPIC_DLQ,
                                        value={"raw": msg.value, "reason": result["reason"]})
                log.warning("dead-lettered message: %s", result["reason"])
            else:
                log.info("processed %s -> %s", result.get("request_id"), result["status"])
    finally:
        await consumer.stop()
        await dlq.stop()


def main() -> None:  # pragma: no cover
    """Entry point: build a Postgres-checkpointed graph and consume."""
    import anyio

    from agent.checkpointer import postgres_checkpointer
    from agent.graph import build_graph
    from observability.logging import configure_logging

    configure_logging()
    with postgres_checkpointer() as cp:
        graph = build_graph(checkpointer=cp)
        anyio.run(consume, graph)


if __name__ == "__main__":
    main()
