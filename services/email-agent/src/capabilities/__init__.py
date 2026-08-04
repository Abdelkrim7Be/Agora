from __future__ import annotations

import importlib
from contextvars import ContextVar
from typing import Dict, List, Tuple

from langchain_core.tools import BaseTool

# Set to True by tool_node only after human approval; gated tools check this before executing.
hitl_approved: ContextVar[bool] = ContextVar("hitl_approved", default=False)

# Trusted Gmail ids for the email currently being handled. Tools read these
# from graph context instead of accepting LLM-supplied target ids.
current_email_id: ContextVar[str | None] = ContextVar("current_email_id", default=None)
current_gmail_thread_id: ContextVar[str | None] = ContextVar(
    "current_gmail_thread_id", default=None
)

# Trusted recipients. These are the reason a prompt injection cannot redirect
# mail: the model is never asked where something goes.
#
#   current_reply_to     the address that sent the message being handled, taken
#                        from its headers — the only place a reply can go.
#   current_route_targets addresses the *workspace* configured (workflow route,
#                        roles directory). Never derived from message content.
#
# Both are set by tool_node from graph state, so a tool that needs a recipient
# reads it here rather than accepting one from the LLM.
current_reply_to: ContextVar[str | None] = ContextVar("current_reply_to", default=None)
current_route_targets: ContextVar[tuple[str, ...]] = ContextVar(
    "current_route_targets", default=()
)


class UntrustedRecipientError(RuntimeError):
    """Raised when a tool has no trusted recipient to send to.

    tool_node turns this into a recoverable tool message, so the run reports the
    misconfiguration instead of silently falling back to a model-chosen address.
    """

CAPABILITY_MODULES: Dict[str, str] = {
    "email": "src.capabilities.email_tools",
    "calendar": "src.capabilities.calendar_tools",
    "inbox": "src.capabilities.inbox_tools",
    "drafts": "src.capabilities.draft_tools",
}


def load_capabilities(flags: Dict[str, bool]) -> Tuple[List[BaseTool], str]:
    """Assemble the active tool list + combined tools-prompt from enabled capabilities.

    Fails loud on an unknown capability name (typo in config.yaml).
    """
    tools: List[BaseTool] = []
    prompt_parts: List[str] = []
    for name, enabled in flags.items():
        if name not in CAPABILITY_MODULES:
            raise ValueError(
                f"Unknown capability '{name}' in config. "
                f"Known: {sorted(CAPABILITY_MODULES)}"
            )
        if not enabled:
            continue
        module = importlib.import_module(CAPABILITY_MODULES[name])
        tools.extend(module.TOOLS)
        prompt_parts.append(module.TOOLS_PROMPT.strip())
    return tools, "\n".join(prompt_parts)


def tools_by_name(tools: List[BaseTool]) -> Dict[str, BaseTool]:
    return {t.name: t for t in tools}


def approval_required(flags: Dict[str, bool]) -> set[str]:
    """Aggregate the names of tools that need human approval across enabled capabilities."""
    names: set[str] = set()
    for name, enabled in flags.items():
        if name not in CAPABILITY_MODULES:
            raise ValueError(
                f"Unknown capability '{name}' in config. "
                f"Known: {sorted(CAPABILITY_MODULES)}"
            )
        if not enabled:
            continue
        module = importlib.import_module(CAPABILITY_MODULES[name])
        names |= getattr(module, "REQUIRES_APPROVAL", set())
    return names
