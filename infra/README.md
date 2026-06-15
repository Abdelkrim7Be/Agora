# Agora — local stack

Three services on a shared network: **postgres** (data), **email-agent** (internal), **gateway** (public ingress on port 8080). The email-agent port is never published to the host — all traffic goes through the gateway.

## Setup

1. Copy the example env file and fill in the values:

```
cp .env.example .env
```

Required values:
- `GATEWAY_JWT_SECRET` — at least 32 characters, random string
- `GATEWAY_OWNER_USERNAME` / `GATEWAY_OWNER_PASSWORD` — admin credentials
- `GATEWAY_VIEWER_USERNAME` / `GATEWAY_VIEWER_PASSWORD` — read-only credentials (optional; leave blank to skip seeding)
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — database config

The email-agent also needs its own `.env` at `services/email-agent/.env` (see `services/email-agent/.env.example` if present). At minimum it needs `GROQ_API_KEY`.

2. Start the stack:

```
docker compose up --build
```

## Usage

**Check gateway health:**
```
curl http://localhost:8080/health
```

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
- SQLite state files (`checkpoints.db`, `store.db`) are written to `/app/data`, backed by the named `agent_data` volume, so paused runs and learned memory survive container restarts.
- The gateway waits for the agent's `/health` to pass (not just for the container to start) before it comes up.
- Set `AGENT_DRY_RUN=false` in `services/email-agent/.env` to enable real Gmail sends (requires OAuth credentials).
