-- Older installations created this table through SQLAlchemy rather than a
-- migration. Create its current shape before migrations that add indexes.
CREATE TABLE IF NOT EXISTS notification_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    type VARCHAR(64) NOT NULL,
    dedupe_key VARCHAR(160) NOT NULL UNIQUE,
    text TEXT NOT NULL,
    callback_data VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    last_error VARCHAR(64),
    marketing BOOLEAN NOT NULL DEFAULT FALSE,
    broadcast_id INTEGER
);

CREATE INDEX IF NOT EXISTS ix_notification_log_user_id ON notification_log (user_id);
CREATE INDEX IF NOT EXISTS ix_notification_log_status ON notification_log (status);
CREATE INDEX IF NOT EXISTS ix_notification_log_broadcast_id ON notification_log (broadcast_id);
