-- Durable record of calls. Live call state stays in Redis; this table only
-- keeps what happened, never any content of the conversation.
CREATE TABLE IF NOT EXISTS calls (
    id SERIAL PRIMARY KEY,
    chat_session_id VARCHAR(32) NOT NULL,
    initiator_id BIGINT NOT NULL,
    partner_id BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'offered',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    end_reason VARCHAR(32)
);

CREATE INDEX IF NOT EXISTS ix_calls_chat_session_id ON calls (chat_session_id);
CREATE INDEX IF NOT EXISTS ix_calls_initiator_id ON calls (initiator_id);
CREATE INDEX IF NOT EXISTS ix_calls_partner_id ON calls (partner_id);
