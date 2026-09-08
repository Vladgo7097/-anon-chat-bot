-- Existing installations run migrations before create_all, so the tables may be absent.
CREATE TABLE IF NOT EXISTS ad_campaigns (
    id SERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    channel_username VARCHAR(32) NOT NULL,
    title VARCHAR(64) NOT NULL,
    subtitle VARCHAR(64) NOT NULL DEFAULT '',
    body VARCHAR(400) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    daily_limit INTEGER NOT NULL DEFAULT 0,
    total_limit INTEGER NOT NULL DEFAULT 0,
    impressions_total INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ad_impressions (
    id SERIAL PRIMARY KEY,
    campaign_id INTEGER NOT NULL,
    user_id BIGINT NOT NULL,
    shown_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ad_impressions_campaign_id ON ad_impressions(campaign_id);
CREATE INDEX IF NOT EXISTS ix_ad_impressions_user_id ON ad_impressions(user_id);
CREATE INDEX IF NOT EXISTS ix_ad_impressions_shown_at ON ad_impressions(shown_at);
-- Cheap "shown today for this campaign" counts without scanning the whole table.
CREATE INDEX IF NOT EXISTS ix_ad_impressions_campaign_shown ON ad_impressions(campaign_id, shown_at);
