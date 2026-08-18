"""DebugSession — the debugger-agnostic interface every backend implements.

Grouped as: Session / Execution / Observation / Inspection / Control / State.
This is the contract the MCP layer and CLI talk to; any backend (DbgEng, x64dbg,
GDB/LLDB via DAP) must implement it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from .types import (
    BreakpointInfo,
    ExceptionInfo,
    Instruction,
    Module,
    StateSnapshot,
)


class DebugSession(ABC):
    """Lifecycle + state access for one debugging session (one debuggee)."""

    # -- Session -----------------------------------------------------------
    @abstractmethod
    def launch(self, path: str, args: Optional[List[str]] = None) -> StateSnapshot:
        """Create a process and stop at the initial breakpoint."""

    @abstractmethod
    def attach(self, pid: int) -> StateSnapshot:
        """Attach to a running process and stop at the initial breakpoint."""

    @abstractmethod
    def restart(self) -> StateSnapshot:
        """Restart the current debuggee."""

    @abstractmethod
    def terminate(self) -> None:
        """Terminate the current debuggee."""

    @abstractmethod
    def detach(self) -> None:
        """Detach, leaving the debuggee running."""

    @abstractmethod
    def module_base(self, module: str) -> Module:
        """Resolve a module by (case-insensitive) basename."""

    # -- Execution ---------------------------------------------------------
    @abstractmethod
    def run(self, timeout: float = 10.0) -> StateSnapshot:
        """Continue execution; block until a stop event or timeout."""

    @abstractmethod
    def pause(self) -> StateSnapshot:
        """Interrupt a running debuggee."""

    @abstractmethod
    def step(self, mode: str = "into") -> StateSnapshot:
        """Single-step. mode: into | over | out."""

    # -- Threads -----------------------------------------------------------
    @abstractmethod
    def thread_list(self) -> List[dict]:
        """List threads: index (engine id), tid (OS), teb, pc, symbol."""

    @abstractmethod
    def set_thread(self, index: int) -> None:
        """Switch the current thread by index (engine id)."""

    @abstractmethod
    def get_thread(self) -> int:
        """Return the current thread index (engine id)."""

    # -- Observation / Event -----------------------------------------------
    @abstractmethod
    def wait_event(self, timeout: float = 10.0) -> dict:
        """Block until the next debugger event; return a structured Event."""

    @abstractmethod
    def observe(self) -> StateSnapshot:
        """Structured, high-value context for the current stop."""

    # -- Inspection --------------------------------------------------------
    @abstractmethod
    def read_memory(self, address: int, size: int) -> bytes:
        """Read ``size`` bytes of virtual memory at ``address``."""

    @abstractmethod
    def write_memory(self, address: int, data: bytes) -> None:
        """Write bytes to virtual memory (exploit-dev: patch/verify)."""

    @abstractmethod
    def get_register(self, name: str) -> int:
        """Read a single register by name (rax, rcx, rip, ...)."""

    @abstractmethod
    def set_register(self, name: str, value: int) -> None:
        """Set a register (exploit-dev: force a control-flow value)."""

    @abstractmethod
    def search_memory(self, address: int, size: int, pattern: bytes) -> List[int]:
        """Search [address, address+size) for a byte pattern; return match addrs."""

    @abstractmethod
    def find_gadget(self, module: str, gadget: List[str], limit: int = 20) -> List[int]:
        """Find ROP gadget addresses (a sequence of instruction mnemonics)."""

    @abstractmethod
    def disassemble(self, address: int, count: int = 8) -> List[Instruction]:
        """Disassemble ``count`` instructions at ``address``."""

    # -- Control -----------------------------------------------------------
    @abstractmethod
    def breakpoint_add(self, expr: str, condition: Optional[str] = None) -> BreakpointInfo:
        """Add a breakpoint; ``expr`` is a symbol or ``0xADDR``. ``condition`` is
        an optional DbgEng MASM expression (stop only when true)."""

    @abstractmethod
    def breakpoint_add_hw(self, address: int, size: int = 8, access: str = "write") -> BreakpointInfo:
        """Add a hardware watchpoint. access: read | write | execute."""

    @abstractmethod
    def breakpoint_remove(self, bp_id: int) -> None:
        """Remove a breakpoint by id."""

    @abstractmethod
    def breakpoint_list(self) -> List[BreakpointInfo]:
        """List current breakpoints."""

    # -- State -------------------------------------------------------------
    @abstractmethod
    def snapshot(self) -> StateSnapshot:
        """Full current-state snapshot (same as observe, explicit alias)."""


class BackendError(Exception):
    """Raised for backend-level failures (attach failed, bad address, ...)."""
