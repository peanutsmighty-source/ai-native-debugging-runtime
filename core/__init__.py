"""Core abstraction layer for the AI-Native Debugging Runtime.

This package defines the debugger-agnostic primitives from the PRD:
Session / State / Observation / Event / Action. Backends implement the
``DebugSession`` interface; the CLI and MCP layers talk only to this layer.
"""

from .types import (
    StopReason,
    ExceptionInfo,
    BreakpointInfo,
    Frame,
    Module,
    StateSnapshot,
    Instruction,
)
from .session import DebugSession

__all__ = [
    "StopReason",
    "ExceptionInfo",
    "BreakpointInfo",
    "Frame",
    "Module",
    "StateSnapshot",
    "Instruction",
    "DebugSession",
]
