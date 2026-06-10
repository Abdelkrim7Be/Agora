from __future__ import annotations

import importlib
from contextvars import ContextVar
from typing import Dict, List, Tuple

from langchain_core.tools import BaseTool

# Set to True by tool_node only after human approval; gated tools check this before executing.
hitl_approved: ContextVar[bool] = ContextVar("hitl_approved", default=False)

CAPABILITY_MODULES: Dict[str, str] = {
    "email": "src.capabilities.email_tools",
    "calendar": "src.capabilities.calendar_tools",
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
