# Security policy

For the security *architecture* — what's defended against, how
authentication and authorization work, what the real limitations are — see
[`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md). This file is about
reporting a vulnerability, not explaining the design.

## Supported versions

Agora doesn't yet have a versioned release process — `main` is the only
supported line. Security fixes land there; there is no LTS branch to
backport to at this stage.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Use GitHub's private vulnerability reporting: go to the
[Security tab](../../security/advisories/new) and click "Report a
vulnerability." This opens a private advisory visible only to you and the
maintainer.

Include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof of concept if you have one.
- The affected component (`gateway`, `email-agent`, `security`, `web-react`,
  or infrastructure/deployment configuration).

You should expect an acknowledgment within a reasonable timeframe. As a
small project without a dedicated security team, response times aren't
formally guaranteed yet — see the "known gaps" note in
[`docs/INCIDENT_RESPONSE.md`](docs/INCIDENT_RESPONSE.md).

## Scope

In scope: the four services under `services/`, the Docker Compose
deployment configuration under `infra/`, and the CI pipeline.

Out of scope: vulnerabilities in third-party dependencies without a
demonstrated, Agora-specific exploit path (report those upstream instead);
the security posture of an LLM provider you choose to connect (Ollama,
Groq, Mistral, OpenAI, Anthropic) — that's between you and the provider.

## Disclosure

Coordinated disclosure preferred: report privately first, allow time for a
fix, then disclose publicly. There is no bug bounty program.
