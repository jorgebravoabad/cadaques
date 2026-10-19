"""``cadaques-agent``: the tool surface as a JSON command line.

For agents that operate through a shell rather than MCP::

    cadaques-agent --root ws schemas
    cadaques-agent --root ws call get_campaign_status '{"campaign_id": "pc1d"}'
    echo '{...}' | cadaques-agent --root ws call settle_evaluation -
    cadaques-agent --root ws serve-mcp

``call`` prints the response envelope as JSON on stdout and exits 0
when ``ok`` is true, 1 when it is false. The workspace defaults to
``$CADAQUES_WORKSPACE``, else ``./cadaques-workspace``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from .tools import Toolbox


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cadaques-agent", description=__doc__.splitlines()[0])
    p.add_argument("--root", default=os.environ.get("CADAQUES_WORKSPACE", "cadaques-workspace"),
                   help="workspace directory holding the campaigns")
    p.add_argument("--allow", action="append", default=[], metavar="CLASS",
                   help="additionally allow this dotted participant class in specs (repeatable)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("schemas", help="print every tool with its input and output schema")
    call = sub.add_parser("call", help="call one tool and print the response envelope")
    call.add_argument("tool")
    call.add_argument("args", nargs="?", default="{}", help="JSON object, or '-' for stdin")
    sub.add_parser("serve-mcp", help="serve the tools over MCP on stdio (needs cadaques[mcp])")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    toolbox = Toolbox(ns.root, allow_components=ns.allow)
    if ns.command == "schemas":
        json.dump(toolbox.schemas(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if ns.command == "call":
        raw = sys.stdin.read() if ns.args == "-" else ns.args
        try:
            args = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = {"ok": False, "tool": ns.tool, "error": {
                "code": "invalid_arguments", "message": f"Arguments are not valid JSON: {exc}",
                "retryable": False, "next_actions": [ns.tool], "details": {}}}
        else:
            response = toolbox.call(ns.tool, args)
        json.dump(response, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if response["ok"] else 1
    from .mcp_server import serve

    serve(toolbox)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
