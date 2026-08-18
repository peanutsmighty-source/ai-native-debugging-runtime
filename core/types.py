"""Structured types shared by the Core, backends, CLI and MCP layers.

Everything is JSON-serializable via ``to_dict()``. This is the frozen
"after-state / state diff" contract the PRD requires: actions return these
snapshots, never bare "success" booleans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class StopReason(str, Enum):
    INITIAL_BREAK = "initial_break"
    BREAKPOINT = "breakpoint"
    EXCEPTION = "exception"
    STEP = "step"
    PROCESS_EXIT = "process_exit"
    UNKNOWN = "unknown"


@dataclass
class ExceptionInfo:
    code: int
    address: int
    first_chance: bool = True
    params: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": hex(self.code),
            "address": hex(self.address),
            "first_chance": self.first_chance,
            "params": [hex(p) for p in self.params],
        }


@dataclass
class BreakpointInfo:
    id: int
    address: Optional[int] = None
    symbol: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "address": hex(self.address) if self.address is not None else None,
            "symbol": self.symbol,
        }


@dataclass
class Frame:
    index: int
    ip: int
    symbol: str
    ret: int
    stack: int

    def to_dict(self) -> dict:
        return {
            "frame": self.index,
            "ip": hex(self.ip),
            "symbol": self.symbol,
            "ret": hex(self.ret),
            "stack": hex(self.stack),
        }


@dataclass
class Module:
    name: str
    base: int
    size: int

    def to_dict(self) -> dict:
        return {"name": self.name, "base": hex(self.base), "size": hex(self.size)}


@dataclass
class Instruction:
    address: int
    bytes: str
    mnemonic: str
    operands: str

    def to_dict(self) -> dict:
        return {
            "address": hex(self.address),
            "bytes": self.bytes,
            "mnemonic": self.mnemonic,
            "operands": self.operands,
        }


@dataclass
class StateSnapshot:
    """A point-in-time observation of the debuggee, tied to a stop reason."""

    pid: int
    status: str
    pc: int
    sp: int
    symbol_at_pc: str
    stop_reason: StopReason
    registers: Dict[str, int]
    modules: List[Module]
    backtrace: List[Frame]
    exception: Optional[ExceptionInfo] = None
    breakpoint: Optional[BreakpointInfo] = None
    disassembly: List[Instruction] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "pid": self.pid,
            "status": self.status,
            "pc": hex(self.pc),
            "sp": hex(self.sp),
            "symbol_at_pc": self.symbol_at_pc,
            "stop_reason": self.stop_reason.value,
        }
        if self.registers:
            d["registers"] = {k: hex(v) for k, v in self.registers.items()}
        if self.modules:
            d["modules"] = [m.to_dict() for m in self.modules]
        if self.backtrace:
            d["backtrace"] = [f.to_dict() for f in self.backtrace]
        if self.disassembly:
            d["disassembly"] = [i.to_dict() for i in self.disassembly]
        if self.exception is not None:
            d["exception"] = self.exception.to_dict()
        if self.breakpoint is not None:
            d["breakpoint"] = self.breakpoint.to_dict()
        if self.errors:
            d["errors"] = self.errors
        return d
