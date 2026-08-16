#!/usr/bin/env python3
"""M4 end-to-end flow through MCP: launch -> breakpoint -> run -> observe ->
run(to AV) -> terminate, all as ordinary MCP tool calls."""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

SERVER = str(ROOT / "mcp" / "server.py")
TARGET = str(ROOT / "benchmarks" / "targets" / "crash_target.exe")


def result(r) -> dict:
    """Extract the tool's structured return value from a call_tool result."""
    if getattr(r, "structured_content", None) is not None:
        return r.structured_content
    # fall back to text content block
    for c in r.content:
        if hasattr(c, "text"):
            return json.loads(c.text)
    return {}


async def main() -> int:
    out = []
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            d = result(await session.call_tool("launch", {"path": TARGET}))
            out.append({"op": "launch", "stop_reason": d.get("stop_reason")})

            d = result(await session.call_tool("breakpoint_add", {"expr": "crash_target!crash_here"}))
            out.append({"op": "breakpoint_add", "address": d.get("address")})

            d = result(await session.call_tool("run", {"timeout": 10}))
            out.append({"op": "run", "stop_reason": d.get("stop_reason"),
                        "symbol_at_pc": d.get("symbol_at_pc")})

            d = result(await session.call_tool("run", {"timeout": 10}))
            out.append({"op": "run", "stop_reason": d.get("stop_reason"),
                        "exception": d.get("exception")})

            d = result(await session.call_tool("observe", {}))
            out.append({"op": "observe", "registers": len(d.get("registers", {})),
                        "modules": len(d.get("modules", []))})

            await session.call_tool("terminate", {})
            out.append({"op": "terminate", "ok": True})

    print(json.dumps(out, indent=2, default=str))

    # assertions on the collected flow
    stops = [o.get("stop_reason") for o in out if "stop_reason" in o]
    assert stops[0] == "initial_break", stops
    assert stops[1] == "breakpoint", stops
    assert stops[2] == "exception", stops
    assert out[3]["exception"]["code"] == "0xc0000005", out[3]
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"FAILED: {e!r}")
        sys.exit(1)
