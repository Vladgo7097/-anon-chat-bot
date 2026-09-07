-- Existing installations run migrations before create_all, so the tables may be absent.
ALTER TABLE IF EXISTS ad_campaigns ADD COLUMN IF NOT EXISTS invite_link VARCHAR(64);
ALTER TABLE IF EXISTS ad_campaigns ADD COLUMN IF NOT EXISTS joins_total INTEGER NOT NULL DEFAULT 0;
DO $$ BEGIN
    IF to_regclass('public.ad_campaigns') IS NOT NULL THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ad_campaigns_invite_link ON ad_campaigns(invite_link);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS ad_joins (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    user_id BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dedupe_key VARCHAR(160) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_ad_joins_campaign_id ON ad_joins(campaign_id);
CREATE INDEX IF NOT EXISTS ix_ad_joins_user_id ON ad_joins(user_id);
