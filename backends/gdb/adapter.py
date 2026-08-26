"""GdbAdapter — implements the Core DebugSession interface on top of gdb's
Machine Interface (MI2) protocol, driven through a long-lived interactive
gdb process. This is the SECOND backend: it proves the DebugSession
abstraction is debugger-agnostic (the same pytest suite runs against it).

gdb 17.x (mingw-w64) supports DWARF symbols for our mingw targets — richer
than DbgEng's export-only symbol table (e.g. non-exported `main` resolves).

Protocol notes:
  * Commands are `-command` lines; responses arrive as records ending with a
    `(gdb)` prompt line. We read lines until the prompt.
  * Stop events arrive as `*stopped,reason=...` (signal-received / breakpoint-hit
    / exited / ...). We keep the latest one for stop_reason and also queue it
    for wait_event (Event primitive).
  * Register names are dynamic (use -data-list-register-names).
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

from core.session import BackendError, DebugSession
from core.types import (
    BreakpointInfo,
    ExceptionInfo,
    Frame,
    Instruction,
    Module,
    StateSnapshot,
    StopReason,
)

GDB = r"C:\Users\WHO\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\gdb.exe"

# x86-64 general registers (gdb numbering is stable, but we resolve by name).
KEY_REGS = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "rip",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags"]

_SIGNAL_TO_CODE = {
    "SIGSEGV": 0xC0000005,
    "SIGILL": 0xC000001D,
    "SIGFPE": 0xC0000094,
    "SIGABRT": 0xC0000409,
    "SIGTRAP": 0x80000003,
}


def _hard_kill(pid: int) -> None:
    try:
        import ctypes
        PROCESS_TERMINATE = 0x0001
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if h:
            ctypes.windll.kernel32.TerminateProcess(h, 1)
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass


class GdbAdapter(DebugSession):
    def __init__(self, gdb_path: str = GDB):
        self._gdb_path = gdb_path
        self._proc = subprocess.Popen(
            [gdb_path, "--interpreter=mi2", "--quiet"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        self._q: "queue.Queue[str]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._target_path: Optional[str] = None
        self._target_args: Optional[List[str]] = None
        self._reg_names: List[str] = []
        self._reg_index: Dict[str, int] = {}
        self._last_stop: Dict = {}          # latest *stopped event
        self._event_queue: List[Dict] = []
        self._bps: Dict[int, BreakpointInfo] = {}
        self._bp_seq = 0
        self._pending_initial_break = False
        self._last_breakpoint_hit: Optional[int] = None
        self._pid_cache: Optional[int] = None
        self._consume_until_prompt(5)       # drain startup banner
        self._read_register_names()

    # -- MI plumbing --------------------------------------------------------
    def _read_loop(self):
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            self._q.put(line.decode(errors="replace").rstrip())

    def _consume_until_prompt(self, timeout: float = 10.0) -> List[str]:
        import time
        lines: List[str] = []
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self._q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                if time.monotonic() >= deadline:
                    break
                continue
            lines.append(line)
            if line == "(gdb)":
                break
        return lines

    def _mi(self, cmd: str, timeout: float = 15.0) -> List[str]:
        """Send one synchronous MI/CLI command; return output lines up to the
        prompt. Caller must ensure the queue is drained (see _mi_exec)."""
        if self._proc.poll() is not None:
            raise BackendError("gdb process exited")
        try:
            self._proc.stdin.write((cmd + "\n").encode())
            self._proc.stdin.flush()
        except Exception as e:
            raise BackendError(f"gdb write failed: {e}") from e
        return self._consume_until_prompt(timeout)

    def _mi_exec(self, cmd: str, timeout: float = 15.0) -> List[str]:
        """Send an exec command (-exec-run/-exec-continue/-exec-step) and read
        until the *stopped record arrives (exec commands are ASYNC: gdb first
        answers ^running+(gdb), then the stop event arrives later, followed by
        another (gdb) prompt)."""
        import time
        if self._proc.poll() is not None:
            raise BackendError("gdb process exited")
        try:
            self._proc.stdin.write((cmd + "\n").encode())
            self._proc.stdin.flush()
        except Exception as e:
            raise BackendError(f"gdb write failed: {e}") from e
        lines: List[str] = []
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            try:
                line = self._q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            lines.append(line)
            if "*stopped" in line:
                # consume the trailing (gdb) prompt
                while time.monotonic() < deadline:
                    try:
                        tail = self._q.get(timeout=max(0.05, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    lines.append(tail)
                    if tail == "(gdb)":
                        break
                break
        return lines

    @staticmethod
    def _parse_kv(line: str) -> Optional[Dict]:
        """Parse `^done,key=value,key2=...` or `*stopped,key=...` records."""
        m = re.match(r"^(\^|\*|=|\~|\&)([a-z-]+)?(?:,(.*))?$", line)
        if not m:
            return None
        rec = {"kind": m.group(1), "name": m.group(2), "fields": {}}
        rest = m.group(3) or ""
        for k, v in re.findall(r"([a-zA-Z0-9_-]+)=(.*?)(?:,(?=[a-zA-Z0-9_-]+=)|$)", rest):
            rec["fields"][k] = v.strip('"')
        return rec

    def _last_record(self, lines: List[str], kind: str) -> Optional[Dict]:
        for line in reversed(lines):
            rec = self._parse_kv(line)
            if rec and rec["kind"] == kind:
                return rec
        return None

    def _read_register_names(self):
        out = self._mi("-data-list-register-names", 5)
        for line in out:
            m = re.match(r"\^done,register-names=\[(.*)\]$", line)
            if m:
                self._reg_names = re.findall(r'"(.*?)"', m.group(1))
                for i, name in enumerate(self._reg_names):
                    if name:
                        self._reg_index[name] = i
                break

    def _reg_value(self, name: str) -> int:
        """Read a 64-bit register via the `info registers` CLI output.

        `-data-list-register-values` and `-data-evaluate-expression $reg` are
        unreliable on mingw gdb for PE targets (32-bit register name view);
        `info registers` is verified correct.
        """
        out = self._mi(f"info registers {name}", 8)
        for line in out:
            m = re.match(r'~"([a-z0-9]+)\s+(0x[0-9a-fA-F]+)\s', line)
            if m and m.group(1) == name:
                return int(m.group(2), 16)
        raise BackendError(f"register {name!r} not readable")

    def _set_reg_value(self, name: str, value: int) -> None:
        self._mi(f"set ${name} = {value:#x}", 8)

    # -- Session -----------------------------------------------------------
    def launch(self, path: str, args: Optional[List[str]] = None,
               stdin_data: Optional[bytes] = None) -> StateSnapshot:
        # gdb commands treat backslashes as escapes — always forward slashes.
        self._target_path = Path(path).resolve().as_posix()
        self._target_args = [str(a).replace("\\", "/") for a in (args or [])]
        self._mi(f"-file-exec-and-symbols {self._target_path}", 10)
        if self._target_args:
            quoted = " ".join(f'"{a}"' for a in self._target_args)
            self._mi(f"-exec-arguments {quoted}", 5)
        self._last_stop = {}
        self._pending_initial_break = True
        # Align the DebugSession contract: launch stops at main entry
        # (temporary breakpoint, auto-deleted after first hit = initial break).
        self._mi("-break-insert -t main", 5)
        out = self._mi_exec("-exec-run", 10)
        self._process_stop_records(out)
        if self._last_stop.get("reason") == "breakpoint-hit":
            self._last_stop["initial_break"] = True
        return self._snapshot(False, 0, 0, False)

    def attach(self, pid: int) -> StateSnapshot:
        raise BackendError("gdb attach not implemented yet")

    def restart(self) -> StateSnapshot:
        if not self._target_path:
            raise BackendError("restart() without a prior launch()")
        self.terminate()
        return self.launch(self._target_path, self._target_args)

    def terminate(self) -> None:
        pid = self._pid_cache
        try:
            self._mi("-exec-abort", 5)
        except Exception:
            pass
        try:
            self._mi("kill", 5)
        except Exception:
            pass
        if pid:
            _hard_kill(pid)
        self._event_queue = []
        self._pid_cache = None

    def detach(self) -> None:
        try:
            self._mi("-target-detach", 5)
        except Exception:
            pass

    def _pid(self) -> Optional[int]:
        return self._pid_cache

    def _release(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass

    # -- stop-event capture -------------------------------------------------
    def _process_stop_records(self, lines: List[str]):
        for line in lines:
            rec = self._parse_kv(line)
            if rec is None:
                continue
            if rec["kind"] == "*" and rec["name"] == "stopped":
                reason = rec["fields"].get("reason")
                self._last_stop = rec["fields"]
                # normalize stop events to the shared event vocabulary so the
                # same pytest suite runs against both backends
                etype = {"breakpoint-hit": "breakpoint",
                         "signal-received": "exception",
                         "exited": "process_exit",
                         "exited-normally": "process_exit"}.get(reason, "stop")
                ev: Dict = {"type": etype, "seq": len(self._event_queue) + 1}
                if etype == "exception":
                    ev["code"] = _SIGNAL_TO_CODE.get(rec["fields"].get("signal-name"),
                                                     0xC0000005)
                    ev["address"] = self._frame_addr(rec["fields"].get("frame"))
                if etype == "breakpoint" and self._pending_initial_break:
                    ev["initial_break"] = True
                    self._last_stop["initial_break"] = True
                    self._pending_initial_break = False
                self._event_queue.append(ev)
            elif rec["kind"] == "=" and rec["name"] == "thread-group-started":
                pid = rec["fields"].get("pid")
                if pid:
                    self._pid_cache = int(pid)
            elif rec["kind"] == "=" and rec["name"] == "thread-created":
                tid = rec["fields"].get("id")
                self._event_queue.append({"type": "thread_create", "seq": len(self._event_queue) + 1,
                                          "thread": tid})
            elif rec["kind"] == "=" and rec["name"] == "thread-exited":
                tid = rec["fields"].get("id")
                self._event_queue.append({"type": "thread_exit", "seq": len(self._event_queue) + 1,
                                          "thread": tid})
            elif rec["kind"] == "=" and rec["name"] == "library-loaded":
                name = rec["fields"].get("id", "").split("\\")[-1].rstrip('"')
                self._event_queue.append({"type": "module_load", "seq": len(self._event_queue) + 1,
                                          "name": name})

    @staticmethod
    def _frame_addr(frame_field: Optional[str]) -> Optional[int]:
        if not frame_field:
            return None
        m = re.search(r'addr="(0x[0-9a-fA-F]+)"', frame_field)
        return int(m.group(1), 16) if m else None

    def _stop_reason(self) -> StopReason:
        reason = self._last_stop.get("reason")
        if self._last_stop.get("initial_break"):
            return StopReason.INITIAL_BREAK
        if reason == "breakpoint-hit":
            return StopReason.BREAKPOINT
        if reason == "signal-received":
            return StopReason.EXCEPTION
        if reason in ("exited", "exited-normally"):
            return StopReason.UNKNOWN
        return StopReason.UNKNOWN

    # -- Execution ---------------------------------------------------------
    def run(self, timeout: float = 10.0) -> StateSnapshot:
        self._last_stop = {}
        out = self._mi_exec("-exec-continue", timeout)
        self._process_stop_records(out)
        return self._snapshot(False, 3, 3)

    def pause(self) -> StateSnapshot:
        try:
            self._mi("-exec-interrupt", 5)
        except Exception:
            pass
        return self._snapshot(False, 3, 3)

    def step(self, mode: str = "into") -> StateSnapshot:
        self._last_stop = {}
        cmd = {"into": "-exec-step", "over": "-exec-next",
               "out": "-exec-finish"}.get(mode, "-exec-step")
        out = self._mi_exec(cmd, 10)
        self._process_stop_records(out)
        return self._snapshot(False, 3, 3)

    # -- Threads -----------------------------------------------------------
    def thread_list(self) -> List[dict]:
        out = self._mi("-thread-info", 8)
        threads = []
        for line in out:
            for m in re.finditer(r'\{id="(\d+)",target-id="([^"]*)"', line):
                tid, target = m.group(1), m.group(2)
                frame_rest = line[m.end():line.find("}", m.end())]
                addr = re.search(r'addr="(0x[0-9a-fA-F]+)"', frame_rest)
                func = re.search(r'func="([^"]*)"', frame_rest)
                state = re.search(r'state="([^"]*)"', line[m.end():])
                threads.append({
                    "index": int(tid),
                    "tid": int(target.split(".")[-1], 16) if "." in target else target,
                    "pc": addr.group(1) if addr else None,
                    "symbol": func.group(1) if func else None,
                    "state": state.group(1) if state else None,
                })
        return threads

    def set_thread(self, index: int) -> None:
        self._mi(f"-thread-select {int(index)}", 5)

    def get_thread(self) -> int:
        out = self._mi("-thread-info", 5)
        for line in out:
            m = re.search(r'current-thread-id="(\d+)"', line)
            if m:
                return int(m.group(1))
        return 1

    # -- Observation / Event -----------------------------------------------
    def wait_event(self, timeout: float = 10.0) -> dict:
        import time
        if self._event_queue:
            evs = list(self._event_queue)
            self._event_queue = []
            return {"events": evs, "waited": False}
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if self._event_queue:
                evs = list(self._event_queue)
                self._event_queue = []
                return {"events": evs, "waited": True}
        return {"events": [], "waited": False}

    def _snapshot(self, include_modules: bool, backtrace_frames: int,
                  disasm_count: int, include_regs: bool = True) -> StateSnapshot:
        errors: List[str] = []
        try:
            pc = self._reg_value("rip")
        except Exception as e:
            pc = 0
            errors.append(f"pc: {e}")
        try:
            sp = self._reg_value("rsp")
        except Exception as e:
            sp = 0
            errors.append(f"sp: {e}")
        reason = self._stop_reason()
        exc = None
        bp = None
        if reason == StopReason.EXCEPTION:
            sig = self._last_stop.get("signal-name", "SIGSEGV")
            exc = ExceptionInfo(code=_SIGNAL_TO_CODE.get(sig, 0xC0000005),
                                address=pc, first_chance=True, params=[])
        if reason == StopReason.BREAKPOINT:
            bp = BreakpointInfo(id=self._last_breakpoint_hit or -1, address=pc)
        dis = []
        try:
            dis = self.disassemble(pc, disasm_count)
        except BackendError as e:
            errors.append(f"disassemble: {e}")
        try:
            sym = self._symbol_at_pc(pc)
        except Exception:
            sym = None
        regs = self._read_regs(errors) if include_regs else {}
        frames = self._backtrace(backtrace_frames, errors)
        mods = self._modules(errors) if include_modules else []
        return StateSnapshot(
            pid=self._pid(), status="BREAK", pc=pc, sp=sp, symbol_at_pc=sym,
            stop_reason=reason, registers=regs, modules=mods, backtrace=frames,
            exception=exc, breakpoint=bp, disassembly=dis, errors=errors)

    def observe(self) -> StateSnapshot:
        return self._snapshot(True, 6, 4)

    def snapshot(self, regions: Optional[List[tuple]] = None) -> dict:
        regs = self._read_regs()
        mem = []
        for addr, size in (regions or []):
            try:
                data = self.read_memory(int(addr), int(size))
                mem.append({"address": hex(int(addr)), "size": int(size), "data": data.hex()})
            except BackendError:
                pass
        bps = [{"id": b.id, "address": hex(b.address) if b.address else None,
                "symbol": b.symbol} for b in self._bps.values()]
        return {"pid": self._pid(), "thread": self.get_thread(),
                "registers": {k: hex(v) for k, v in regs.items()},
                "memory": mem, "breakpoints": bps,
                "pc": hex(self._reg_value("rip")), "sp": hex(self._reg_value("rsp"))}

    def restore(self, snap: dict) -> None:
        if not isinstance(snap, dict):
            raise BackendError("restore() expects a snapshot dict")
        for r in KEY_REGS:
            if r in snap.get("registers", {}):
                try:
                    self._set_reg_value(r, int(snap["registers"][r], 16))
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
        out = self._mi(f"-data-read-memory-bytes {address:#x} {size}", 10)
        for line in out:
            m = re.search(r'contents="([0-9a-fA-F]*)"', line)
            if m:
                return bytes.fromhex(m.group(1))
        raise BackendError(f"read_memory(0x{address:x}, {size}) failed")

    def write_memory(self, address: int, data: bytes) -> None:
        hexs = data.hex()
        # write in chunks (gdb handles the rest)
        self._mi(f"-data-write-memory-bytes {address:#x} {hexs}", 10)

    def get_register(self, name: str) -> int:
        return self._reg_value(name)

    def set_register(self, name: str, value: int) -> None:
        self._set_reg_value(name, value)

    def resolve_symbol(self, expr: str) -> Optional[int]:
        """Resolve `mod!name` (strip module prefix) via gdb DWARF symbols."""
        return self._gdb_symbol(str(expr).split("!")[-1])

    def disassemble(self, address: int, count: int = 8) -> List[Instruction]:
        out = self._mi(f"-data-disassemble -s {address:#x} -e {address + count * 16:#x} -- 1", 10)
        insns: List[Instruction] = []
        for line in out:
            for m in re.finditer(r'address="(0x[0-9a-fA-F]+)"[^}]*?func-name="([^"]*)"[^}]*?offset="([^"]*)"[^}]*?inst="([^"]*)"', line):
                insns.append(Instruction(
                    address=int(m.group(1), 16), bytes="",
                    mnemonic=m.group(4).split()[0] if m.group(4) else "",
                    operands=" ".join(m.group(4).split()[1:])))
        return insns[:count]

    def search_memory(self, address: int, size: int, pattern: bytes) -> List[int]:
        # gdb's `find` reports matches as console `~"0xADDR ..."` lines;
        # multi-byte patterns are comma-separated.
        results: List[int] = []
        chunk = 0x10000
        pat = ", ".join(f"0x{b:02x}" for b in pattern)
        pos = address
        end = address + size
        while pos < end:
            sz = min(chunk, end - pos)
            out = self._mi(f"find /b {pos:#x}, +{sz:#x}, {pat}", 10)
            for line in out:
                m = re.match(r'~"0x([0-9a-fA-F]+)', line)
                if m:
                    results.append(int(m.group(1), 16))
            pos += chunk
        return results

    def module_base(self, module: str) -> Module:
        target = Path(module).name.lower()
        for m in self._modules():
            if target in Path(m.name).name.lower():
                return m
        # main executable is not in shared-libraries; derive its image base
        # from `info files` (first section without an " in <dll>" suffix,
        # .text starts at base+0x1000 for these mingw images).
        try:
            out = self._mi("info files", 8)
            for line in out:
                if " is ." not in line or " in " in line:
                    continue
                m = re.search(r'0x([0-9a-fA-F]{8,16}) - 0x[0-9a-fA-F]+ is ([a-zA-Z0-9_.]+)', line)
                if m:
                    # mingw PE: .text starts at image base + 0x1000
                    base = (int(m.group(1), 16) - 0x1000) & ~0xFFF
                    return Module(name=module, base=base, size=0)
        except Exception:
            pass
        raise BackendError(f"module {module!r} not loaded")

    def find_gadget(self, module: str, gadget: List[str], limit: int = 20) -> List[int]:
        raise BackendError("find_gadget not implemented for gdb backend yet")

    # -- Control -----------------------------------------------------------
    def breakpoint_add(self, expr, condition: Optional[str] = None) -> BreakpointInfo:
        # gdb uses DWARF names (no `module!` prefix) — strip it for both the
        # breakpoint and the address lookup; int addresses become 0x...
        if isinstance(expr, int):
            name = "*0x%x" % expr            # gdb needs *ADDR for bare addresses
        else:
            name = str(expr).split("!")[-1]
        self._bp_seq += 1
        out = self._mi(f"-break-insert {name}", 8)
        number = self._bp_seq
        for line in out:
            m = re.search(r'number="(\d+)"', line)
            if m:
                number = int(m.group(1))
        addr = None
        try:
            addr = expr if isinstance(expr, int) else (
                int(expr, 16) if str(expr).lower().startswith("0x") else None)
        except Exception:
            addr = None
        if addr is None:
            try:
                addr = self._gdb_symbol(name)
            except Exception:
                addr = None
        info = BreakpointInfo(id=number, address=addr, symbol=str(expr))
        self._bps[number] = info
        return info

    def breakpoint_add_hw(self, address: int, size: int = 8, access: str = "write") -> BreakpointInfo:
        raise BackendError("hardware watchpoints not implemented for gdb backend yet")

    def breakpoint_remove(self, bp_id: int) -> None:
        try:
            self._mi(f"-break-delete {int(bp_id)}", 5)
        except Exception:
            pass
        self._bps.pop(int(bp_id), None)

    def breakpoint_list(self) -> List[BreakpointInfo]:
        return list(self._bps.values())

    # -- helpers -----------------------------------------------------------
    def _gdb_symbol(self, name) -> Optional[int]:
        """Resolve a symbol name to its address via `info address`.

        Returns the address (int) or None. Accepts a DWARF name like
        `crash_here` or an expression like `main+4`.
        """
        out = self._mi(f"info address {name}", 6)
        for line in out:
            m = re.search(r'at (?:address )?(0x[0-9a-fA-F]+)', line)
            if m:
                return int(m.group(1), 16)
        return None

    def _symbol_at_pc(self, addr: int) -> Optional[str]:
        """Resolve an address to a symbol name via `info symbol`."""
        out = self._mi(f"info symbol {addr:#x}", 6)
        for line in out:
            m = re.search(r'~"([A-Za-z_][A-Za-z0-9_]*)( \+ \d+)? in section', line)
            if m:
                return m.group(1)
            m2 = re.search(r'~"No symbol matches', line)
            if m2:
                return None
        return None

    def _read_regs(self, errors: Optional[List[str]] = None) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in KEY_REGS:
            try:
                out[r] = self._reg_value(r)
            except Exception as e:
                if errors is not None:
                    errors.append(f"register {r}: {e}")
        return out

    def _modules(self, errors: Optional[List[str]] = None) -> List[Module]:
        mods: List[Module] = []
        try:
            out = self._mi("-file-list-shared-libraries", 8)
            for line in out:
                for m in re.finditer(
                        r'\{id="([^"]*)"[^}]*?ranges=\[\{from="(0x[0-9a-fA-F]+)",to="(0x[0-9a-fA-F]+)"',
                        line):
                    mods.append(Module(name=m.group(1), base=int(m.group(2), 16),
                                       size=int(m.group(3), 16) - int(m.group(2), 16)))
        except Exception as e:
            if errors is not None:
                errors.append(f"modules: {e}")
        return mods

    def _backtrace(self, max_frames: int = 12, errors: Optional[List[str]] = None) -> List[Frame]:
        frames: List[Frame] = []
        try:
            out = self._mi(f"-stack-list-frames 0 {max_frames - 1}", 8)
            for line in out:
                for m in re.finditer(r'level="(\d+)"[^}]*?addr="(0x[0-9a-fA-F]+)"[^}]*?func="([^"]*)"', line):
                    frames.append(Frame(index=int(m.group(1)), ip=int(m.group(2), 16),
                                        symbol=m.group(3), ret=0, stack=0))
        except Exception as e:
            if errors is not None:
                errors.append(f"backtrace: {e}")
        return frames
