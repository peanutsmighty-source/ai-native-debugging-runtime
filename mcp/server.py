#!/usr/bin/env python3
"""MCP server — exposes the AI-Native Debugging Runtime to coding agents.

Runs over stdio (Model Context Protocol). The DebugSession persists in-process,
so an agent can: launch -> breakpoint -> run -> observe -> read memory ->
disassemble -> terminate, all through ordinary tool calls.

Register with Claude Desktop / Claude Code / Cursor / Cline, e.g.:
    {"mcpServers": {"ai-debugger": {
        "command": "python",
        "args": ["<repo>/mcp/server.py"]
    }}}
"""

import functools
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("WINDBG_DIR", str(ROOT / "vendor" / "dbgeng"))
os.environ["PATH"] = str(ROOT / "vendor" / "dbgeng") + os.pathsep + os.environ.get("PATH", "")

from mcp.server import MCPServer                 # noqa: E402
from backends.dbgeng.adapter import DbgEngAdapter  # noqa: E402
from benchmarks.exploit_util import asm_x64       # noqa: E402

mcp = MCPServer("ai-debugger")
_session = DbgEngAdapter()

# A debugger session is single-threaded; serialize tool calls defensively.
_lock = threading.Lock()


def locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def _addr(s) -> int:
    s = str(s).strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 10)


# -- Session ---------------------------------------------------------------
@mcp.tool()
@locked
def launch(path: str, args: list = None) -> dict:
    """Launch a Windows executable and stop at its initial breakpoint."""
    return _session.launch(path, args or []).to_dict()


@mcp.tool()
@locked
def attach(pid: int) -> dict:
    """Attach to a running process by PID and stop at the initial breakpoint."""
    return _session.attach(int(pid)).to_dict()


@mcp.tool()
@locked
def restart() -> dict:
    """Restart the current debuggee."""
    return _session.restart().to_dict()


@mcp.tool()
@locked
def terminate() -> dict:
    """Terminate the current debuggee."""
    _session.terminate()
    return {}


@mcp.tool()
@locked
def detach() -> dict:
    """Detach, leaving the debuggee running."""
    _session.detach()
    return {}


# -- Execution -------------------------------------------------------------
@mcp.tool()
@locked
def run(timeout: float = 10.0) -> dict:
    """Continue execution; block until a stop event or timeout (seconds)."""
    return _session.run(float(timeout)).to_dict()


@mcp.tool()
@locked
def pause() -> dict:
    """Interrupt a running debuggee."""
    return _session.pause().to_dict()


@mcp.tool()
@locked
def step(mode: str = "into") -> dict:
    """Single-step. mode: 'into' | 'over' | 'out'."""
    return _session.step(mode).to_dict()


# -- Observation -----------------------------------------------------------
@mcp.tool()
@locked
def wait_event(timeout: float = 10.0) -> dict:
    """Block until the next debugger event (breakpoint/exception/...)."""
    return _session.wait_event(float(timeout))


@mcp.tool()
@locked
def observe() -> dict:
    """Return structured, high-value context for the current stop."""
    return _session.observe().to_dict()


@mcp.tool()
@locked
def snapshot() -> dict:
    """Full current-state snapshot (registers/modules/stack/disasm)."""
    return _session.snapshot().to_dict()


# -- Inspection ------------------------------------------------------------
@mcp.tool()
@locked
def read_memory(address: str, size: int) -> dict:
    """Read `size` bytes of virtual memory at `address` (0x...)."""
    return {"hex": _session.read_memory(_addr(address), int(size)).hex()}


@mcp.tool()
@locked
def write_memory(address: str, data_hex: str) -> dict:
    """Write bytes to memory. `data_hex` is a hex string (e.g. '414243')."""
    _session.write_memory(_addr(address), bytes.fromhex(data_hex))
    return {}


@mcp.tool()
@locked
def get_register(name: str) -> dict:
    """Read a single register by name (rax, rcx, rdx, r8, rip, ...)."""
    return {"name": name, "value": hex(_session.get_register(name))}


@mcp.tool()
@locked
def set_register(name: str, value: str) -> dict:
    """Set a register to `value` (0x...). Exploit-dev: force a control-flow value."""
    _session.set_register(name, _addr(value))
    return {}


@mcp.tool()
@locked
def disassemble(address: str, count: int = 8) -> dict:
    """Disassemble `count` instructions at `address` (0x...)."""
    return {"instructions": [i.to_dict() for i in _session.disassemble(_addr(address), int(count))]}


@mcp.tool()
@locked
def asm(code: str) -> dict:
    """Assemble x86-64 assembly (Intel syntax, Keystone) to shellcode bytes.
    Supports labels (`msg:`), rip-relative (`lea rcx, [rip+msg]`) and data
    directives (`.string "calc"`, `.byte 0x90`)."""
    b = asm_x64(code)
    return {"hex": b.hex(), "size": len(b)}


# -- Breakpoints -----------------------------------------------------------
@mcp.tool()
@locked
def breakpoint_add(expr: str, condition: str = None) -> dict:
    """Add a breakpoint. `expr` is a symbol (mod!func) or 0x... address.
    `condition` (optional) is a DbgEng MASM expression: stop only when true,
    e.g. '@rcx == 0x41414141' or 'poi(rcx) == 0xdeadbeef'."""
    return _session.breakpoint_add(expr, condition).to_dict()


@mcp.tool()
@locked
def breakpoint_add_hw(address: str, size: int = 8, access: str = "write") -> dict:
    """Add a hardware watchpoint. `access`: 'read' | 'write' | 'execute'."""
    return _session.breakpoint_add_hw(_addr(address), int(size), access).to_dict()


@mcp.tool()
@locked
def breakpoint_remove(bp_id: int) -> dict:
    """Remove a breakpoint by id."""
    _session.breakpoint_remove(int(bp_id))
    return {}


@mcp.tool()
@locked
def breakpoint_list() -> dict:
    """List current breakpoints."""
    return {"breakpoints": [b.to_dict() for b in _session.breakpoint_list()]}


if __name__ == "__main__":
    mcp.run()
