-- The second bot shares this database, so a message sent outside the
-- dispatcher (worker notices, a search timer restored after a restart) has to
-- leave through the bot that user actually talks to -- the other token gets a
-- 403 and the notice is lost. NULL until their first update is seen, which
-- routes to the primary bot.
ALTER TABLE users ADD COLUMN IF NOT EXISTS bot_id BIGINT;
