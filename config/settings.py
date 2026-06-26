"""Central configuration for ReturnGuard.

Every threshold, limit, secret, and infrastructure endpoint is sourced here so that
nothing is hard-coded (NFR-MNT-2) and no secret is committed (NFR-SEC-1). The defaults
for the financial thresholds are the illustrative values from SRS Appendix B.

Import contract::

    from config.settings import settings
    print(settings.MAX_AUTO_REFUND_ABS, settings.MAX_ITERATIONS)  # -> 2000.0 12

The module is importable without any ``.env`` present (dev defaults apply, and the LLM
provider defaults to ``stub`` so the system can run fully offline for tests and eval).
Real deployments set ``LLM_PROVIDER=anthropic`` and supply ``LLM_API_KEY``; the runtime
secret check (``settings.validate_runtime()``) then fails fast if a secret is missing.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed, env-driven settings. Field names double as the ``.env`` keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ app
    APP_NAME: str = "returnguard"
    ENVIRONMENT: Literal["dev", "test", "ci", "prod"] = "dev"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # ------------------------------------------------------------- database
    # pgvector image; same Postgres holds domain data, vectors, and the checkpointer.
    DATABASE_URL: str = "postgresql://returnguard:returnguard@localhost:5432/returnguard"
    DB_POOL_MIN: int = 1
    DB_POOL_MAX: int = 10
    # Read-only DSN used by the MCP servers (MCP-1); falls back to DATABASE_URL.
    READ_DATABASE_URL: str = ""

    # ---------------------------------------------------------------- kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_CLIENT_ID: str = "returnguard"
    KAFKA_CONSUMER_GROUP: str = "returnguard-workers"
    TOPIC_REQUESTS: str = "returns.requests.v1"
    TOPIC_RESOLUTIONS: str = "returns.resolutions.v1"
    TOPIC_ESCALATIONS: str = "returns.escalations.v1"
    TOPIC_AUDIT: str = "returns.audit.v1"
    TOPIC_OUTCOMES: str = "returns.outcomes.v1"
    TOPIC_DLQ: str = "returns.deadletter.v1"

    # ------------------------------------------------------------------ llm
    # 'stub' is a deterministic offline model used for tests/eval (no API key needed).
    LLM_PROVIDER: Literal["anthropic", "stub"] = "stub"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "claude-sonnet-4-6"
    LLM_MODEL_FAST: str = "claude-haiku-4-5-20251001"
    LLM_TIMEOUT_S: float = 30.0  # Appendix B: per-call timeout (LLM-2, NFR-REL-3)
    LLM_MAX_RETRIES: int = 3  # Appendix B: per-call retries with backoff
    LLM_MAX_TOKENS: int = 1024

    # ----------------------------------------------------------- embeddings
    # 'stub' uses a deterministic local hashing embedder (no network) so RAG works offline.
    EMBEDDING_PROVIDER: Literal["stub", "anthropic"] = "stub"
    EMBEDDING_MODEL: str = "stub-hash-384"
    EMBEDDING_DIM: int = 384  # MUST match db/schema.sql policy_chunks.embedding vector(N)

    # ----------------------------------------------------------------- rag
    RAG_TOP_K: int = 4  # Appendix B: policy chunks retrieved (DR-RAG-2)

    # --------------------------------------------------- financial guardrails
    # SRS Appendix B — illustrative INR defaults. These are HARD limits (NFR-SAF-*).
    MAX_COUPON_PCT: float = 0.20  # max retention coupon as fraction of order value
    MAX_COUPON_ABS: float = 300.0  # absolute coupon ceiling (INR)
    MAX_AUTO_REFUND_ABS: float = 2000.0  # above this, refunds require human approval
    RISK_ESCALATION_THRESHOLD: float = 0.70  # risk score forcing escalation (FR-RSK-3)
    RISK_NUANCE_BAND: float = 0.05  # max |LLM risk adjustment| — model cannot dominate the score
    MAX_GOODWILL_CREDIT: float = 150.0  # goodwill credit ceiling (INR)
    AUTO_REFUND_RATE_LIMIT: int = 3  # max auto-refunds per customer per window
    AUTO_REFUND_RATE_WINDOW_DAYS: int = 30  # the rate-limit window

    # --------------------------------------------------------- graph control
    MAX_ITERATIONS: int = 12  # Appendix B: hard graph loop cap (CON-4, NFR-REL-2)

    # ----------------------------------------------------- cost model (§9.1)
    # Config-driven INR cost parameters for the expected-cost comparison.
    RETURN_REVERSE_LOGISTICS: float = 80.0   # reverse pickup leg
    RETURN_RESTOCKING: float = 40.0          # inspection + restocking/handling
    RETURN_MARGIN_RATE: float = 0.30         # margin as fraction of price
    RETURN_P_UNSELLABLE: float = 0.25        # Pr(returned item unsellable)
    RTO_FORWARD_COST: float = 60.0           # forward-leg dead cost for an RTO
    EXCHANGE_SHIPPING_COST: float = 90.0     # cost of an exchange shipment
    REPLACEMENT_DELTA_COST: float = 120.0    # expedited replacement delta
    COUPON_REDEMPTION_RATE: float = 0.70     # expected coupon redemption fraction
    PARTIAL_REFUND_FRACTION: float = 0.30    # default partial-refund share of value
    DEFECT_GOODWILL_DEFAULT: float = 100.0   # default goodwill sweetener on a defect

    # ----------------------------------------------------------- observability
    TRACING_ENABLED: bool = False  # toggle (D9); off by default for offline runs
    TRACING_PROVIDER: Literal["langfuse", "langsmith", "none"] = "none"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3000"
    LANGSMITH_API_KEY: str = ""

    # --------------------------------------------------------------- service
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # ------------------------------------------------------------------ clock
    # ISO date override used for return-window math. Empty -> real today(). The eval
    # harness pins this to the dataset reference date so window classification is
    # deterministic regardless of wall-clock time (FR-POL-3).
    AS_OF_DATE: str = ""

    # ------------------------------------------------------------ validators
    @field_validator("MAX_COUPON_PCT")
    @classmethod
    def _pct_in_unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("MAX_COUPON_PCT must be within [0, 1]")
        return v

    @field_validator("RISK_ESCALATION_THRESHOLD")
    @classmethod
    def _risk_in_unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("RISK_ESCALATION_THRESHOLD must be within [0, 1]")
        return v

    # --------------------------------------------------------------- helpers
    @property
    def read_database_url(self) -> str:
        """DSN for read-only MCP servers; defaults to the main DSN if unset."""
        return self.READ_DATABASE_URL or self.DATABASE_URL

    @property
    def use_stub_llm(self) -> bool:
        return self.LLM_PROVIDER == "stub"

    @property
    def as_of_date(self) -> date:
        """Effective 'today' for window math (override via AS_OF_DATE for determinism)."""
        return date.fromisoformat(self.AS_OF_DATE) if self.AS_OF_DATE else date.today()

    def validate_runtime(self) -> None:
        """Fail fast at service boot if a required runtime secret is missing.

        Importing this module never fails (tests/eval run offline on the stub).
        A real deployment calls this in the FastAPI lifespan / worker startup.
        """
        missing: list[str] = []
        if self.LLM_PROVIDER == "anthropic" and not self.LLM_API_KEY:
            missing.append("LLM_API_KEY (required when LLM_PROVIDER=anthropic)")
        if self.TRACING_ENABLED and self.TRACING_PROVIDER == "langfuse" and not (
            self.LANGFUSE_PUBLIC_KEY and self.LANGFUSE_SECRET_KEY
        ):
            missing.append("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY (tracing enabled)")
        if missing:
            raise RuntimeError(
                "Missing required configuration:\n  - " + "\n  - ".join(missing)
            )


@lru_cache
def get_settings() -> Settings:
    """Cached accessor (useful for FastAPI dependency injection)."""
    return Settings()


# Module-level singleton for ergonomic imports.
settings = get_settings()
