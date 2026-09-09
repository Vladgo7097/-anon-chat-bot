-- Existing installations run migrations before create_all, so the table may be absent.
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'text';
ALTER TABLE IF EXISTS broadcasts ADD COLUMN IF NOT EXISTS poll_options TEXT;
