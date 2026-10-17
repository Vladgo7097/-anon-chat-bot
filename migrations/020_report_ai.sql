-- Вердикт локальной модели (Qwen3-0.6B) по жалобе: бета-модерация.
-- Строку пишет вотчер на хосте (deploy/ai/watcher.py), читает панель
-- «Жалобы» веб-админки. PK на report_id держит вставку идемпотентной
-- при повторных проходах, CASCADE уносит вердикт вместе с жалобой.
--
-- IF NOT EXISTS -- на свежей базе таблицу уже умеет создавать
-- Base.metadata.create_all, оба пути обязаны сходиться без конфликта.
CREATE TABLE IF NOT EXISTS report_ai (
    report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
