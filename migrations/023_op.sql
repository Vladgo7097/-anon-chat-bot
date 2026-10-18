-- Обязательная подписка (ОП): одна активная конфигурация на бота плюс
-- список ресурсов. Активность не хранится отдельным полем: enabled +
-- starts_at + ends_at вычисляются на месте, поэтому просроченная ОП
-- перестаёт блокировать сразу после рестарта, не дожидаясь планировщика.
CREATE TABLE IF NOT EXISTS op_settings (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT false,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    text TEXT,
    campaign_id INTEGER NOT NULL DEFAULT 0,
    notify_running BOOLEAN NOT NULL DEFAULT false,
    notify_cursor BIGINT NOT NULL DEFAULT 0,
    notify_done INTEGER NOT NULL DEFAULT 0,
    notify_sent INTEGER NOT NULL DEFAULT 0,
    notify_total INTEGER NOT NULL DEFAULT 0,
    checks_total INTEGER NOT NULL DEFAULT 0,
    checks_blocked INTEGER NOT NULL DEFAULT 0,
    checks_error INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ресурс принадлежит боту: чужие подписки Telegram отдаёт только
-- администратору чата, и права приходится проверять отдельно у каждого бота.
CREATE TABLE IF NOT EXISTS op_chats (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    kind VARCHAR(16) NOT NULL DEFAULT '',
    link VARCHAR(255),
    active BOOLEAN NOT NULL DEFAULT true,
    rights_ok BOOLEAN NOT NULL DEFAULT false,
    rights_error VARCHAR(64),
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bot_id, chat_id)
);
