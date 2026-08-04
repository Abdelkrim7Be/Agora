from __future__ import annotations

import re

from src import ratelimit
from src.config import settings
from src.metrics import inc_counter
from src.models import AuthorizeRequest, AuthorizeResponse
from src.policy import PolicyConfig, load_policy

# Matches both plain addresses and "Name <addr@host>" forms.
_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+")


def _recipient_text(to) -> str:
    if isinstance(to, list):
        return ", ".join(str(item) for item in to if item)
    return str(to or "")


def _extract_addresses(to) -> list[str]:
    """Return lowercased addresses from a string/list recipient field."""
    return [addr.lower() for addr in _ADDR_RE.findall(_recipient_text(to))]


def _extract_domains(to) -> list[str]:
    """Return lowercased domains from a string/list recipient field."""
    return [addr.split("@")[1] for addr in _extract_addresses(to)]


def _domain_matches(domain: str, entries) -> bool:
    """True if domain equals a policy entry or is a subdomain of one.

    So denying 'evil.com' also denies 'mail.evil.com', and allowing 'company.com'
    also permits 'eu.company.com' — entries are matched at the registrable boundary.
    """
    return any(domain == e or domain.endswith("." + e) for e in entries)


def _check_recipients(to, recipients) -> str | None:
    """Return a deny-reason string if the recipient fails policy, else None."""
    normalized_to = _recipient_text(to).strip()
    if not normalized_to:
        return "recipient 'to' field is empty"

    addresses = _extract_addresses(normalized_to)
    if not addresses:
        return f"could not parse a valid email address from 'to': {to!r}"
    domains = [addr.split("@")[1] for addr in addresses]

    for address in addresses:
        if address in recipients.deny_addresses:
            return f"recipient address '{address}' is on the deny list"

    for domain in domains:
        if _domain_matches(domain, recipients.deny_domains):
            return f"recipient domain '{domain}' is on the deny list"

    # Address allow list is checked before the domain one and is strictly
    # narrower: when set, membership is per-address and a permitted domain no
    # longer implies a permitted mailbox.
    if recipients.allow_addresses:
        for address in addresses:
            if address not in recipients.allow_addresses:
                return f"recipient address '{address}' is not on the allow list"

    if recipients.allow_domains:
        for domain in domains:
            if not _domain_matches(domain, recipients.allow_domains):
                return f"recipient domain '{domain}' is not on the allow list"

    return None


def _check_flow(arg_trust: dict, tool) -> str | None:
    """Return a deny reason if any argument carries disallowed trust."""
    if not tool.args:
        return None
    for arg_name, rule in tool.args.items():
        allowed = rule.allow_trust
        if not allowed:
            continue
        actual = arg_trust.get(arg_name)
        if actual and actual not in allowed:
            return (
                f"argument {arg_name!r} carries trust {actual!r}, "
                f"policy allows only {allowed} here"
            )
    return None


def authorize(
    req: AuthorizeRequest,
    policy: PolicyConfig | None = None,
) -> AuthorizeResponse:
    policy = policy or load_policy(settings.policy_path)

    tool = policy.tools.get(req.action)
    if tool is None:
        inc_counter("agora_security_authorize_total", decision=policy.default, action=req.action)
        return AuthorizeResponse(
            decision=policy.default,
            reason=f"no policy rule for action '{req.action}'",
        )

    flow_deny = _check_flow(req.arg_trust, tool)
    if flow_deny:
        inc_counter("agora_security_authorize_total", decision="deny", action=req.action)
        return AuthorizeResponse(decision="deny", reason=flow_deny)

    # Recipient check — fires only when the policy block exists and 'to' arg is present.
    if tool.recipients is not None:
        # Prefer the caller-declared recipients; fall back to a "to" argument so
        # older clients and non-send tools keep working.
        declared = req.recipients or req.args.get("to", "")
        deny = _check_recipients(declared, tool.recipients)
        if deny:
            inc_counter("agora_security_authorize_total", decision="deny", action=req.action)
            return AuthorizeResponse(decision="deny", reason=deny)

    if tool.limits is not None:
        # Content size check.
        if tool.limits.max_content_chars is not None:
            content = (
                req.args.get("content")
                or req.args.get("body")
                or req.args.get("note")
                or ""
            )
            if len(content) > tool.limits.max_content_chars:
                inc_counter("agora_security_authorize_total", decision="deny", action=req.action)
                return AuthorizeResponse(
                    decision="deny",
                    reason=(
                        f"content exceeds max_content_chars "
                        f"({tool.limits.max_content_chars})"
                    ),
                )

        # Rate-limit check.
        if tool.limits.max_per_run is not None or tool.limits.max_per_day is not None:
            run_id = req.context.get("run_id", "")
            action_id = req.context.get("action_id", "")
            user_id = req.context.get("user_id", "")
            deny = ratelimit.would_exceed(
                run_id,
                tool.limits.max_per_run,
                tool.limits.max_per_day,
                action_id,
                user_id,
            )
            if deny:
                inc_counter("agora_security_authorize_total", decision="deny", action=req.action)
                return AuthorizeResponse(decision="deny", reason=deny)

    # All checks passed — consume a rate-limit slot if applicable, then grant.
    if tool.limits is not None and (
        tool.limits.max_per_run is not None or tool.limits.max_per_day is not None
    ):
        ratelimit.record(
            req.context.get("run_id", ""),
            req.context.get("action_id", ""),
            req.context.get("user_id", ""),
        )

    inc_counter("agora_security_authorize_total", decision=tool.decision, action=req.action)
    return AuthorizeResponse(
        decision=tool.decision,
        reason=f"allowed by policy for '{req.action}'",
    )
