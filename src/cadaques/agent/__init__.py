"""The agent-first surface of CADAQUES (ADR-0013).

Typed tools over persistent campaigns, for agents and people alike::

    from cadaques.agent import Toolbox
    tb = Toolbox("workspace/")
    tb.call("describe_capabilities")

The same tools are served over MCP (``cadaques-agent serve-mcp``, extra
``cadaques[mcp]``) and as a JSON command line (``cadaques-agent call``).
"""

from .errors import ERROR_CODES, ToolError
from .store import CampaignStore
from .tools import TOOLS, Tool, Toolbox

__all__ = ["CampaignStore", "ERROR_CODES", "TOOLS", "Tool", "ToolError", "Toolbox"]
