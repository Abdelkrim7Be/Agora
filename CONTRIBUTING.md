# Contributing

## Workflow

We use **trunk-based development**: `main` is always deployable. All work happens on short-lived feature branches merged into `main` via PR. No `develop` or `release` branches.

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/<service>/<short-desc>` | `feat/email-agent/gmail-oauth` |
| Fix | `fix/<service>/<short-desc>` | `fix/email-agent/token-refresh` |
| Chore | `chore/<short-desc>` | `chore/update-root-gitignore` |

- Keep branch names lowercase, hyphen-separated.
- Delete branches after merge.

## Commit Messages

Prefix with the service name (or `chore` for repo-level work):

```
email-agent: add Gmail OAuth handler
email-agent: fix token refresh on 401
chore: update root .gitignore
```

Short imperative subject line. No period at the end. Body optional for non-obvious context.

## Pull Requests

- Branch off `main`, PR back into `main`.
- One logical change per PR — keep them small.
- All CI checks must pass before merge.
- At least one approval required once the second collaborator joins.
- Squash-merge to keep `main` history linear.

## Local Setup

### Python services (`email-agent`, `security`, `platform-core`)

```bash
cd services/<service-name>
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in real values
pytest tests/ -q
```

### Gateway (Java / Spring Boot)

```bash
cd services/gateway
mvn -B test
```

### Frontend (`web-react`)

```bash
cd services/web-react
npm ci
npm run dev
```

`npm run dev` starts the Vite dev server on `http://localhost:5173` and proxies
gateway paths (`/api`, `/auth`, `/audit`, `/users`, `/me`, `/agents`,
`/agent-instances`, `/mailboxes`, `/health`, `/reports`) to a gateway running
on `http://localhost:8090` — no `.env` file needed for local dev against a
gateway started the normal way (see `docs/DEPLOYMENT.md`). To point at a
gateway on a different host/port, set `VITE_GATEWAY_PROXY_TARGET` before
starting the dev server.

Other scripts: `npm run build` (production bundle), `npm run lint` (oxlint).

The frontend has no automated test suite yet — verify UI changes manually
against a running stack before opening a PR.
