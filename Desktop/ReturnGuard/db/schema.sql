-- ReturnGuard domain schema (SRS §5.2). Idempotent: safe to re-run.
-- One PostgreSQL holds domain data, the pgvector index, AND the LangGraph
-- checkpointer (D7, D8). Referential integrity is enforced via FKs; audit_log is
-- insert-only at the application layer (TOOL-1, FR-LOG-2).

CREATE EXTENSION IF NOT EXISTS vector;

-- Drop in dependency order so re-running is clean (DEV/seed convenience).
DROP TABLE IF EXISTS eval_cases    CASCADE;
DROP TABLE IF EXISTS escalations   CASCADE;
DROP TABLE IF EXISTS audit_log     CASCADE;
DROP TABLE IF EXISTS resolutions   CASCADE;
DROP TABLE IF EXISTS policy_chunks CASCADE;
DROP TABLE IF EXISTS policies      CASCADE;
DROP TABLE IF EXISTS orders        CASCADE;
DROP TABLE IF EXISTS customers     CASCADE;

-- ---------------------------------------------------------------- customers
CREATE TABLE customers (
    id            TEXT PRIMARY KEY,
    name          TEXT        NOT NULL,
    signup_date   DATE        NOT NULL,
    segment       TEXT        NOT NULL,              -- new | regular | loyal | vip
    ltv           NUMERIC(12,2) NOT NULL DEFAULT 0,  -- customer lifetime value (INR)
    total_orders  INTEGER     NOT NULL DEFAULT 0,
    total_returns INTEGER     NOT NULL DEFAULT 0,
    return_rate   NUMERIC(5,4) NOT NULL DEFAULT 0,   -- total_returns / total_orders
    cod_orders    INTEGER     NOT NULL DEFAULT 0,
    cod_refusals  INTEGER     NOT NULL DEFAULT 0,
    risk_flags    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    region        TEXT        NOT NULL,
    pincode       TEXT        NOT NULL
);

-- ------------------------------------------------------------------- orders
CREATE TABLE orders (
    id                TEXT PRIMARY KEY,
    customer_id       TEXT NOT NULL REFERENCES customers(id),
    seller_id         TEXT NOT NULL,
    sku               TEXT NOT NULL,
    title             TEXT NOT NULL,
    category          TEXT NOT NULL,
    price             NUMERIC(12,2) NOT NULL,
    qty               INTEGER NOT NULL DEFAULT 1,
    payment_mode      TEXT NOT NULL CHECK (payment_mode IN ('COD', 'PREPAID')),
    order_date        DATE NOT NULL,
    dispatch_date     DATE,
    delivery_date     DATE,
    delivery_status   TEXT NOT NULL,                 -- pending|dispatched|delivered|rto
    return_window_end DATE
);
CREATE INDEX idx_orders_customer ON orders(customer_id);

-- ----------------------------------------------------------------- policies
CREATE TABLE policies (
    id                  TEXT PRIMARY KEY,
    category            TEXT NOT NULL,
    payment_mode        TEXT,                         -- NULL = applies to both modes
    rule_type           TEXT NOT NULL,                -- window | non_returnable | defect | refund_mode | exchange
    window_days         INTEGER,
    refundable          BOOLEAN NOT NULL DEFAULT TRUE,
    exchange_allowed    BOOLEAN NOT NULL DEFAULT TRUE,
    refund_mode_default TEXT NOT NULL DEFAULT 'original',  -- original | store_credit
    text                TEXT NOT NULL
);

-- ------------------------------------------------------------- policy_chunks
-- Embedding dimension MUST equal config.settings.EMBEDDING_DIM (default 384).
CREATE TABLE policy_chunks (
    id         TEXT PRIMARY KEY,
    policy_id  TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding  vector(384),
    metadata   JSONB NOT NULL DEFAULT '{}'::jsonb     -- {category, payment_mode, issue_type}
);
CREATE INDEX idx_policy_chunks_meta ON policy_chunks USING gin (metadata);

-- ------------------------------------------------------------- resolutions
CREATE TABLE resolutions (
    request_id           TEXT PRIMARY KEY,            -- idempotency key
    order_id             TEXT REFERENCES orders(id),
    customer_id          TEXT REFERENCES customers(id),
    issue_type           TEXT,
    root_cause           TEXT,
    risk_score           NUMERIC(5,4),
    risk_factors         JSONB NOT NULL DEFAULT '[]'::jsonb,
    proposed_action      JSONB,
    executed_action      JSONB,
    amount               NUMERIC(12,2),
    expected_return_cost NUMERIC(12,2),
    expected_saving      NUMERIC(12,2),
    requires_human       BOOLEAN NOT NULL DEFAULT FALSE,
    human_decision       TEXT,
    rationale            TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    trace_id             TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at          TIMESTAMPTZ
);
CREATE INDEX idx_resolutions_customer ON resolutions(customer_id);
CREATE INDEX idx_resolutions_status   ON resolutions(status);

-- ---------------------------------------------------------------- audit_log
-- APPEND-ONLY (enforced at the application layer: no UPDATE/DELETE in any code).
CREATE TABLE audit_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY,
    request_id  TEXT NOT NULL,
    action_type TEXT NOT NULL,
    amount      NUMERIC(12,2),
    actor       TEXT NOT NULL,                        -- 'agent' | 'human:<id>'
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
CREATE INDEX idx_audit_request ON audit_log(request_id);

-- --------------------------------------------------------------- escalations
CREATE TABLE escalations (
    request_id     TEXT PRIMARY KEY REFERENCES resolutions(request_id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'pending',   -- pending | decided
    recommendation JSONB NOT NULL DEFAULT '{}'::jsonb,
    assigned_to    TEXT,
    decided_at     TIMESTAMPTZ,
    decision       TEXT
);

-- ---------------------------------------------------------------- eval_cases
CREATE TABLE eval_cases (
    id                  TEXT PRIMARY KEY,
    scenario_text       TEXT NOT NULL,
    seeded_order_id     TEXT REFERENCES orders(id),
    seeded_customer_id  TEXT REFERENCES customers(id),
    expected_root_cause TEXT,
    expected_action     TEXT,
    expected_escalation BOOLEAN,
    notes               TEXT
);
