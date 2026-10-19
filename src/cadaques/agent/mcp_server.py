"""Serve the CADAQUES tools over the Model Context Protocol (extra ``[mcp]``).

A thin adapter: every MCP tool is a :class:`~cadaques.agent.tools.Tool`
with the same name, description and input schema, and every call goes
through :meth:`Toolbox.call`. The response envelope is returned both as
structured content and as JSON text; error envelopes set ``isError``.
Calls run in a worker thread, because tools read and write campaign
files and ``run_campaign`` may evaluate an in-process oracle for a while.

Written against the low-level server of the ``mcp`` 2.x SDK, which
accepts explicit JSON schemas (the high-level server derives them from
Python signatures, which would duplicate :mod:`.tools`).
"""

from __future__ import annotations

import json
from typing import Any

from .. import __version__
from .tools import Toolbox

INSTRUCTIONS = (
    "CADAQUES runs budgeted discovery campaigns. Start with describe_capabilities. "
    "Create a campaign from a spec, then either run_campaign (in-process oracle) or loop "
    "reserve_evaluation -> run the job yourself -> settle_evaluation. The kernel enforces the "
    "budget and records every event with your actor. Give every mutating call a fresh "
    "request_id, and reuse it when retrying the same call."
)


def build_server(toolbox: Toolbox) -> Any:
    try:
        import anyio
        import mcp.types as types
        from mcp.server.lowlevel import Server
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "The MCP server needs the optional dependency: pip install 'cadaques[mcp]'"
        ) from exc

    tools = [
        types.Tool(
            name=t.name,
            description=t.description,
            input_schema=dict(t.input_schema),
            annotations=types.ToolAnnotations(
                read_only_hint=t.read_only,
                destructive_hint=t.destructive,
                idempotent_hint=t.idempotent,
                open_world_hint=False,
            ),
        )
        for t in toolbox.tools.values()
    ]

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        envelope = await anyio.to_thread.run_sync(
            toolbox.call, params.name, dict(params.arguments or {})
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(envelope, indent=2))],
            structured_content=envelope,
            is_error=not envelope["ok"],
        )

    return Server(
        "cadaques",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def serve(toolbox: Toolbox) -> None:  # pragma: no cover — exercised through the stdio client
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server(toolbox)

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)
