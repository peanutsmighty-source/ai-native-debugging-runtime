"""DebugSession — the debugger-agnostic interface every backend implements.

Mirrors the PRD MVP API (10-15 high-quality interfaces), grouped as:
Session / Execution / Observation / Inspection / Control / State.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from .types import (
    BreakpointInfo,
    ExceptionInfo,
    Instruction,
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
    def disassemble(self, address: int, count: int = 8) -> List[Instruction]:
        """Disassemble ``count`` instructions at ``address``."""

    # -- Control -----------------------------------------------------------
    @abstractmethod
    def breakpoint_add(self, expr: str) -> BreakpointInfo:
        """Add a breakpoint; ``expr`` is a symbol or ``0xADDR``."""

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
