CREATE INDEX IF NOT EXISTS ix_chats_user1_started ON chats (user1_id, started_at);
CREATE INDEX IF NOT EXISTS ix_chats_user2_started ON chats (user2_id, started_at);
