-- Migration 0001 — D-03: atomic idempotency guard for audit_log.
-- Apply to an existing database:  psql "$DATABASE_URL" -f db/migrations/0001_audit_idempotency_unique.sql
--
-- De-duplicate any pre-existing rows first (keep the earliest per key), then add the
-- UNIQUE(request_id, action_type) constraint so concurrent workers cannot double-insert.

DELETE FROM audit_log a
USING audit_log b
WHERE a.id > b.id
  AND a.request_id = b.request_id
  AND a.action_type = b.action_type;

ALTER TABLE audit_log
    ADD CONSTRAINT uq_audit_request_action UNIQUE (request_id, action_type);
