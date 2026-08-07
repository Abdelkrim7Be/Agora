"""What this agent tells the platform about itself.

The gateway used to carry this declaration in `application.yml` — the platform
described the agent. That inverts the ownership: adding a settings page to the
email agent meant editing the gateway, and a second agent type would have meant
editing it again. The agent is the only component that knows what it can do and
what there is to configure, so it declares that here and the gateway reads it.

Deliberately NOT declared here: `base_path`, `health_path`, `color` and `icon`.
Routing is the gateway's decision — an agent that could name its own proxy prefix
could claim another agent's traffic — and the palette belongs to the design
system, not to a container.
"""

from __future__ import annotations

# Bumped when the contract itself changes shape, not when this agent's own
# capabilities or settings change. The gateway refuses a manifest it cannot read.
CONTRACT_VERSION = 1

AGENT_TYPE_ID = "email-agent"

DISPLAY_NAME = "Email Agent"

DESCRIPTION = (
    "Trie les e-mails, rédige les réponses, apprend votre style et gère la boîte "
    "mail, chaque envoi restant sous validation."
)

CAPABILITIES = [
    "email_triage",
    "draft_approval",
    "gmail_sync",
    "style_learning",
    "cost_observability",
]

# Ordered: the workspace renders them in this order.
SETTINGS_SCHEMA = [
    {
        "key": "persona",
        "label": "Persona",
        "description": "Background, triage rules, response preferences and writing style.",
        "path": "/persona",
    },
    {
        "key": "categories",
        "label": "Categories",
        "description": "Workflow categories, routing, policies and templates.",
        "path": "/categories",
    },
    {
        "key": "rules",
        "label": "Rules",
        "description": "Deterministic automation, starter rules, digest and follow-up settings.",
        "path": "/rules",
    },
    {
        "key": "capabilities",
        "label": "Capabilities",
        "description": "Enabled tools and approval gates for this agent instance.",
        "path": "/capabilities",
    },
    {
        "key": "permissions",
        "label": "Permissions",
        "description": "Per-instance mailbox access grants.",
        "path": "/permissions",
    },
]


def build_manifest() -> dict:
    """The body served at `GET /manifest`.

    Static by design: the manifest describes the agent *type*, not one tenant's
    instance, so it must not read per-instance config or touch the tenant context.
    It is served unauthenticated for the same reason — it carries no tenant data.
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "id": AGENT_TYPE_ID,
        "display_name": DISPLAY_NAME,
        "description": DESCRIPTION,
        "capabilities": list(CAPABILITIES),
        "settings_schema": [dict(section) for section in SETTINGS_SCHEMA],
    }
