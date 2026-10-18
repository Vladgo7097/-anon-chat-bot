-- Учёт запусков /start.
--
-- Счётчик users.start_count растёт на каждый уникальный апдейт с командой
-- /start; история хранится строками start_events (first_start -- первый
-- зарегистрированный запуск человека, repeat_start -- все следующие).
-- processed_updates защищает от повторной доставки одного update_id:
-- Telegram переотправляет апдейт, если бот не успел подтвердить его, и без
-- этой таблицы повторный запуск считался бы дважды, а событий становилось бы
-- больше, чем было у пользователя запусков.
--
-- Ключ везде составной (bot_id, update_id): два бота опрашиваются
-- независимо, их нумерация апдейтов начинается заново, и совпадение номеров
-- у разных токенов -- норма, а не дубликат.
ALTER TABLE users ADD COLUMN IF NOT EXISTS start_count BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS processed_updates (
    bot_id BIGINT NOT NULL,
    update_id BIGINT NOT NULL,
    update_type TEXT NOT NULL DEFAULT '',
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, update_id)
);

CREATE TABLE IF NOT EXISTS start_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL REFERENCES users(telegram_id),
    bot_id BIGINT NOT NULL,
    update_id BIGINT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('first_start', 'repeat_start')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bot_id, update_id)
);

CREATE INDEX IF NOT EXISTS idx_start_events_created_at ON start_events (created_at);
CREATE INDEX IF NOT EXISTS idx_start_events_user_created ON start_events (telegram_user_id, created_at DESC);

-- Бэкфил истории. Таблиц не существовало, но каждая строка users означает,
-- что человек уже как минимум раз запускал бота. Без этих двух запросов
-- старые пользователи на ближайшем /start выглядели бы новыми, и владелец
-- получил бы карточку «новый пользователь» на каждого вернувшегося.
-- Синтетическое событие якорится на users.created_at, а отрицательный
-- update_id не пересекается с настоящими (Telegram нумерует их с единицы).
UPDATE users SET start_count = 1 WHERE start_count = 0;

INSERT INTO start_events (telegram_user_id, bot_id, update_id, event_type, created_at)
SELECT u.telegram_id, COALESCE(u.bot_id, 0), -u.id, 'first_start', u.created_at
FROM users AS u
WHERE NOT EXISTS (SELECT 1 FROM start_events AS e WHERE e.telegram_user_id = u.telegram_id);
