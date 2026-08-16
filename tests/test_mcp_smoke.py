#!/usr/bin/env python3
"""M4 smoke test — spawns the MCP server over stdio and lists/calls tools."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

SERVER = str(ROOT / "mcp" / "server.py")


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", json_dump(names))

            # Safe call that needs no active session.
            r = await session.call_tool("breakpoint_list", {})
            print("breakpoint_list:", json_dump(r.content))
    return 0


def json_dump(o) -> str:
    import json
    return json.dumps(o, default=str)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"FAILED: {e!r}")
        sys.exit(1)
