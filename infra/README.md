# Agora — local stack

Five services on a shared network: **postgres** (durable agent/gateway data), **redis** (shared security rate limits), **security** (internal policy/sanitize service), **email-agent** (internal), and **gateway** (public ingress on port 8080). The email-agent and security ports are never published to the host — all external traffic goes through the gateway.

## Setup

1. Copy the example env file and fill in the values:

```
cp .env.example .env
```

Required values:
- `GROQ_API_KEY` — used by the security service quarantined classifier
- `GATEWAY_JWT_SECRET` — at least 32 characters, random string
- `GATEWAY_OWNER_USERNAME` / `GATEWAY_OWNER_PASSWORD` — admin credentials
- `GATEWAY_VIEWER_USERNAME` / `GATEWAY_VIEWER_PASSWORD` — read-only credentials (optional; leave blank to skip seeding)
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — database config

The email-agent also needs its own `.env` at `services/email-agent/.env` (see `services/email-agent/.env.example` if present). At minimum it needs `GROQ_API_KEY` for its agent LLM calls. Compose enables `AGENT_SECURITY_ENABLED=true`, points the agent at `http://security:8001`, stores agent graph/run state in Postgres, and stores security rate limits in Redis.

2. Start the stack:

```
docker compose up --build
```

## Usage

**Check gateway health:**
```
curl http://localhost:8080/health
```

The security service is internal-only. From inside the compose network its health endpoint is `http://security:8001/health`; it is not published on the host.

**Log in:**
```
curl -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "<owner>", "password": "<password>"}'
```
Returns `{"token": "..."}`.

**Trigger an email run (owner only):**
```
curl -X POST http://localhost:8080/api/agent/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"author": "sender@example.com", "to": "you@example.com", "subject": "Hello", "email_thread": "Hi there"}'
```

**Read a run status (owner or viewer):**
```
curl http://localhost:8080/api/agent/run/<run_id> \
  -H "Authorization: Bearer <token>"
```

**Approve a pending draft (owner only):**
```
curl -X POST http://localhost:8080/api/agent/run/<run_id>/approve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"decision": "accept"}'
```

**View audit log (owner only):**
```
curl http://localhost:8080/audit \
  -H "Authorization: Bearer <token>"
```

## Notes

- The email-agent is reachable only from within the Docker network (`http://email-agent:8000`). Its port is not exposed to the host.
- The security service is reachable only from within the Docker network (`http://security:8001`). Its port is not exposed to the host.
- Compose enables inbound sanitization and tool-action authorization by setting `AGENT_SECURITY_ENABLED=true` for the email-agent.
- Phase 4 compose runs the email-agent with `AGENT_STORAGE_BACKEND=postgres` and `AGENT_RUN_REGISTRY_BACKEND=postgres`, so checkpoints, learned memory, and run listings are shared through Postgres for multi-process deployments.
- Redis backs the security service rate limiter with `SECURITY_RATELIMIT_BACKEND=redis`, so send caps are shared across security service processes.
- The `agent_data` volume is still used for per-user Gmail OAuth token files. Set `AGENT_TOKEN_ENCRYPTION_KEY` to store those tokens as Fernet-encrypted blobs at rest.
- Gmail push notifications can be enabled with `GMAIL_WEBHOOK_ENABLED=true`, `GMAIL_WEBHOOK_TOPIC`, and `GMAIL_WEBHOOK_SECRET`; the gateway route is `POST /api/agent/webhooks/gmail?token=<secret>`. The poller registers (and renews, every `GMAIL_WATCH_RENEW_HOURS`) the Gmail watch and records a per-user historyId baseline; each push is processed incrementally from that baseline, then advances it. Keep `GMAIL_POLLING_FALLBACK_ENABLED=true` until the webhook delivery path is verified.
- The gateway waits for the agent's `/health` to pass (not just for the container to start) before it comes up.
- Set `AGENT_DRY_RUN=false` in `services/email-agent/.env` to enable real Gmail sends (requires OAuth credentials).
