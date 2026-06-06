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

```bash
cd services/<service-name>
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in real values
```
