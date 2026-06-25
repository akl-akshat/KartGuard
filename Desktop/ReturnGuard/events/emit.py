"""Event emission seam.

Action tools and the logger node emit through :func:`emit_event`. Offline (default),
events are appended to an in-process sink so tests can assert on them. Phase 9 registers a
real Kafka emitter via :func:`set_emitter`; nothing upstream changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_sink: list[dict[str, Any]] = []
_emitter: Callable[[str, str, dict[str, Any]], None] | None = None


def set_emitter(fn: Callable[[str, str, dict[str, Any]], None] | None) -> None:
    """Register the production emitter (Kafka). Pass ``None`` to revert to sink-only."""
    global _emitter
    _emitter = fn


def emit_event(topic: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = {"topic": topic, "key": key, "payload": payload}
    _sink.append(record)
    if _emitter is not None:
        _emitter(topic, key, payload)
    return record


def drain_sink() -> list[dict[str, Any]]:
    """Return and clear the in-process event sink (test helper)."""
    out = list(_sink)
    _sink.clear()
    return out


def peek_sink() -> list[dict[str, Any]]:
    return list(_sink)
