-- Migration 0002 — D-04: transactional outbox for guaranteed event emission.
-- Apply:  psql "$DATABASE_URL" -f db/migrations/0002_outbox.sql

CREATE TABLE IF NOT EXISTS outbox (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL,
    topic      TEXT NOT NULL,
    msg_key    TEXT NOT NULL,
    payload    JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(status) WHERE status = 'pending';
