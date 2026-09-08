ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(32);

UPDATE users
SET referral_code = replace(gen_random_uuid()::text, '-', '')
WHERE referral_code IS NULL;

ALTER TABLE users ALTER COLUMN referral_code SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code);
