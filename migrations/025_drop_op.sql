-- ОП вычищена 19.10.2026 по решению владельца: гейт, команды владельца
-- (/setup, /unsetup, /op, /id) и рассылка неподписанным сняты из кода.
-- Dropping both tables matters: bot.py runs metadata.create_all() right after
-- migrations, which would resurrect them the moment anything still maps them
-- (та же причина, что в 017_drop_calls.sql).
DROP TABLE IF EXISTS op_chats;
DROP TABLE IF EXISTS op_settings;
-- Недоставленные письма снесённой рассылки ушли бы голым текстом без кнопок.
DELETE FROM notification_log WHERE type = 'op_notice' AND status IN ('queued', 'sending');
