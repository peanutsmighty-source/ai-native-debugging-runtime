"""DbgEngAdapter — implements the Core DebugSession interface on top of DbgEng.

Thin, debugger-specific translation of the PRD primitives. All reasoning /
strategy stays in the agent; this layer only turns debugger state into stable,
structured Core types and deterministic actions into DbgEng calls.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Locate the vendored Debugging Tools (dbgeng.dll) and make pybag load it.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBGENG_DIR = PROJECT_ROOT / "vendor" / "dbgeng"

os.environ.setdefault("WINDBG_DIR", str(DBGENG_DIR))
os.environ["PATH"] = str(DBGENG_DIR) + os.pathsep + os.environ.get("PATH", "")

from pybag.userdbg import UserDbg                 # noqa: E402
from pybag.dbgeng import core as DbgEng           # noqa: E402
from pybag.dbgeng.idebugbreakpoint import DebugBreakpoint  # noqa: E402

from core.session import BackendError, DebugSession          # noqa: E402
from core.types import (                                     # noqa: E402
    BreakpointInfo,
    ExceptionInfo,
    Frame,
    Instruction,
    Module,
    StateSnapshot,
    StopReason,
)

EXCEPTION_BREAKPOINT = 0x80000003
EXCEPTION_SINGLE_STEP = 0x80000004

KEY_REGS = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "rip",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags"]


def _hard_kill(pid: int) -> None:
    """Best-effort Win32 TerminateProcess, used when DbgEng terminate fails."""
    try:
        import ctypes
        PROCESS_TERMINATE = 0x0001
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if h:
            ctypes.windll.kernel32.TerminateProcess(h, 1)
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass


class DbgEngAdapter(DebugSession):
    def __init__(self, silent: bool = True):
        self._dbg = UserDbg()
        if silent:
            try:
                self._dbg.callbacks.stdout = open(os.devnull, "w")
            except Exception:
                pass

        self._last_event: Dict = {}
        self._target_path: Optional[str] = None
        self._target_args: Optional[List[str]] = None
        self._bps: Dict[int, BreakpointInfo] = {}
        self._initial_break_pending: bool = False

        # Exception-safe event handlers (must NEVER raise — see M1 report).
        self._dbg.events.breakpoint(self._on_breakpoint)
        self._dbg.events.exception(self._on_exception)

    # -- event capture -----------------------------------------------------
    def _on_breakpoint(self, *args) -> int:
        self._last_event = {"type": "breakpoint"}
        try:
            bp = DebugBreakpoint(args[0])
            self._last_event["bp_id"] = int(bp.GetId())
            self._last_event["bp_offset"] = int(bp.GetOffset())
        except Exception as e:
            self._last_event["handler_error"] = repr(e)
        return DbgEng.DEBUG_STATUS_BREAK

    def _on_exception(self, record, first_chance) -> int:
        self._last_event = {"type": "exception"}
        try:
            rec = getattr(record, "contents", record)
            self._last_event["code"] = int(rec.ExceptionCode)
            self._last_event["address"] = int(rec.ExceptionAddress)
            self._last_event["first_chance"] = bool(first_chance)
            try:
                self._last_event["params"] = [
                    int(rec.ExceptionInformation[i]) for i in range(2)
                ]
            except Exception:
                pass
        except Exception as e:
            self._last_event["handler_error"] = repr(e)
        return DbgEng.DEBUG_STATUS_BREAK

    def _clear_event(self) -> None:
        self._last_event = {}

    # -- Session -----------------------------------------------------------
    def launch(self, path: str, args: Optional[List[str]] = None) -> StateSnapshot:
        self._target_path = os.path.abspath(path)
        self._target_args = args or []
        cmdline = self._target_path
        if self._target_args:
            cmdline += " " + " ".join(self._target_args)

        self._clear_event()
        # DETACHED_PROCESS (0x8) keeps the debuggee from inheriting our std
        # handles, so its printf does not pollute the CLI/MCP stdio stream.
        flags = DbgEng.DEBUG_ONLY_THIS_PROCESS | 0x8
        self._initial_break_pending = True
        self._dbg._client.CreateProcess(cmdline, flags)
        self._dbg._control.AddEngineOptions(DbgEng.DEBUG_ENGINITIAL_BREAK)
        self._dbg.wait(10)
        return self._snapshot(False, 0, 0, False)   # minimal: pid/status/pc/stop

    def attach(self, pid: int) -> StateSnapshot:
        self._clear_event()
        self._initial_break_pending = True
        self._dbg.attach(pid, initial_break=True)
        return self._snapshot(False, 0, 0, False)

    def restart(self) -> StateSnapshot:
        if not self._target_path:
            raise BackendError("restart() without a prior launch()")
        self.terminate()
        return self.launch(self._target_path, self._target_args)

    def terminate(self) -> None:
        pid = None
        try:
            pid = int(self._dbg.pid)
        except Exception:
            pass
        try:
            self._dbg.terminate()
        except Exception:
            pass
        if pid:
            _hard_kill(pid)
        # NOTE: do NOT call Release() here — the MCP server keeps ONE adapter
        # alive across sessions; Release() would kill pybag's worker thread and
        # break all subsequent launches.

    def _release(self) -> None:
        """Stop pybag's worker thread (only when the adapter is discarded)."""
        try:
            self._dbg.Release()
        except Exception:
            pass

    def detach(self) -> None:
        self._dbg.detach()

    # -- Execution ---------------------------------------------------------
    def run(self, timeout: float = 10.0) -> StateSnapshot:
        self._clear_event()
        # NOTE: timeout MUST be an int — DbgEng's WaitForEvent takes a ULONG
        # and pybag silently swallows the TypeError a float causes (see M1
        # report); a float makes run() return immediately without waiting.
        self._dbg.go(int(timeout))
        return self._snapshot(False, 3, 3)   # lean after-state

    def pause(self) -> StateSnapshot:
        self._clear_event()
        try:
            self._dbg._control.SetInterrupt(DbgEng.DEBUG_INTERRUPT_ACTIVE)
        except Exception:
            pass
        self._dbg.wait(5)
        return self._snapshot(False, 3, 3)

    def step(self, mode: str = "into") -> StateSnapshot:
        self._clear_event()
        if mode == "over":
            self._dbg.stepo(1)
        elif mode == "out":
            self._dbg.stepout()
        else:
            self._dbg.stepi(1)
        return self._snapshot(False, 3, 3)

    # -- Observation / Event -----------------------------------------------
    def wait_event(self, timeout: float = 10.0) -> dict:
        self._clear_event()
        waited = self._dbg.wait(int(timeout))
        ev = dict(self._last_event)
        ev["waited"] = bool(waited)
        return ev

    def _snapshot(self, include_modules: bool, backtrace_frames: int,
                  disasm_count: int, include_regs: bool = True) -> StateSnapshot:
        pc = int(self._dbg.reg.get_pc())
        sp = int(self._dbg.reg.get_sp())
        reason, exc, bp = self._interpret_event(self._last_event)
        return StateSnapshot(
            pid=int(self._dbg.pid),
            status=self._dbg.exec_status(),
            pc=pc,
            sp=sp,
            symbol_at_pc=self._dbg.get_name_by_offset(pc),
            stop_reason=reason,
            registers=self._read_regs() if include_regs else {},
            modules=self._modules() if include_modules else [],
            backtrace=self._backtrace(backtrace_frames),
            exception=exc,
            breakpoint=bp,
            disassembly=self.disassemble(pc, disasm_count),
        )

    def observe(self) -> StateSnapshot:
        # Full context, on demand: modules + 6-frame backtrace + 4 insns.
        return self._snapshot(True, 6, 4)

    def snapshot(self) -> StateSnapshot:
        return self.observe()

    # -- Inspection --------------------------------------------------------
    def read_memory(self, address: int, size: int) -> bytes:
        try:
            return self._dbg.read(address, size)
        except Exception as e:
            raise BackendError(f"read_memory(0x{address:x}, {size}) failed: {e}") from e

    def write_memory(self, address: int, data: bytes) -> None:
        """Write bytes to virtual memory (exploit-dev: patch/verify assumptions)."""
        try:
            self._dbg.write(int(address), bytes(data))
        except Exception as e:
            raise BackendError(f"write_memory(0x{address:x}, {len(data)}B) failed: {e}") from e

    def get_register(self, name: str) -> int:
        return int(self._dbg.reg[name])

    def set_register(self, name: str, value: int) -> None:
        """Set a register (exploit-dev: force a control-flow value to test a hypothesis)."""
        self._dbg.reg[name] = int(value)

    def disassemble(self, address: int, count: int = 8) -> List[Instruction]:
        out: List[Instruction] = []
        addr = address
        try:
            from pybag.dbgeng import util as pu
            for _ in range(count):
                ins = pu.disassemble_instruction(
                    self._dbg.bitness(), addr, self._dbg.read(addr, 15))
                if ins is None:
                    break
                out.append(Instruction(
                    address=ins.address,
                    bytes=ins.bytes.hex(),
                    mnemonic=ins.mnemonic,
                    operands=ins.op_str,
                ))
                addr += ins.size
        except Exception:
            out = []
        return out

    # -- Control -----------------------------------------------------------
    def breakpoint_add(self, expr: str, condition: Optional[str] = None) -> BreakpointInfo:
        bpid = int(self._dbg.bp(expr))
        if condition:
            # DbgEng conditional breakpoint: stop only when `condition` is true.
            # GetBreakpointById already returns a DebugBreakpoint wrapper.
            bp = self._dbg._control.GetBreakpointById(bpid)
            bp.SetCommand((".if (%s) {} .else {gc}" % condition).encode())
        addr = None
        try:
            addr = self._dbg.symbol(expr)
        except Exception:
            addr = None
        info = BreakpointInfo(id=bpid, address=addr, symbol=expr)
        self._bps[bpid] = info
        return info

    def breakpoint_add_hw(self, address: int, size: int = 8, access: str = "write") -> BreakpointInfo:
        """Hardware/data breakpoint (watchpoint). access: read | write | execute."""
        acc = {
            "read": DbgEng.DEBUG_BREAK_READ,
            "write": DbgEng.DEBUG_BREAK_WRITE,
            "execute": DbgEng.DEBUG_BREAK_EXECUTE,
        }.get(access, DbgEng.DEBUG_BREAK_WRITE)
        bpid = int(self._dbg.ba(int(address), size=int(size), access=acc))
        info = BreakpointInfo(id=bpid, address=int(address), symbol=f"hw:{access}")
        self._bps[bpid] = info
        return info

    def breakpoint_remove(self, bp_id: int) -> None:
        try:
            self._dbg.bc(bp_id)
        except Exception:
            pass
        self._bps.pop(bp_id, None)

    def breakpoint_list(self) -> List[BreakpointInfo]:
        return list(self._bps.values())

    # -- helpers -----------------------------------------------------------
    def _read_regs(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in KEY_REGS:
            try:
                out[r] = int(self._dbg.reg[r])
            except Exception:
                pass
        return out

    def _modules(self) -> List[Module]:
        mods: List[Module] = []
        try:
            for m in self._dbg.module_list():
                mods.append(Module(name=m[0][0], base=int(m[1].Base), size=int(m[1].Size)))
        except Exception:
            pass
        return mods

    def _backtrace(self, max_frames: int = 12) -> List[Frame]:
        frames: List[Frame] = []
        try:
            for f in self._dbg.backtrace_list():
                if len(frames) >= max_frames:
                    break
                frames.append(Frame(
                    index=f.FrameNumber,
                    ip=int(f.InstructionOffset),
                    symbol=self._dbg.get_name_by_offset(int(f.InstructionOffset)),
                    ret=int(f.ReturnOffset),
                    stack=int(f.StackOffset),
                ))
        except Exception:
            pass
        return frames

    def _interpret_event(self, ev: Dict):
        """Map the raw last event to (StopReason, ExceptionInfo|None, BreakpointInfo|None)."""
        if not ev:
            return StopReason.UNKNOWN, None, None
        if ev.get("type") == "breakpoint":
            self._initial_break_pending = False
            bp = BreakpointInfo(
                id=int(ev.get("bp_id", -1)),
                address=int(ev["bp_offset"]) if "bp_offset" in ev else None,
            )
            return StopReason.BREAKPOINT, None, bp
        if ev.get("type") == "exception":
            code = int(ev.get("code", 0))
            if code == EXCEPTION_BREAKPOINT:
                # First 0x80000003 after launch/attach is the initial break;
                # later ones (e.g. RtlpBreakPointHeap) are real breakpoint
                # exceptions and should surface as EXCEPTION, not INITIAL_BREAK.
                if self._initial_break_pending:
                    self._initial_break_pending = False
                    return StopReason.INITIAL_BREAK, None, None
                exc = ExceptionInfo(
                    code=code,
                    address=int(ev.get("address", 0)),
                    first_chance=bool(ev.get("first_chance", True)),
                    params=list(ev.get("params", [])),
                )
                return StopReason.EXCEPTION, exc, None
            self._initial_break_pending = False
            if code == EXCEPTION_SINGLE_STEP:
                return StopReason.STEP, None, None
            exc = ExceptionInfo(
                code=code,
                address=int(ev.get("address", 0)),
                first_chance=bool(ev.get("first_chance", True)),
                params=list(ev.get("params", [])),
            )
            return StopReason.EXCEPTION, exc, None
        return StopReason.UNKNOWN, None, None
