"""Shared bootstrap for the read-only MCP servers.

Builds a data-access backend (Postgres in production, in-memory fallback for standalone
demos) and a structured logger so each tool call is observable (P5).
"""

from __future__ import annotations

import logging

from config.settings import settings
from tools.data_access import LocalDataAccess


def build_data_access() -> LocalDataAccess:
    """Postgres-backed access in production; in-memory fallback if the DB is unreachable."""
    try:
        from db.repository import PostgresRepository

        repo = PostgresRepository(settings.read_database_url)
        repo.get_order("__probe__")  # connectivity probe
        return LocalDataAccess(repo)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("mcp").warning(
            "Postgres unavailable (%s); MCP server falling back to in-memory dataset.",
            exc.__class__.__name__,
        )
        return LocalDataAccess()


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return logging.getLogger(name)
