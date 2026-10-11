-- Calls went out with the web client on 2026-09-10: no handler ever writes
-- this table, prod held zero rows, and the model sat in models/chat.py as the
-- only reason the table existed. Dropping both together matters -- bot.py runs
-- metadata.create_all() right after migrations, which would resurrect the
-- table the moment anything still maps it.
DROP TABLE IF EXISTS calls;
