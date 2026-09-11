-- NULL means the user has never picked a language yet -- step_for() reads
-- that as "show the LANGUAGE onboarding step first". Existing users (all
-- onboarded before this feature existed) are backfilled to 'ru' directly
-- so they are not sent back through onboarding.
ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(2);
UPDATE users SET language = 'ru' WHERE language IS NULL AND onboarding_completed = true;
