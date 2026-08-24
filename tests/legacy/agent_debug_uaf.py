#!/usr/bin/env python3
"""Agent-in-the-loop root-cause run on a REAL crash sample (use-after-free).

Drives the MCP server exactly as a coding agent would: launch -> hypothesize ->
breakpoint -> run -> observe -> inspect the crashing instruction + registers ->
synthesize a root-cause conclusion. This is the "AI 直接操作调试器" loop.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

SERVER = str(ROOT / "mcp" / "server.py")
TARGET = str(ROOT / "benchmarks" / "targets" / "uaf_target.exe")


def result(r):
    if getattr(r, "structured_content", None) is not None:
        return r.structured_content
    for c in r.content:
        if hasattr(c, "text"):
            return json.loads(c.text)
    return {}


async def main() -> int:
    steps = []
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. launch
            d = result(await session.call_tool("launch", {"path": TARGET}))
            steps.append(("launch", d.get("stop_reason"), d.get("symbol_at_pc")))

            # 2. hypothesis: the bug lives in trigger_uaf -> breakpoint there
            d = result(await session.call_tool("breakpoint_add", {"expr": "uaf_target!trigger_uaf"}))
            steps.append(("breakpoint trigger_uaf", d.get("address")))

            # 3. run to the suspected function
            d = result(await session.call_tool("run", {"timeout": 10}))
            steps.append(("run", d.get("stop_reason"), d.get("symbol_at_pc")))

            # 4. continue -> crash
            d = result(await session.call_tool("run", {"timeout": 10}))
            steps.append(("run", d.get("stop_reason"), d.get("pc")))
            crash_pc = d.get("pc", "0x0")
            exception = d.get("exception", {})

            # 5. what instruction crashed?
            d = result(await session.call_tool("disassemble", {"address": crash_pc, "count": 3}))
            insns = d.get("instructions", [])
            crashing_ins = insns[0] if insns else {}
            steps.append(("crashing_instruction",
                          f"{crashing_ins.get('mnemonic')} {crashing_ins.get('operands')}"))

            # 6. which register held the target? (observe gives full registers)
            d = result(await session.call_tool("observe", {}))
            regs = d.get("registers", {})
            steps.append(("registers", {k: regs.get(k) for k in ("rax", "rcx", "rdx", "rip")}))

            await session.call_tool("terminate", {})

    print(json.dumps(steps, indent=2, default=str))

    # ---- root-cause synthesis -------------------------------------------
    print("\n===== ROOT-CAUSE CONCLUSION =====")
    mnemonic = crashing_ins.get("mnemonic", "")
    operands = crashing_ins.get("operands", "")
    rdx = regs.get("rdx", "")

    is_uaf = (
        exception.get("code") == "0xc0000005"
        and mnemonic == "call"
        and "0x4141414141414141" in str(rdx)
    )
    if is_uaf:
        print(f"Crash: {mnemonic} {operands} at {crash_pc} (access violation).")
        print(f"rdx = {rdx}  ->  0x41 is ASCII 'A'.")
        print("The target was a freed heap object whose function-pointer field was")
        print("overwritten with 'A' fill (0x41) after its slot was reused.")
        print("=> USE-AFTER-FREE: dangling call through a corrupted function pointer.")
        return 0

    print("Crash signature did not match UAF hypothesis:", mnemonic, operands, rdx)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"FAILED: {e!r}")
        sys.exit(1)
