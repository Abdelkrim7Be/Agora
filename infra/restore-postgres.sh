#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${AGORA_ENV_FILE:-${ROOT}/infra/.env}"
COMPOSE_FILE="${AGORA_COMPOSE_FILE:-${ROOT}/infra/docker-compose.yml}"
PROJECT_DIR="${AGORA_COMPOSE_PROJECT_DIR:-${ROOT}/infra}"
BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
  printf 'usage: %s <backup.dump>\n' "$0" >&2
  exit 64
fi

if [ ! -f "$BACKUP_FILE" ]; then
  printf 'backup file not found: %s\n' "$BACKUP_FILE" >&2
  exit 66
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

POSTGRES_DB="${POSTGRES_DB:-agora}"
POSTGRES_USER="${POSTGRES_USER:-agora}"
TARGET_DB="${AGORA_RESTORE_DB:-$POSTGRES_DB}"
CREATE_DB="${AGORA_RESTORE_CREATE_DB:-false}"
CONFIRM="${AGORA_RESTORE_CONFIRM:-}"

case "$TARGET_DB" in
  ''|*[!A-Za-z0-9_]*)
    printf 'invalid restore database name: %s\n' "$TARGET_DB" >&2
    exit 65
    ;;
esac

docker compose \
  --project-directory "$PROJECT_DIR" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  pg_restore --list < "$BACKUP_FILE" >/dev/null

if [ "$CONFIRM" != "restore-${TARGET_DB}" ]; then
  printf 'refusing restore: set AGORA_RESTORE_CONFIRM=restore-%s to overwrite database %s\n' "$TARGET_DB" "$TARGET_DB" >&2
  exit 78
fi

if [ "$CREATE_DB" = "true" ]; then
  docker compose \
    --project-directory "$PROJECT_DIR" \
    -f "$COMPOSE_FILE" \
    exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE);" \
      -c "CREATE DATABASE \"$TARGET_DB\";"
fi

cat "$BACKUP_FILE" | docker compose \
  --project-directory "$PROJECT_DIR" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$TARGET_DB" --clean --if-exists --no-owner --no-acl
