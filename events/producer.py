"""Kafka producers (aiokafka). Replaces the offline emit stub with real publishing.

Registering :func:`kafka_emitter` via ``events.emit.set_emitter`` routes every event the
nodes/tools emit to Kafka, keyed by request_id — nothing upstream changes.
"""

from __future__ import annotations

import json
from typing import Any

from config.settings import settings
from events.emit import set_emitter

_producer: Any = None


async def start_producer() -> None:
    global _producer
    from aiokafka import AIOKafkaProducer  # lazy

    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        client_id=settings.KAFKA_CLIENT_ID,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: (k or "").encode("utf-8"),
        enable_idempotence=True,
    )
    await _producer.start()
    set_emitter(_sync_emit)


async def stop_producer() -> None:
    global _producer
    set_emitter(None)
    if _producer is not None:
        await _producer.stop()
        _producer = None


def _sync_emit(topic: str, key: str, payload: dict[str, Any]) -> None:
    """Bridge the sync emit_event call sites to the async producer."""
    import anyio

    anyio.from_thread.run(_async_emit, topic, key, payload) if _in_worker_thread() else \
        anyio.run(_async_emit, topic, key, payload)


def _in_worker_thread() -> bool:
    try:
        import anyio.from_thread  # noqa: F401
        import sniffio

        sniffio.current_async_library()
        return False
    except Exception:
        return True


async def _async_emit(topic: str, key: str, payload: dict[str, Any]) -> None:
    if _producer is not None:
        await _producer.send_and_wait(topic, value=payload, key=key)


def kafka_emitter(topic: str, key: str, payload: dict[str, Any]) -> None:
    _sync_emit(topic, key, payload)


async def publish_request(payload: dict[str, Any]) -> None:
    """Publish a returns.requests.v1 message (used by demo/load tools)."""
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: (k or "").encode("utf-8"),
    )
    await producer.start()
    try:
        await producer.send_and_wait(settings.TOPIC_REQUESTS, value=payload, key=payload.get("request_id"))
    finally:
        await producer.stop()
