"""RawDbgEng — low-level DbgEng accessor set (the A/B control arm).

Mimics the fine-grained tool shape of x64dbg-MCP-style bindings: every call
returns ONE piece of state. There is no composed snapshot, and `run` does NOT
report a stop_reason — the caller must assemble the picture itself with several
separate calls. Same DbgEng backend as the high-level adapter; only the tool
abstraction differs, which is exactly the variable the A/B isolates.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBGENG_DIR = PROJECT_ROOT / "vendor" / "dbgeng"

os.environ.setdefault("WINDBG_DIR", str(DBGENG_DIR))
os.environ["PATH"] = str(DBGENG_DIR) + os.pathsep + os.environ.get("PATH", "")

from pybag.userdbg import UserDbg                 # noqa: E402
from pybag.dbgeng import core as DbgEng           # noqa: E402
from pybag.dbgeng.idebugbreakpoint import DebugBreakpoint  # noqa: E402

KEY_REGS = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "rip",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags",
            "cs", "ss", "ds", "es", "fs", "gs"]


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


class RawDbgEng:
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
        self._dbg.events.breakpoint(self._on_breakpoint)
        self._dbg.events.exception(self._on_exception)

    # -- event capture (exception-safe) ------------------------------------
    def _on_breakpoint(self, *args) -> int:
        self._last_event = {"type": "breakpoint"}
        try:
            bp = DebugBreakpoint(args[0])
            self._last_event["bp_id"] = int(bp.GetId())
            self._last_event["bp_offset"] = int(bp.GetOffset())
        except Exception:
            pass
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
        except Exception:
            pass
        return DbgEng.DEBUG_STATUS_BREAK

    # -- session -----------------------------------------------------------
    def launch(self, path: str, args: Optional[List[str]] = None) -> dict:
        self._target_path = os.path.abspath(path)
        self._target_args = args or []
        cmdline = self._target_path
        if self._target_args:
            cmdline += " " + " ".join(self._target_args)
        self._last_event = {}
        self._dbg._client.CreateProcess(cmdline, DbgEng.DEBUG_ONLY_THIS_PROCESS | 0x8)
        self._dbg._control.AddEngineOptions(DbgEng.DEBUG_ENGINITIAL_BREAK)
        self._dbg.wait(10)
        return {"pid": int(self._dbg.pid)}

    def attach(self, pid: int) -> dict:
        self._last_event = {}
        self._dbg.attach(int(pid), initial_break=True)
        return {"pid": int(self._dbg.pid)}

    def restart(self) -> dict:
        self.terminate()
        return self.launch(self._target_path, self._target_args)

    def terminate(self) -> dict:
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
        # NOTE: do NOT call Release() here — keep the adapter reusable.
        return {}

    def detach(self) -> dict:
        self._dbg.detach()
        return {}

    # -- execution (minimal return: no snapshot, no stop_reason) -----------
    def run(self, timeout: float = 10.0) -> dict:
        self._last_event = {}
        self._dbg.go(int(timeout))
        status = self._dbg.exec_status()
        return {"status": status, "stopped": status == "BREAK"}

    def step(self, mode: str = "into") -> dict:
        self._last_event = {}
        if mode == "over":
            self._dbg.stepo(1)
        elif mode == "out":
            self._dbg.stepout()
        else:
            self._dbg.stepi(1)
        status = self._dbg.exec_status()
        return {"status": status, "stopped": status == "BREAK"}

    def pause(self) -> dict:
        self._last_event = {}
        try:
            self._dbg._control.SetInterrupt(DbgEng.DEBUG_INTERRUPT_ACTIVE)
        except Exception:
            pass
        self._dbg.wait(5)
        status = self._dbg.exec_status()
        return {"status": status, "stopped": status == "BREAK"}

    # -- single-purpose state accessors ------------------------------------
    def get_pc(self) -> str:
        return hex(int(self._dbg.reg.get_pc()))

    def get_sp(self) -> str:
        return hex(int(self._dbg.reg.get_sp()))

    def get_register(self, name: str) -> str:
        return hex(int(self._dbg.reg[name]))

    def get_registers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for r in KEY_REGS:
            try:
                out[r] = hex(int(self._dbg.reg[r]))
            except Exception:
                pass
        return out

    def read_memory(self, address: int, size: int) -> dict:
        return {"hex": self._dbg.read(int(address), int(size)).hex()}

    def disassemble(self, address: int, count: int = 8) -> dict:
        out = []
        addr = int(address)
        try:
            from pybag.dbgeng import util as pu
            for _ in range(int(count)):
                ins = pu.disassemble_instruction(
                    self._dbg.bitness(), addr, self._dbg.read(addr, 15))
                if ins is None:
                    break
                out.append({
                    "address": hex(ins.address),
                    "bytes": ins.bytes.hex(),
                    "mnemonic": ins.mnemonic,
                    "operands": ins.op_str,
                })
                addr += ins.size
        except Exception:
            pass
        return {"instructions": out}

    def get_modules(self) -> list:
        mods = []
        try:
            for m in self._dbg.module_list():
                mods.append({"name": m[0][0], "base": hex(int(m[1].Base)), "size": hex(int(m[1].Size))})
        except Exception:
            pass
        return mods

    def get_backtrace(self, max_frames: int = 12) -> list:
        frames = []
        try:
            for f in self._dbg.backtrace_list():
                if len(frames) >= max_frames:
                    break
                frames.append({
                    "frame": f.FrameNumber,
                    "ip": hex(int(f.InstructionOffset)),
                    "symbol": self._dbg.get_name_by_offset(int(f.InstructionOffset)),
                    "ret": hex(int(f.ReturnOffset)),
                    "stack": hex(int(f.StackOffset)),
                })
        except Exception:
            pass
        return frames

    def get_symbol_name(self, address: int) -> str:
        return self._dbg.get_name_by_offset(int(address))

    def get_exception_info(self) -> Optional[dict]:
        if self._last_event.get("type") != "exception":
            return None
        e = self._last_event
        return {
            "code": hex(e.get("code", 0)),
            "address": hex(e.get("address", 0)),
            "first_chance": bool(e.get("first_chance", True)),
            "params": [hex(p) for p in e.get("params", [])],
        }

    def get_breakpoint_info(self) -> Optional[dict]:
        if self._last_event.get("type") != "breakpoint":
            return None
        return {
            "id": self._last_event.get("bp_id"),
            "address": hex(self._last_event["bp_offset"]) if "bp_offset" in self._last_event else None,
        }

    # -- breakpoints -------------------------------------------------------
    def breakpoint_add(self, expr: str) -> dict:
        bpid = int(self._dbg.bp(expr))
        addr = None
        try:
            addr = self._dbg.symbol(expr)
        except Exception:
            addr = None
        return {"id": bpid, "address": hex(addr) if addr is not None else None}

    def breakpoint_remove(self, bp_id: int) -> dict:
        try:
            self._dbg.bc(int(bp_id))
        except Exception:
            pass
        return {}

    def breakpoint_list(self) -> dict:
        return {"breakpoints": []}
