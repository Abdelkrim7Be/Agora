# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Dependabot, CODEOWNERS, a dependency-review CI gate, and `.editorconfig`.

### Fixed
- Platform-role approvers could manage (write) categories, contacts, roles, and
  drafts but could not read them back through the same endpoints. The UI's own
  nav links to those screens 403'd for that role.
- Gateway CORS accepted a credentialed wildcard origin when
  `GATEWAY_CORS_ALLOWED_ORIGINS` was left unset outside Docker Compose.
- The security service accepted requests with no authentication at all beyond
  Docker network isolation; it now shares a secret with email-agent the same
  way email-agent already does with the gateway.
- Redis and the Grafana admin account had no required password; Postgres had a
  weak literal fallback outside Compose. All now require an explicit value.
