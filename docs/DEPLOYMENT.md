# Deployment

Agora deploys with Docker Compose. There is no Kubernetes/Helm support.
If you need it, you'd be writing it from scratch against the Compose
topology below, not adapting something that already exists.

## Prerequisites

- Docker and Docker Compose v2.
- ~8 GB free disk/RAM headroom if you're running the local LLM (Ollama pulls
  a multi-GB model).
- A Gmail (or Outlook) account if you want to exercise the mail features.
  The platform itself runs without one, but the email agent has nothing to
  do until a mailbox is connected.

## Local development

```bash
git clone <repo-url> agora
cd agora/infra
cp .env.example .env   # fill in real values, see "Required values" below
docker compose -f docker-compose.yml -f docker-compose.override.local.yml up --build
```

This is dry-run by default (`AGENT_DRY_RUN=true`; no real email is ever
sent), routes the agent and poller at a host-run Ollama via
`host.docker.internal`, and starts a `poller` container so inbound mail is
picked up automatically rather than only on a manual sync click.

First run builds four images and pulls an Ollama model; expect this to
take several minutes, not "five minutes," depending on your connection and
machine. Subsequent starts are fast.

Once containers report healthy (`docker compose ps`), open the web UI at
`http://localhost:5173` (or whatever `WEB_PORT` you set) and log in with the
owner credentials from your `.env` (`GATEWAY_OWNER_USERNAME` /
`GATEWAY_OWNER_PASSWORD`).

**Required `.env` values:**

| Variable | Purpose |
|---|---|
| `GATEWAY_JWT_SECRET` | ≥32 random characters; signs access/refresh tokens |
| `GATEWAY_JWT_TTL_MINUTES` / `GATEWAY_JWT_REFRESH_TTL_DAYS` | Token lifetimes (defaults `15` / `7`) |
| `GATEWAY_OWNER_USERNAME` / `GATEWAY_OWNER_PASSWORD` | The first admin account, seeded at startup |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Database owner credentials, used by migrations |
| `AGENT_POSTGRES_PASSWORD` | Password for the restricted `agora_email_agent` runtime role |
| `AGENT_TOKEN_ENCRYPTION_KEY` | Encrypts stored Gmail/Outlook OAuth tokens at rest; see `docs/SECURITY_MODEL.md` |

Schema migrations run once, in a dedicated one-shot container, using an
elevated database role. The long-running API and poller containers use a
separate, `NOSUPERUSER` role (`agora_email_agent`) that PostgreSQL row-level
security binds to the tenant/instance pair on every business-table query;
see `docs/SECURITY_MODEL.md` for what that isolation does and doesn't cover.

### Sending real mail locally

The default local overlay is dry-run: no message ever actually reaches
Gmail/Outlook. To exercise real sends, add the demo overlay, which mounts a
real Google OAuth `credentials.json`, sets `AGENT_DRY_RUN=false`, and adds a
`worker` process behind an opt-in job-queue profile:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.local.yml \
  -f docker-compose.demo.yml up --build
```

Only do this against a mailbox you're comfortable receiving/sending real
test mail from. There is no separate confirmation step once this overlay is
active.

## Production

```bash
chmod 600 traefik/certs/tls.key
# TRAEFIK_UID / TRAEFIK_GID in .env must match the key's owner (id -u / id -g).
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  --profile production up --build
```

Traefik is the **only** service with a published host port (443). Web,
gateway, email-agent, security, Postgres, Redis, and Ollama all stay on the
internal Compose network with no host port mapping. The agent and security
services are unreachable from outside the network even by an operator with
host access, short of exec-ing into the Compose network itself.

The production overlay refuses to start unless token encryption is enabled
and a Vault (KV v2) endpoint is reachable for Gmail OAuth token storage.
Create these two files (both git-ignored, mode `0600`):

- `secrets/agent-token-keys`: a raw secret, or a rotation keyring:
  `{"active_key_id":"2026-07","keys":{"2026-07":"<secret>"}}`
- `secrets/agent-vault-token`: a narrowly-scoped Vault token, granted
  `create`/`read`/`update`/`delete` on `secret/data/agora/gmail-tokens/*`

Tokens are envelope-encrypted before upload; decrypted copies exist only in
container tmpfs, removed after each Google API call. See
`services/email-agent/docs/token-encryption.md` for rotation and recovery.

TLS certificates may come from Let's Encrypt or any other trusted CA;
renewal is your responsibility on the host; Traefik only reads the cert
files (read-only mount, no ACME automation configured). Traefik itself runs
non-root, read-only filesystem, all Linux capabilities dropped, no Docker
socket access.

### GPU-accelerated Ollama

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Adds GPU passthrough for a containerized Ollama. Without this overlay,
Ollama in a container runs on CPU, noticeably slower for drafting. The
local-dev overlay avoids this entirely by routing to a host-run Ollama
instead (see below), which is the faster path if your host already has a
GPU driver set up outside Docker.

## LLM configuration

Every agent role (triage, quarantine classification, drafting, reasoning,
memory/style) resolves its model independently through `get_llm(role)`,
reading a named profile from `services/email-agent/config/llm.*.yaml`.
Which profile is active is controlled by `AGENT_LLM_PROFILE` (selects
`config/llm.<profile>.yaml` by convention) or an explicit
`AGENT_LLM_CONFIG_PATH` override. Compose sets the override explicitly per
overlay, so the table below reflects what's actually wired, not just what's
possible:

| Profile | Used by | Endpoint | Model |
|---|---|---|---|
| `local` | Bare host process (no explicit override) | `http://localhost:11434/v1` | `qwen2.5:3b-8k` |
| `local-docker` | Base `docker-compose.yml` (agent + Ollama both containerized) | `http://ollama:11434/v1` | `qwen2.5:3b-8k` |
| `local-host` | `docker-compose.override.local.yml` / `docker-compose.demo.yml` (agent containerized, Ollama on the host) | `http://host.docker.internal:11434/v1` | `qwen3:4b-8k` |
| `safe` | Explicit opt-in degraded mode | `http://localhost:11434/v1` | `qwen2.5:3b-8k`, tighter output cap |
| `dev` | Explicit opt-in (`AGENT_LLM_PROFILE=dev`) | Groq | `llama-3.3-70b-versatile` (+ a faster 8B model for quarantine); needs `GROQ_API_KEY` |
| `prod` | `cloud` Compose profile | `http://litellm:4000/v1` | Named aliases (`agora-triage`, `agora-draft`, ...) resolved by `infra/litellm.config.yaml`, with configured provider fallback chains |

**The `prod` profile is Agora's "bring your own enterprise model" path.**
`email-agent` never talks to OpenAI/Anthropic/etc. directly in this mode;
it calls a LiteLLM proxy over an OpenAI-compatible endpoint, and the proxy
(`infra/litellm.config.yaml`, started via `--profile cloud`) is what actually
routes to whichever real provider(s) an operator configures, including
fallback chains. Switching an existing deployment onto an enterprise-
provided model endpoint means editing `litellm.config.yaml` and enabling
this profile, with no change to `email-agent`'s own code.

**On the `local`/`local-docker` vs `local-host` model difference**: this is
the actual current configuration, not an inconsistency to paper over.
`local-host` (the profile the local-dev and demo overlays both use) was
retuned to `qwen3:4b-8k` for better instruction-following on drafts and
retouches; `local`/`local-docker`/`safe` still run the smaller
`qwen2.5:3b-8k`. Every profile intentionally points every role at the *same*
single model. Running two different models on one small GPU/CPU card
causes constant eviction thrashing between them, which was measured to turn
a two-second rewrite into a multi-minute one.

Setting up a host-run Ollama, a committed Modelfile ships for each model
the local profiles use:

```bash
# local / local-docker / safe profiles
ollama pull qwen2.5:3b
ollama create qwen2.5:3b-8k -f infra/ollama/qwen2.5-3b-8k.Modelfile

# local-host profile (used by the local-dev and demo overlays)
ollama pull qwen3:4b-instruct
ollama create qwen3:4b-8k -f infra/ollama/qwen3-4b-8k.Modelfile
```

Verify with `ollama show <model>`; `Parameters` should list the expected
`num_ctx`. `infra/ollama/ollama-service-override.conf` has additional
tuning notes (KV cache quantization, keep-alive) for running Ollama as a
long-lived host service.

## Backup and restore

Two independent mechanisms exist, covering different scopes:

**Whole-database Postgres dump** (everything: gateway users, audit log, all
agent instances), from `infra/`:

```bash
./backup-postgres.sh
```

Writes a custom-format `pg_dump` to `infra/backups/postgres/` (git-ignored),
validates it, and prunes dumps older than `AGORA_BACKUP_RETENTION_DAYS`.
Restore requires an explicit confirmation matching the target database name
(destructive by default) or a disposable-database drill:

```bash
# Destructive restore into the real database:
AGORA_RESTORE_CONFIRM=restore-${POSTGRES_DB:-agora} \
  ./restore-postgres.sh backups/postgres/agora-latest.dump

# Non-destructive drill into a scratch database:
AGORA_RESTORE_DB=agora_restore_drill AGORA_RESTORE_CREATE_DB=true \
  AGORA_RESTORE_CONFIRM=restore-agora_restore_drill \
  ./restore-postgres.sh backups/postgres/agora-latest.dump
```

Keep backup files encrypted and off-host in production: a Postgres dump
contains everything, including encrypted-token ciphertext and audit history.

This whole-database dump is the one canonical backup/restore strategy: it
already covers the email-agent's own Alembic-managed tables as a subset,
including LangGraph's own checkpoint/store tables, which live outside
Alembic (created by LangGraph's own setup path) and would be missed by a
schema-scoped backup. Verification drill: run `alembic upgrade head` on a
target database, back up, restore into a scratch database, and confirm the
core tables (`agent_runs`, `gmail_sync_state`, `email_agent_sync`,
`llm_costs`, `email_agent_instance_config`) exist with matching row counts.

## Vulnerability and secret scanning

Both run in CI on every PR and can be run locally with the same pinned
tooling:

```bash
./infra/scan-security.sh   # Trivy: dependency manifests + built images, HIGH/CRITICAL, fixable-only
./infra/scan-secrets.sh    # gitleaks: full git history, not just the working tree
```

## Observability

Prometheus scrapes `/metrics` from the agent and gateway; Grafana ships
with a starter dashboard (`infra/grafana/provisioning/`). Neither is
published on the host by default. The local-dev override republishes
Grafana on `GRAFANA_PORT` (default `3000`) for convenience; production
leaves it internal-only, reachable only from inside the Compose network.

## Verifying a deployment is working

```bash
curl https://<your-host>/health

curl -X POST https://<your-host>/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "<owner-username>", "password": "<owner-password>"}'
# -> {"token": "...", ...}

curl https://<your-host>/audit/verify -H "Authorization: Bearer <token>"
# -> {"valid": true, ...} confirms the audit hash chain is intact
```

`GET /api/agent/events` (Server-Sent Events, proxied through the gateway)
pushes run-status changes to the web UI in real time; a `heartbeat` event
on every idle tick confirms the connection itself is alive even with
nothing happening.

## Configuration reference

- Real Gmail/Outlook sends require the demo overlay or an explicit
  `AGENT_DRY_RUN=false`; the committed default stays dry-run.
- `AGENT_SECURITY_ENABLED=true` (Compose default) turns on inbound
  sanitization and outbound tool authorization; see
  `docs/SECURITY_MODEL.md`.
- Gmail push notifications (instead of polling) are opt-in; see
  `services/email-agent/docs/gmail-push-setup.md`.
- The gateway waits for the agent's own `/health` to pass (not just for the
  container process to start) before it comes up, so a slow agent boot
  doesn't produce a gateway that's up but proxying to nothing.
