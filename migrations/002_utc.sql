DO $$
DECLARE c RECORD;
BEGIN
    FOR c IN SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = 'timestamp without time zone'
        AND table_name IN ('users', 'chats', 'chat_messages', 'reports', 'bans', 'payments', 'premium_subscriptions')
    LOOP
        EXECUTE format('ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''UTC''',
            c.table_name, c.column_name, c.column_name);
    END LOOP;
END $$;
