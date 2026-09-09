-- Per-recipient poll snapshot: a broadcast poll is sent as a SEPARATE Poll
-- object to each user's own chat, so Telegram never aggregates votes across
-- recipients on its own. This table holds the latest vote-count snapshot
-- Telegram reports for each individual poll_id; the app sums them per
-- broadcast_id to get a campaign-wide tally.
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id VARCHAR(64) PRIMARY KEY,
    broadcast_id INTEGER NOT NULL,
    counts TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_poll_votes_broadcast_id ON poll_votes (broadcast_id);
