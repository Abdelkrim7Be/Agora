# Agora — deployment stack

The production profile runs Traefik as the only public ingress on TCP 443. Web, gateway, email-agent, security, Ollama, Redis, and Postgres remain private on the shared Compose network. Traefik terminates TLS and forwards same-origin UI/API traffic to the internal web proxy.

## Setup

1. Copy the example env file and fill in the values:

```
cp .env.example .env
```

Required values:
- `GATEWAY_JWT_SECRET` — at least 32 characters, random string
- `GATEWAY_JWT_TTL_MINUTES` — short-lived access token lifetime (default `15`)
- `GATEWAY_JWT_REFRESH_TTL_DAYS` — rotating refresh session lifetime (default `7`)
- `GATEWAY_JWT_SECURE_COOKIES` — keep `true` behind HTTPS; the demo overlay disables it for local HTTP
- `GATEWAY_OWNER_USERNAME` / `GATEWAY_OWNER_PASSWORD` — admin credentials
- `GATEWAY_VIEWER_USERNAME` / `GATEWAY_VIEWER_PASSWORD` — read-only credentials (optional; leave blank to skip seeding)
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — database owner config used by migrations
- `AGENT_POSTGRES_PASSWORD` — password for the restricted `agora_email_agent` runtime role
- `AGORA_TLS_CERT_FILE` / `AGORA_TLS_KEY_FILE` — host paths to a PEM certificate chain and private key
- `TRAEFIK_UID` / `TRAEFIK_GID` — numeric owner of those files (`id -u` / `id -g`)
- `AGENT_SECRET_MANAGER_URL` — HTTPS URL of the production Vault
- `AGENT_TOKEN_KEYS_FILE` / `AGENT_VAULT_TOKEN_FILE` — host paths to the ignored keyring and scoped Vault credential
- `GMAIL_OAUTH_REDIRECT_URI` / `GATEWAY_CORS_ALLOWED_ORIGINS` — the public HTTPS origin

The email-agent also needs its own `.env` at `services/email-agent/.env` (see `services/email-agent/.env.example` if present). LLM calls are local-first: agents and the security quarantine classifier run on Ollama (`qwen2.5:3b-8k` via the OpenAI-compatible endpoint); no cloud LLM key is required. Cloud providers remain available through the optional `cloud` compose profile (LiteLLM) and the explicit `dev`/`prod` LLM profiles. Compose enables `AGENT_SECURITY_ENABLED=true`, points the agent at `http://security:8001`, stores agent graph/run state in Postgres, and stores security rate limits in Redis.

Schema migrations run in a one-shot owner process. The API and poller use the `agora_email_agent` role, which is created with `NOSUPERUSER NOBYPASSRLS`; forced Postgres row-level policies bind every app-owned business query to `agora.user_id` and `agora.agent_instance_id`.

2. Put the production certificate and key at the configured paths, then start the TLS-only dry-run stack:

```
chmod 600 traefik/certs/tls.key
# Set TRAEFIK_UID and TRAEFIK_GID in .env to the key owner.
docker compose -f docker-compose.yml -f docker-compose.production.yml --profile production up --build
```

The certificate may be issued by Let's Encrypt or another trusted CA. Renewal is managed on the host; Traefik receives only read-only certificate mounts and has no Docker socket access.

### Production Gmail token vault

The production overlay refuses to start unless token encryption is enabled, the keyring secret is readable, and Vault accepts a read on the configured KV v2 path. Create these ignored files with mode `0600`:

- `secrets/agent-token-keys` — a raw high-entropy secret or a rotation keyring such as `{"active_key_id":"2026-07","keys":{"2026-07":"<secret>"}}`
- `secrets/agent-vault-token` — a narrowly scoped Vault token

Grant that Vault identity `create`, `read`, `update`, and `delete` on `secret/data/agora/gmail-tokens/*`. Tokens are envelope-encrypted before upload; decrypted files exist only in the container tmpfs and are removed after each Google client operation. Existing local token files are migrated during startup, and startup fails if migration or Vault validation fails.

Live Gmail sends are opt-in. The explicit local demo uses HTTP development ports and bypasses the production ingress. For the local demo stack that mounts `credentials.json`, uses host Ollama, and starts the poller, run:

```
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```


## Postgres backup and restore drill

Backups are host-side, custom-format `pg_dump` files created through the running Compose `postgres` service. From `infra/`, run:

```
./backup-postgres.sh
```

By default this writes ignored dumps to `infra/backups/postgres/`, validates the dump with `pg_restore --list`, updates a `*-latest.dump` symlink, and removes dumps older than `AGORA_BACKUP_RETENTION_DAYS` days. Override the destination for production with `AGORA_BACKUP_DIR=/secure/backup/path`.

Restore is destructive and intentionally requires an explicit confirmation value. For the normal database, the confirmation must match the target database name:

```
AGORA_RESTORE_CONFIRM=restore-${POSTGRES_DB:-agora} ./restore-postgres.sh backups/postgres/agora-latest.dump
```

For a non-destructive drill, restore into a disposable database instead:

```
AGORA_RESTORE_DB=agora_restore_drill \
AGORA_RESTORE_CREATE_DB=true \
AGORA_RESTORE_CONFIRM=restore-agora_restore_drill \
./restore-postgres.sh backups/postgres/agora-latest.dump
```

Keep backup files encrypted and off-host in production.

## Local vulnerability scanning

CI and local scans use the same pinned Trivy generation. From the repo root, run:

```
./infra/scan-security.sh
```

The script runs `aquasec/trivy:0.70.0` through Docker, scans dependency manifests, builds the first-party service images, and fails on fixable `HIGH` or `CRITICAL` CVEs. Override with `TRIVY_VERSION=<version>` only when updating CI at the same time. The local DB cache lives in ignored `.trivy-cache/`.

## Secret scanning

CI and local scans use the same pinned Gitleaks generation, scanning the full git history rather than the working tree (so gitignored local artifacts like `token.json` never produce noise). From the repo root, run:

```
./infra/scan-secrets.sh
```

The script runs `zricethezav/gitleaks:v8.30.1` through Docker against `.gitleaks.toml` and fails on any finding. Override with `GITLEAKS_VERSION=<version>` only when updating CI at the same time. Add allowlist exceptions to `.gitleaks.toml` with a comment explaining why, never by disabling the job.

## Usage

**Check gateway health:**
```
curl https://mail.example.com/health
```

The security service is internal-only. From inside the compose network its health endpoint is `http://security:8001/health`; it is not published on the host.

**Log in:**
```
curl -X POST https://mail.example.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "<owner>", "password": "<password>"}'
```
Returns `{"token": "..."}`.

**Trigger an email run (owner only):**
```
curl -X POST https://mail.example.com/api/agent/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"author": "sender@example.com", "to": "you@example.com", "subject": "Hello", "email_thread": "Hi there"}'
```

**Read a run status (owner or viewer):**
```
curl https://mail.example.com/api/agent/run/<run_id> \
  -H "Authorization: Bearer <token>"
```

**Approve a pending draft (owner only):**
```
curl -X POST https://mail.example.com/api/agent/run/<run_id>/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"decision": "accept"}'
```

**View audit log (owner only):**
```
curl https://mail.example.com/audit \
  -H "Authorization: Bearer <token>"
```

## Notes

- In the production profile, only host port `443` is published. The gateway, web server, Ollama, databases, and sidecars have no host port mappings.
- Traefik runs non-root with a read-only filesystem, all Linux capabilities dropped, and no Docker socket.
- The email-agent is reachable only from within the Docker network (`http://email-agent:8000`). Its port is not exposed to the host.
- The security service is reachable only from within the Docker network (`http://security:8001`). Its port is not exposed to the host.
- Compose enables inbound sanitization and tool-action authorization by setting `AGENT_SECURITY_ENABLED=true` for the email-agent.
- Phase 4 compose runs the email-agent with `AGENT_STORAGE_BACKEND=postgres` and `AGENT_RUN_REGISTRY_BACKEND=postgres`, so checkpoints, learned memory, and run listings are shared through Postgres for multi-process deployments.
- Redis backs the security service rate limiter with `SECURITY_RATELIMIT_BACKEND=redis`, so send caps are shared across security service processes.
- The `agent_data` volume is still used for per-user Gmail OAuth token files. Set `AGENT_TOKEN_ENCRYPTION_KEY` to store those tokens as Fernet-encrypted blobs at rest.
- Gmail push notifications can be enabled with `GMAIL_WEBHOOK_ENABLED=true`, `GMAIL_WEBHOOK_TOPIC`, and `GMAIL_WEBHOOK_SECRET`; the gateway route is `POST /api/agent/webhooks/gmail?token=<secret>`. The poller registers (and renews, every `GMAIL_WATCH_RENEW_HOURS`) the Gmail watch and records a per-user historyId baseline; each push is processed incrementally from that baseline, then advances it. Keep `GMAIL_POLLING_FALLBACK_ENABLED=true` until the webhook delivery path is verified.
- The gateway waits for the agent's `/health` to pass (not just for the container to start) before it comes up.
- Real Gmail sends require the explicit `docker-compose.demo.yml` overlay or an explicit `AGENT_DRY_RUN=false`; the committed default stack stays in dry-run mode.
