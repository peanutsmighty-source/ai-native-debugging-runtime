"""DbgEngAdapter — implements the Core DebugSession interface on top of DbgEng.

Thin, debugger-specific translation of the PRD primitives. All reasoning /
strategy stays in the agent; this layer only turns debugger state into stable,
structured Core types and deterministic actions into DbgEng calls.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections import deque
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
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "efl"]


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

        # Event queue (Event primitive): every debugger event is enqueued with
        # a sequence number so run()/wait_event() can observe intermediate
        # events (thread create, module load, ...) instead of only the last one.
        self._event_queue: "deque[Dict]" = deque()
        self._event_lock = threading.Lock()
        self._event_seq = 0

        # Exception-safe event handlers (must NEVER raise — see M1 report).
        self._dbg.events.breakpoint(self._on_breakpoint)
        self._dbg.events.exception(self._on_exception)
        self._dbg.events.create_thread(self._on_info_event("thread_create"))
        self._dbg.events.exit_thread(self._on_info_event("thread_exit"))
        self._dbg.events.module_load(self._on_info_event("module_load"))
        self._dbg.events.unload_module(self._on_info_event("module_unload"))
        self._dbg.events.exit_process(self._on_info_event("process_exit", stop=True))

    # -- event capture -----------------------------------------------------
    def _next_seq(self) -> int:
        with self._event_lock:
            self._event_seq += 1
            return self._event_seq

    def _enqueue(self, ev: Dict) -> None:
        with self._event_lock:
            self._event_queue.append(ev)
        self._last_event = ev          # most recent event drives stop_reason

    def _drain_events(self) -> List[Dict]:
        """Consume and return all currently queued events (FIFO)."""
        with self._event_lock:
            out = list(self._event_queue)
            self._event_queue.clear()
        return out

    def _on_info_event(self, etype: str, stop: bool = False):
        """Factory for informational events (thread/module/process-exit).

        Returns DEBUG_STATUS_NO_CHANGE so execution continues through them
        (they are queued for the Event primitive, not treated as stops),
        except process_exit which stops so the agent can observe the exit.
        """
        def handler(*args):
            ev: Dict = {"type": etype, "seq": self._next_seq()}
            try:
                if etype == "thread_create" and len(args) >= 3:
                    ev["start_offset"] = hex(int(args[2]))
                elif etype in ("module_load", "process_create") and len(args) >= 2:
                    ev["base"] = hex(int(args[1]))
                    if etype == "module_load" and len(args) >= 5 and args[3]:
                        ev["name"] = str(args[3])
                elif etype in ("thread_exit", "process_exit") and len(args) >= 1:
                    ev["exit_code"] = int(args[0])
            except Exception as e:
                ev["handler_error"] = repr(e)
            self._enqueue(ev)
            return DbgEng.DEBUG_STATUS_BREAK if stop else DbgEng.DEBUG_STATUS_NO_CHANGE
        return handler

    def _on_breakpoint(self, *args) -> int:
        ev: Dict = {"type": "breakpoint", "seq": self._next_seq()}
        try:
            bp = DebugBreakpoint(args[0])
            ev["bp_id"] = int(bp.GetId())
            ev["bp_offset"] = int(bp.GetOffset())
        except Exception as e:
            ev["handler_error"] = repr(e)
        self._enqueue(ev)
        return DbgEng.DEBUG_STATUS_BREAK

    def _on_exception(self, record, first_chance) -> int:
        ev: Dict = {"type": "exception", "seq": self._next_seq()}
        try:
            rec = getattr(record, "contents", record)
            ev["code"] = int(rec.ExceptionCode)
            ev["address"] = int(rec.ExceptionAddress)
            ev["first_chance"] = bool(first_chance)
            # Tag the initial break once, at event time, so _interpret_event
            # stays idempotent (it may be re-interpreted by observe()).
            if ev["code"] == EXCEPTION_BREAKPOINT and self._initial_break_pending:
                ev["initial_break"] = True
            self._initial_break_pending = False
            try:
                ev["params"] = [
                    int(rec.ExceptionInformation[i]) for i in range(2)
                ]
            except Exception:
                pass
        except Exception as e:
            ev["handler_error"] = repr(e)
        self._enqueue(ev)
        return DbgEng.DEBUG_STATUS_BREAK

    def _clear_event(self) -> None:
        self._last_event = {}
        with self._event_lock:
            self._event_queue.clear()

    # -- Session -----------------------------------------------------------
    def launch(self, path: str, args: Optional[List[str]] = None,
               stdin_data: Optional[bytes] = None) -> StateSnapshot:
        self._target_path = os.path.abspath(path)
        self._target_args = args or []
        # Proper Win32 quoting (handles paths/args with spaces or metacharacters).
        cmdline = subprocess.list2cmdline([self._target_path] + self._target_args)

        self._clear_event()
        if stdin_data is not None:
            # Redirect the debuggee's stdin to a temp file and stdout/stderr to
            # NUL via inheritable handles (headless-safe; input callbacks only
            # work for console apps).
            from .stdio_redirect import redirect_stdlib
            restore = redirect_stdlib(stdin_data)
            options = DbgEng._DEBUG_CREATE_PROCESS_OPTIONS()
            options.CreateFlags = DbgEng.DEBUG_ONLY_THIS_PROCESS
            options.EngCreateFlags = DbgEng.DEBUG_ECREATE_PROCESS_INHERIT_HANDLES
            try:
                self._dbg._client.CreateProcess2(cmdline, options, None, None)
            finally:
                restore()
        else:
            # DETACHED_PROCESS (0x8) keeps the debuggee from inheriting our std
            # handles, so its printf does not pollute the CLI/MCP stdio stream.
            flags = DbgEng.DEBUG_ONLY_THIS_PROCESS | 0x8
            self._dbg._client.CreateProcess(cmdline, flags)
        self._initial_break_pending = True
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

    # -- Threads -----------------------------------------------------------
    def thread_list(self) -> List[dict]:
        threads: List[dict] = []
        try:
            ids, sysids = self._dbg._systems.GetThreadIdsByIndex()
            curid = self._dbg._systems.GetCurrentThreadId()
            for eid, sysid in zip(ids, sysids):
                self._dbg._systems.SetCurrentThreadId(eid)
                pc = int(self._dbg.reg.get_pc())
                threads.append({
                    "index": int(eid),
                    "tid": int(sysid),
                    "teb": hex(int(self._dbg._systems.GetCurrentThreadTeb())),
                    "pc": hex(pc),
                    "symbol": self._dbg.get_name_by_offset(pc),
                })
            self._dbg._systems.SetCurrentThreadId(curid)
        except Exception as e:
            raise BackendError(f"thread_list failed: {e}") from e
        return threads

    def set_thread(self, index: int) -> None:
        try:
            self._dbg._systems.SetCurrentThreadId(int(index))
        except Exception as e:
            raise BackendError(f"set_thread({index}) failed: {e}") from e

    def get_thread(self) -> int:
        try:
            return int(self._dbg._systems.GetCurrentThreadId())
        except Exception as e:
            raise BackendError(f"get_thread failed: {e}") from e

    # -- Observation / Event -----------------------------------------------
    def wait_event(self, timeout: float = 10.0) -> dict:
        """Event primitive: return queued events (FIFO), blocking up to
        ``timeout`` seconds for the next debugger event if none are queued.

        Returns {"events": [...], "waited": bool} — events are the raw
        callback dicts (type/seq/code/address/...) in arrival order.
        """
        queued = self._drain_events()
        if queued:
            return {"events": queued, "waited": False}
        waited = self._dbg.wait(int(timeout))
        queued = self._drain_events()
        return {"events": queued, "waited": bool(waited)}

    def _snapshot(self, include_modules: bool, backtrace_frames: int,
                  disasm_count: int, include_regs: bool = True) -> StateSnapshot:
        errors: List[str] = []
        pc = sp = 0
        try:
            pc = int(self._dbg.reg.get_pc())
        except Exception as e:
            errors.append(f"pc: {e}")          # process may have exited
        try:
            sp = int(self._dbg.reg.get_sp())
        except Exception as e:
            errors.append(f"sp: {e}")
        reason, exc, bp = self._interpret_event(self._last_event)
        try:
            dis = self.disassemble(pc, disasm_count)
        except BackendError as e:
            dis = []
            errors.append(f"disassemble: {e}")
        try:
            sym = self._dbg.get_name_by_offset(pc) if pc else None
        except Exception:
            sym = None
        try:
            status = self._dbg.exec_status()
        except Exception:
            status = None
        try:
            pid = int(self._dbg.pid)
        except Exception:
            pid = None
        return StateSnapshot(
            pid=pid,
            status=status,
            pc=pc,
            sp=sp,
            symbol_at_pc=sym,
            stop_reason=reason,
            registers=self._read_regs(errors) if include_regs else {},
            modules=self._modules(errors) if include_modules else [],
            backtrace=self._backtrace(backtrace_frames, errors),
            exception=exc,
            breakpoint=bp,
            disassembly=dis,
            errors=errors,
        )

    def observe(self) -> StateSnapshot:
        # Full context, on demand: modules + 6-frame backtrace + 4 insns.
        return self._snapshot(True, 6, 4)

    # -- Experiment (snapshot / restore) ------------------------------------
    def snapshot(self, regions: Optional[List[tuple]] = None) -> dict:
        """Experiment primitive: capture restorable state.

        ``regions`` = optional list of (address, size) memory ranges to save
        along with the register file (current thread) and breakpoint set.
        Returns a JSON-safe dict; pass it back to restore().
        """
        regs = self._read_regs()
        mem = []
        for addr, size in (regions or []):
            try:
                data = self.read_memory(int(addr), int(size))
                mem.append({
                    "address": hex(int(addr)),
                    "size": int(size),
                    "data": data.hex(),
                })
            except BackendError:
                pass   # unmapped region: skip silently
        bps = [
            {"id": b.id, "address": hex(b.address) if b.address else None,
             "symbol": b.symbol}
            for b in self._bps.values()
        ]
        snap: Dict = {
            "pid": int(self._dbg.pid),
            "thread": self.get_thread(),
            "registers": {k: hex(v) for k, v in regs.items()},
            "memory": mem,
            "breakpoints": bps,
        }
        try:
            snap["pc"] = hex(int(self._dbg.reg.get_pc()))
            snap["sp"] = hex(int(self._dbg.reg.get_sp()))
        except Exception:
            pass
        return snap

    def restore(self, snap: dict) -> None:
        """Experiment primitive: write back a snapshot captured by snapshot().

        Best-effort: restores registers (current thread), saved memory ranges
        and re-adds missing breakpoints. A full process checkpoint is not
        possible on Windows; this restores the agent-visible state.
        """
        if not isinstance(snap, dict):
            raise BackendError("restore() expects a snapshot dict from snapshot()")
        if "registers" in snap:
            regs = snap["registers"]
            # non-rip first, rip last (rip steers execution).
            order = [r for r in KEY_REGS if r in regs and r != "rip"]
            if "rip" in regs:
                order.append("rip")
            for r in order:
                try:
                    self.set_register(r, int(regs[r], 16))
                except Exception:
                    pass
        for m in snap.get("memory", []):
            try:
                self.write_memory(int(m["address"], 16), bytes.fromhex(m["data"]))
            except Exception:
                pass
        existing = {b.address for b in self._bps.values()}
        for b in snap.get("breakpoints", []):
            addr = b.get("address")
            if addr and int(addr, 16) not in existing:
                try:
                    self.breakpoint_add("0x%x" % int(addr, 16))
                except Exception:
                    pass

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
        except Exception as e:
            raise BackendError(f"disassemble(0x{address:x}) failed: {e}") from e
        return out

    def search_memory(self, address: int, size: int, pattern: bytes) -> List[int]:
        """Search [address, address+size) for a byte pattern; return match addrs."""
        results: List[int] = []
        if not pattern:
            return results
        CHUNK = 0x10000
        overlap = len(pattern) - 1
        carry = b""
        pos = address
        end = address + size
        while pos < end:
            chunk_size = min(CHUNK, end - pos)
            try:
                data = self.read_memory(pos, chunk_size)
            except BackendError:
                break
            buf = carry + data
            idx = 0
            while True:
                idx = buf.find(pattern, idx)
                if idx == -1:
                    break
                results.append(pos - len(carry) + idx)
                idx += 1
            carry = data[-overlap:] if overlap > 0 else b""
            pos += chunk_size
        return results

    def module_base(self, module: str) -> Module:
        """Resolve a module by (case-insensitive) basename."""
        target = Path(module).name.lower()
        for m in self._modules():
            name = Path(m.name).name.lower()
            if target == name or target in name:
                return m
        raise BackendError(f"module {module!r} not loaded")

    def find_gadget(self, module: str, gadget: List[str], limit: int = 20) -> List[int]:
        """Find addresses of a ROP gadget sequence (list of instruction mnemonics)
        inside a module's image."""
        from capstone import Cs, CS_ARCH_X86, CS_MODE_64
        m = self.module_base(module)
        base, size = m.base, m.size
        if size > 0x800000:
            size = 0x800000  # cap linear scan at 8MB
        try:
            code = self.read_memory(base, size)
        except BackendError as e:
            raise BackendError(f"find_gadget: cannot read module image: {e}") from e
        md = Cs(CS_ARCH_X86, CS_MODE_64)
        md.skipdata = True
        insns = list(md.disasm(code, base))
        n = len(gadget)
        results: List[int] = []
        for i in range(len(insns) - n + 1):
            window = insns[i:i + n]
            if all(self._match_gadget(spec, ins) for spec, ins in zip(gadget, window)):
                results.append(window[0].address)
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def _match_gadget(spec: str, ins) -> bool:
        spec = spec.strip().lower()
        mnem = ins.mnemonic.lower()
        if spec == "ret":
            return mnem in ("ret", "retf", "retn")
        if spec == mnem:
            return True
        return f"{mnem} {ins.op_str}".strip().lower() == spec

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
    def _read_regs(self, errors: Optional[List[str]] = None) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in KEY_REGS:
            try:
                out[r] = int(self._dbg.reg[r])
            except Exception as e:
                if errors is not None:
                    errors.append(f"register {r}: {e}")
        return out

    def _modules(self, errors: Optional[List[str]] = None) -> List[Module]:
        mods: List[Module] = []
        try:
            for m in self._dbg.module_list():
                mods.append(Module(name=m[0][0], base=int(m[1].Base), size=int(m[1].Size)))
        except Exception as e:
            if errors is not None:
                errors.append(f"modules: {e}")
        return mods

    def _backtrace(self, max_frames: int = 12, errors: Optional[List[str]] = None) -> List[Frame]:
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
        except Exception as e:
            if errors is not None:
                errors.append(f"backtrace: {e}")
        return frames

    def _interpret_event(self, ev: Dict):
        """Map the raw last event to (StopReason, ExceptionInfo|None, BreakpointInfo|None).

        Idempotent: the initial-break tag is stamped at event time in
        _on_exception, so repeated interpretation yields the same result.
        """
        if not ev:
            return StopReason.UNKNOWN, None, None
        if ev.get("type") == "breakpoint":
            bp = BreakpointInfo(
                id=int(ev.get("bp_id", -1)),
                address=int(ev["bp_offset"]) if "bp_offset" in ev else None,
            )
            return StopReason.BREAKPOINT, None, bp
        if ev.get("type") == "exception":
            code = int(ev.get("code", 0))
            if code == EXCEPTION_BREAKPOINT:
                # Tagged initial break (stamped in _on_exception); later
                # 0x80000003 (e.g. RtlpBreakPointHeap) surfaces as EXCEPTION.
                if ev.get("initial_break"):
                    return StopReason.INITIAL_BREAK, None, None
                exc = ExceptionInfo(
                    code=code,
                    address=int(ev.get("address", 0)),
                    first_chance=bool(ev.get("first_chance", True)),
                    params=list(ev.get("params", [])),
                )
                return StopReason.EXCEPTION, exc, None
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
