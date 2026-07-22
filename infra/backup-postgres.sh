#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${AGORA_ENV_FILE:-${ROOT}/infra/.env}"
BACKUP_DIR="${AGORA_BACKUP_DIR:-${ROOT}/infra/backups/postgres}"
RETENTION_DAYS="${AGORA_BACKUP_RETENTION_DAYS:-14}"
COMPOSE_FILE="${AGORA_COMPOSE_FILE:-${ROOT}/infra/docker-compose.yml}"
PROJECT_DIR="${AGORA_COMPOSE_PROJECT_DIR:-${ROOT}/infra}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

POSTGRES_DB="${POSTGRES_DB:-agora}"
POSTGRES_USER="${POSTGRES_USER:-agora}"
case "$BACKUP_DIR" in
  /*) ;;
  *) BACKUP_DIR="${ROOT}/infra/${BACKUP_DIR#./}" ;;
esac
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/${POSTGRES_DB}-${STAMP}.dump"
LATEST="${BACKUP_DIR}/${POSTGRES_DB}-latest.dump"

mkdir -p "$BACKUP_DIR"

# Custom format keeps restores selective and validates through pg_restore --list.
docker compose \
  --project-directory "$PROJECT_DIR" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl > "$OUT"

docker compose \
  --project-directory "$PROJECT_DIR" \
  -f "$COMPOSE_FILE" \
  exec -T postgres \
  pg_restore --list < "$OUT" >/dev/null

ln -sfn "$(basename "$OUT")" "$LATEST"

if [ "$RETENTION_DAYS" -gt 0 ]; then
  find "$BACKUP_DIR" -type f -name "${POSTGRES_DB}-*.dump" -mtime "+$RETENTION_DAYS" -print -delete
fi

printf '%s\n' "$OUT"
