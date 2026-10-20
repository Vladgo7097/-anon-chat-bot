-- ОП v2: система обязательной подписки по ТЗ владельца от 19.10.2026.
-- Старое ОП (023/024) снято миграцией 025; здесь -- новая схема: ресурсы,
-- правила с группами AND/OR, настраиваемые сообщения, исключения, аудит и
-- журнал ошибок. Всё скоупится по bot_id: площадки две, и getChatMember
-- для чужих подписок валиден только у бота-администратора чата.
-- Сроки (глобальные, у ресурсов и правил) считаются на месте, без
-- планировщика: рестарт не может оставить просроченное требование включённым.

CREATE TABLE IF NOT EXISTS op_settings (
    bot_id BIGINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT false,
    paused BOOLEAN NOT NULL DEFAULT false,
    check_after_registration BOOLEAN NOT NULL DEFAULT true,
    check_each_action BOOLEAN NOT NULL DEFAULT true,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    timezone TEXT NOT NULL DEFAULT 'Europe/Kyiv',
    btn_join TEXT,
    btn_check TEXT,
    btn_back TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ресурс: join_url нужен пользователю для вступления, chat_id -- для
-- getChatMember. Ссылку и идентификатор путать нельзя. setup_status --
-- состояние подключения (pending/verified/error), last_error -- причина
-- последней проблемы для диагностики в панели.
CREATE TABLE IF NOT EXISTS op_targets (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT 'channel',
    visibility TEXT NOT NULL DEFAULT 'public',
    join_url TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true,
    setup_status TEXT NOT NULL DEFAULT 'pending',
    last_checked_at TIMESTAMPTZ,
    last_error TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bot_id, chat_id)
);
CREATE INDEX IF NOT EXISTS ix_op_targets_bot_active ON op_targets (bot_id, is_active);

-- Правило: feature_key='*' -- глобальное требование, иначе -- требование
-- конкретной функции. Условия функций накладываются поверх глобальных.
CREATE TABLE IF NOT EXISTS op_policies (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    feature_key TEXT NOT NULL DEFAULT '*',
    title TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT true,
    position INTEGER NOT NULL DEFAULT 0,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_op_policies_bot_feature ON op_policies (bot_id, feature_key);

-- Группа условий: mode='and' -- обязательны все ресурсы группы, 'or' --
-- достаточно любого. Правило выполнено, когда выполнены все его группы.
CREATE TABLE IF NOT EXISTS op_rule_groups (
    id SERIAL PRIMARY KEY,
    policy_id INTEGER NOT NULL REFERENCES op_policies(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'and',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS op_rule_targets (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES op_rule_groups(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES op_targets(id) ON DELETE CASCADE,
    UNIQUE (group_id, target_id)
);

-- Шаблоны сообщений: scope='global' -- основной экран, 'feature' -- текст
-- конкретной функции (feature_key), 'error' -- техническая ошибка проверки.
CREATE TABLE IF NOT EXISTS op_message_templates (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    feature_key TEXT,
    text TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'html',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_op_message_templates_scope
    ON op_message_templates (bot_id, scope, COALESCE(feature_key, ''));

CREATE TABLE IF NOT EXISTS op_exemptions (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    until TIMESTAMPTZ,
    note TEXT NOT NULL DEFAULT '',
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bot_id, user_id)
);

CREATE TABLE IF NOT EXISTS op_audit_log (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    actor_id BIGINT,
    action TEXT NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_op_audit_bot_time ON op_audit_log (bot_id, created_at);

CREATE TABLE IF NOT EXISTS op_error_log (
    id SERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL,
    target_id INTEGER,
    user_id BIGINT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_op_errors_bot_time ON op_error_log (bot_id, created_at);
