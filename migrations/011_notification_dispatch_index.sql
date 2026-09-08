-- The worker picks the next notification several times a second, now ordered
-- by marketing before id so bulk sends cannot delay a "match" notice.
CREATE INDEX IF NOT EXISTS ix_notification_dispatch
    ON notification_log (marketing, id)
    WHERE status IN ('queued', 'sending');
