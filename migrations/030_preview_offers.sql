-- Preview mode (idea #1: see photo+bio before the chat opens, both must confirm).
--
-- One row per canonical pair (user1_id < user2_id). While
-- status='requested' the pair is reserved against any other match, the
-- partial UNIQUE index is the DB-level guard against duplicate offers.
-- declined/expired rows act as pair cooldowns read by compatible_candidates.
--
-- bot.py's on_startup runs Base.metadata.create_all() BEFORE this file, so
-- every statement is IF NOT EXISTS and stays a no-op there.

CREATE TABLE IF NOT EXISTS preview_offers (
    id SERIAL PRIMARY KEY,
    user1_id BIGINT NOT NULL,
    user2_id BIGINT NOT NULL,
    consent_1 BOOLEAN NOT NULL DEFAULT FALSE,
    consent_2 BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(16) NOT NULL DEFAULT 'requested',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolved_session VARCHAR(32),
    CONSTRAINT ck_preview_canonical_pair CHECK (user1_id < user2_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_preview_active_pair
    ON preview_offers (user1_id, user2_id) WHERE (status = 'requested');

CREATE INDEX IF NOT EXISTS ix_preview_user1
    ON preview_offers (user1_id);
CREATE INDEX IF NOT EXISTS ix_preview_user2
    ON preview_offers (user2_id);
CREATE INDEX IF NOT EXISTS ix_preview_status_created
    ON preview_offers (status, created_at DESC);
