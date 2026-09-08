-- The table is created from metadata on the first upgrade.
ALTER TABLE IF EXISTS admin_audit_log ADD COLUMN IF NOT EXISTS request_key VARCHAR(128);
DO $$ BEGIN
    IF to_regclass('public.admin_audit_log') IS NOT NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_request_key ON admin_audit_log(request_key);
    END IF;
END $$;
