-- Idea collection: the "Suggest an idea" menu button writes here instead of
-- vanishing. One table, forward-only: statuses live as short strings so the
-- owner panel and cards never need a join to show a state.
--
-- reviewed_at/reviewed_by mark the transition OUT of "new" (the first touch
-- by an admin); admin_reply carries the public answer the author sees.

CREATE TABLE IF NOT EXISTS idea_suggestions (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    text TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'new',
    bot_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by BIGINT,
    admin_reply TEXT,
    admin_reply_at TIMESTAMPTZ,
    duplicate_of INTEGER
);

CREATE INDEX IF NOT EXISTS ix_ideas_user_created
    ON idea_suggestions (telegram_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ideas_status_created
    ON idea_suggestions (status, created_at DESC);
