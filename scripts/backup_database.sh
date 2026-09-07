#!/bin/sh
set -eu

backup_dir=/opt/anon-chat-backups
database_container=anon-chat-bot-db-1
verification_database=anon_chat_backup_verify
stamp=$(date -u +%Y%m%dT%H%M%SZ)
temporary_file="$backup_dir/.anon-chat-$stamp.dump.tmp"
final_file="$backup_dir/anon-chat-$stamp.dump"

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
cleanup() {
    docker exec "$database_container" dropdb -U anon --if-exists --force "$verification_database" >/dev/null 2>&1 || true
    rm -f "$temporary_file"
}
trap cleanup EXIT

docker exec "$database_container" pg_dump -U anon -d anon_chat -Fc > "$temporary_file"
test -s "$temporary_file"
docker exec -i "$database_container" pg_restore --list < "$temporary_file" >/dev/null
docker exec "$database_container" dropdb -U anon --if-exists --force "$verification_database"
docker exec "$database_container" createdb -U anon "$verification_database"
docker exec -i "$database_container" pg_restore -U anon -d "$verification_database" < "$temporary_file"
docker exec "$database_container" psql -U anon -d "$verification_database" -Atqc \
    "SELECT count(*) FROM users" >/dev/null
docker exec "$database_container" dropdb -U anon --if-exists --force "$verification_database"
chmod 600 "$temporary_file"
mv "$temporary_file" "$final_file"
find "$backup_dir" -type f -name 'anon-chat-*.dump' -mtime +14 -delete
trap - EXIT
