#!/usr/bin/env bash
# language: bash, file: deploy/backup.sh, target: Ubuntu 22.04 / Docker Compose
# Ежедневный дамп базы с ротацией по дням.
#
# Второй машины нет: упал хост -- упали оба бота и вся история чатов. Это
# единственная защита, и она превращает «дни на восстановление» в «часы».
# Скрипт лежит в репозитории, поэтому на сервере всегда та же версия, что
# и код, который эту базу пишет.
#
# Запуск вручную:    deploy/backup.sh
# В cron:            0 4 * * * /opt/anon-chat-bot/deploy/backup.sh >> /var/log/anon-backup.log 2>&1
set -euo pipefail

CONTAINER="${CONTAINER:-anon-chat-bot-db-1}"
DB_USER="${DB_USER:-anon}"
DB_NAME="${DB_NAME:-anon_chat}"
OUT_DIR="${OUT_DIR:-/opt/backups/anon-chat}"
KEEP_DAYS="${KEEP_DAYS:-7}"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
TARGET="$OUT_DIR/anon-chat-$STAMP.sql.gz"

mkdir -p "$OUT_DIR"

# --clean --if-exists: дамп должен ложиться на пустую базу при восстановлении,
# иначе наткнётся на существующие таблицы и упадёт в самый неподходящий момент.
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists \
    | gzip > "$TARGET.part"
mv "$TARGET.part" "$TARGET"

# .part переименовывается только после полной записи: обрезанный дамп не должен
# выглядеть как готовый бэкап.
find "$OUT_DIR" -name 'anon-chat-*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "backup=$TARGET size=$(du -h "$TARGET" | cut -f1)"
