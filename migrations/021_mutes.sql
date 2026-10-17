-- Мут: наказание мягче бана. Аккаунт остаётся живым (меню, профиль,
-- покупки), но писать и искать собеседника нельзя до expires_at; выдача
-- мута завершает текущий чат. Инвариант тот же, что у банов: is_muted в
-- users ⇔ активная строка в mutes, история хранится строками.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_muted BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS mute_expires TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS mutes (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reason VARCHAR(64) NOT NULL,
    duration_hours INTEGER,
    muted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    muted_by BIGINT,
    report_id INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_mutes_user_id ON mutes(user_id);
