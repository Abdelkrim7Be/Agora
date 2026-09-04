# Agent contract

Agora's core (gateway + web UI) is agent-agnostic: it doesn't know anything
email-specific. An **agent type** is any service that registers with the
gateway and can be instantiated as one or more **agent instances** — separate
mailboxes, separate tenants, separate configuration, all isolated from each
other. `email-agent` is the one agent type that ships and is exercised today.

This document is normative for anyone building a second agent type.
MUST / MUST NOT / SHOULD carry their usual meaning. Every MUST has a
corresponding check in the conformance suite (below), so "does my service
satisfy the contract" is a command you can run, not a judgment call.

**Current status: one agent type (`email-agent`) implements this contract.**
The contract and conformance suite exist and are exercised by `email-agent`
itself, but no second, independent agent type has been built against it yet —
treat the multi-agent-type story as a real, tested mechanism with a sample
size of one, not as a proven ecosystem.

**Contract version: 1.**

---

## 1. Health endpoint

The service MUST expose `GET <base_path>/health` returning HTTP 200 with a
JSON body containing at least `{"status": "ok"}`.

The gateway probes this endpoint to derive the `service_health` field shown
on the Agent Instances overview. Unhealthy or unreachable services are
surfaced but not automatically disabled.

---

## 2. Manifest — the agent declares itself

The service MUST expose `GET /manifest`, returning the agent type's own
description:

| Field | Requirement |
|---|---|
| `contract_version` | Integer. MUST equal the version this document declares |
| `id` | Unique machine identifier (e.g. `email-agent`). MUST match the registered type id |
| `display_name` | Human-readable label. MUST be non-empty |
| `description` | One sentence. MUST be non-empty |
| `capabilities` | Non-empty list of named capabilities (e.g. `["email_triage", "drafts"]`) |
| `settings_schema` | Ordered list of instance settings sections the platform renders |

Each `settings_schema` entry:

| Field | Requirement |
|---|---|
| `key` | Stable machine key, unique within the agent type. MUST be non-empty |
| `label` | Short UI label. MUST be non-empty |
| `description` | One-sentence purpose |
| `path` | Workspace-relative path. MUST start with a single `/` |

The manifest MUST be:

- **Unauthenticated.** The gateway reads it upstream, before any JWT exists.
- **Tenant-independent.** It describes the *type*, not one tenant's instance.
  The gateway caches it per type and would otherwise serve one tenant's
  manifest to another.

The manifest MUST NOT declare `base_path`, `health_path`, or `manifest_path`.
**Routing is the platform's decision** — an agent that could name its own
proxy prefix could claim another agent's traffic. Likewise `color` and `icon`
belong to the design system, not the agent.

### How the gateway resolves it

`gateway.agent-types` in `application.yml` is the registration: it assigns
routing and is the offline fallback. At read time the gateway overlays the
manifest onto it, so the agent's own declaration wins for the fields the
agent owns. The configured entry is served unchanged when the agent is
unreachable, hasn't shipped `/manifest`, declares a `contract_version` this
gateway doesn't understand, or the manifest's `id` doesn't match the
registered type. Individual `settings_schema` entries with a non-relative
`path` are dropped — an agent that is down must never blank out the settings
navigation.

---

## 3. Identity headers

The gateway stamps three headers on every proxied request and expects the
agent service to honour them. The service MUST NOT trust any client-supplied
values for these headers — the gateway strips them from the inbound request
before forwarding, so a value the agent sees always came from the gateway.

| Header | Description |
|---|---|
| `X-Agora-User` | Authenticated platform username; maps to tenant/user scope |
| `X-Agora-Agent-Instance` | Instance id; scopes all state (runs, memory, tokens, costs, categories) |
| `X-Agora-Instance-Role` | Effective per-instance role (`owner`, `approver`, `viewer`) resolved by the gateway |

The service MUST:
- Use `X-Agora-User` to scope all persisted state (runs, memory, etc.) to the
  tenant.
- Use `X-Agora-Agent-Instance` to scope state to the instance so two
  instances' data never mixes.
- Return an empty result — never another instance's data — for an instance
  id it has never seen.

The service SHOULD use `X-Agora-Instance-Role` for defense-in-depth
authorization checks on sensitive actions (approve, reject) when the header
is present — the gateway already enforces this before the request arrives,
but a second check on the agent side means the agent isn't relying solely on
the network boundary.

---

## 4. Instance-scoped state

Every piece of persisted state the service produces MUST carry both
`user_id` and `agent_instance_id`:

- Run records / audit data
- Memory / learned preferences
- OAuth tokens / mailbox connections
- Cost records
- Any domain-specific data (drafts, categories, templates, sync status, etc.)

The default instance MUST behave identically to a single-instance
deployment. Introducing a second instance MUST produce fully isolated state
with no cross-instance leakage — this is the property the conformance suite
and `email-agent`'s own tenant-isolation tests both exist to check.

---

## 5. Summary fields (optional)

An agent type can expose compact status on the Agent Instances overview card
via standard endpoints the gateway aggregates:

| Endpoint | Used for |
|---|---|
| `GET /drafts` | `pending_drafts` count in the instance card |
| `GET /costs/summary?period=day` | `today_cost_eur` in the instance card |
| `GET /sync/status` | Connection/sync badge (`email-agent`-specific today) |

These are `email-agent`-specific conventions, not part of the normative
contract. A different agent type can expose different summary data, or none —
the gateway falls back to `0` / `"unknown"` on error.

---

## 6. Proxy transparency

The agent service MUST:
- Accept and return arbitrary HTTP methods and bodies — the gateway forwards
  them verbatim.
- Return **404**, not 5xx, for an unknown path or an unknown run id. The
  gateway passes the upstream status through unchanged, so an agent that
  500s on a stale link turns every out-of-date bookmark into a platform
  incident.
- Not assume a specific authentication mechanism beyond the identity headers
  above. JWT authentication is handled entirely by the gateway; the upstream
  service trusts `X-Agora-User`.

---

## 7. Reusing the platform layer

`services/platform-core/` is an installable Python package holding the parts
of §3–§4 that aren't agent-specific: tenancy scoping, storage helpers, a
security-service client, cost/pricing/usage accounting, run registry
helpers, and system notifications. A Python agent SHOULD import it rather
than reimplement tenancy and cost accounting — `email-agent` does. It's not
required; the contract is the HTTP surface, not the language a conforming
agent is written in.

---

## 8. Out of scope (for now)

- A browser OAuth onboarding flow — that's `email-agent`-specific, not part
  of the general contract.
- Redis/Postgres backends — services may use any durable store.
- Registration at runtime. Agent types are registered in `application.yml`;
  the manifest describes a type the platform already knows, it doesn't add
  one.

---

## Conformance suite

The conformance suite is the executable half of this document: the document
says what MUST hold, the suite says whether it does, for a specific running
instance of a candidate agent type.

It talks to the agent **directly**, on its own port — not through the
gateway. That's deliberate: it checks that the agent honours the contract by
itself, rather than checking that the gateway happens to protect it.

```bash
cd services/platform-core
AGENT_CONFORMANCE_URL=http://localhost:8000 python -m pytest conformance/ -q
```

Against the Compose stack, the agent isn't published on the host, so run it
from inside the network instead:

```bash
docker compose -f infra/docker-compose.yml exec email-agent \
  python -m pytest /app/conformance/ -q
```

Without `AGENT_CONFORMANCE_URL` set, the whole suite skips — it never turns
a normal unit-test run red on a machine with nothing listening.

**What it does NOT check:**
- **Product behaviour.** Whether triage is any good is not a contract
  question.
- **Authentication.** The gateway owns it; the agent trusts the identity
  headers.
- **Destructive paths.** Nothing in the suite writes. A conformance run is
  safe to run against a live instance, which is the only way it's useful
  before a deploy.
