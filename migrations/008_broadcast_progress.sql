-- Existing installations run migrations before create_all, so the table may be absent.
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS progress_chat_id BIGINT;
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS progress_message_id BIGINT;
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS progress_at TIMESTAMPTZ;
