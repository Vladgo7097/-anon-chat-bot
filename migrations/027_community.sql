-- Community features: progressive moderation, friendships and the
-- ten-minute "return to the previous partner" window.
--
-- user_warnings keeps one row per confirmed report plus manual warnings.
-- Expired warnings remain for the audit trail but stop counting.
-- report_snapshots is the durable copy of the relay buffer, created only
-- after an explicit report; the maintenance worker purges it after 30 days.
-- friendships stores one row per pair in ascending-id order.
-- reconnect_offers stores one row per ended chat session.

CREATE TABLE IF NOT EXISTS user_warnings (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    report_id INTEGER,
    reason VARCHAR(255) NOT NULL DEFAULT '',
    issued_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_user_warnings_report UNIQUE (report_id)
);
CREATE INDEX IF NOT EXISTS ix_user_warnings_user_id ON user_warnings (user_id);
CREATE INDEX IF NOT EXISTS ix_user_warnings_user_expires
    ON user_warnings (user_id, expires_at);

CREATE TABLE IF NOT EXISTS report_snapshots (
    id SERIAL PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'text',
    file_id VARCHAR(512),
    text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_report_snapshots_report
    ON report_snapshots (report_id);
CREATE INDEX IF NOT EXISTS ix_report_snapshots_expires
    ON report_snapshots (expires_at);

CREATE TABLE IF NOT EXISTS friendships (
    id SERIAL PRIMARY KEY,
    user1_id BIGINT NOT NULL,
    user2_id BIGINT NOT NULL,
    requested_by BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    session_id VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_friendship_order CHECK (user1_id < user2_id),
    CONSTRAINT uq_friendship_pair UNIQUE (user1_id, user2_id)
);
CREATE INDEX IF NOT EXISTS ix_friendships_user1_status
    ON friendships (user1_id, status);
CREATE INDEX IF NOT EXISTS ix_friendships_user2_status
    ON friendships (user2_id, status);

CREATE TABLE IF NOT EXISTS reconnect_offers (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    user1_id BIGINT NOT NULL,
    user2_id BIGINT NOT NULL,
    user1_wants BOOLEAN NOT NULL DEFAULT false,
    user2_wants BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_reconnect_offers_session UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS ix_reconnect_offers_expires
    ON reconnect_offers (expires_at);
