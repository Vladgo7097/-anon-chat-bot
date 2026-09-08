ALTER TABLE reports ADD COLUMN IF NOT EXISTS chat_session_id VARCHAR(32);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reports_session_reporter ON reports(chat_session_id, reporter_id);
