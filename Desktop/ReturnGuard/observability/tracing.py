"""Tracing seam (NFR-OBS-1, D9).

Every node, LLM call, and tool call opens a span correlated by request_id. The default is a
no-op tracer (zero overhead offline); ``TRACING_ENABLED=true`` selects Langfuse/LangSmith.
An in-memory tracer is provided for tests to assert the full trajectory was spanned.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from config.settings import settings


class Tracer:
    @contextmanager
    def span(self, name: str, request_id: str | None = None, **attrs: Any) -> Iterator[None]:
        yield


class NoopTracer(Tracer):
    pass


@dataclass
class InMemoryTracer(Tracer):
    """Records spans for assertions in tests."""

    spans: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, request_id: str | None = None, **attrs: Any) -> Iterator[None]:
        self.spans.append({"name": name, "request_id": request_id, **attrs})
        yield

    def names(self) -> set[str]:
        return {s["name"] for s in self.spans}


class LangfuseTracer(Tracer):  # pragma: no cover - requires Langfuse + network
    def __init__(self) -> None:
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )

    @contextmanager
    def span(self, name: str, request_id: str | None = None, **attrs: Any) -> Iterator[None]:
        span = self._client.span(name=name, metadata={"request_id": request_id, **attrs})
        try:
            yield
        finally:
            span.end()


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        if settings.TRACING_ENABLED and settings.TRACING_PROVIDER == "langfuse":
            _tracer = LangfuseTracer()
        else:
            _tracer = NoopTracer()
    return _tracer


def set_tracer(tracer: Tracer | None) -> None:
    global _tracer
    _tracer = tracer
