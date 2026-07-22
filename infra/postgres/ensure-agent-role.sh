#!/bin/sh
set -eu

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${AGENT_POSTGRES_PASSWORD:?AGENT_POSTGRES_PASSWORD is required}"

psql \
  --host postgres \
  --username "${POSTGRES_USER:-agora}" \
  --dbname "${POSTGRES_DB:-agora}" \
  --set ON_ERROR_STOP=1 \
  --set app_password="${AGENT_POSTGRES_PASSWORD}" \
  --set app_database="${POSTGRES_DB:-agora}" <<'SQL'
SELECT format(
    'CREATE ROLE agora_email_agent LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent'
) \gexec

SELECT format(
    'ALTER ROLE agora_email_agent PASSWORD %L NOSUPERUSER NOBYPASSRLS',
    :'app_password'
) \gexec

GRANT CONNECT ON DATABASE :"app_database" TO agora_email_agent;
GRANT USAGE ON SCHEMA public TO agora_email_agent;
SQL
