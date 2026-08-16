#!/usr/bin/env python3
"""
M1 backend spike — headless Windows user-mode debugging via DbgEng (pybag).

Chain verified in one run (no GUI, fully scripted):
    launch -> initial break -> breakpoint(crash_here) -> registers/mem/disasm
    -> access-violation exception -> fault state -> terminate -> JSON

Usage:
    python spike_demo.py [target.exe]

Design notes (M1 findings baked in):
  * pybag reads WINDBG_DIR to locate dbgeng.dll; PATH is prepended so its
    dependent DLLs resolve from the same vendored folder.
  * DbgEng output is redirected to os.devnull so the CLI's stdout is clean JSON.
  * Event handlers must NEVER raise: an exception inside a COM callback is
    swallowed as E_FAIL and the engine prints "Callback failed with 80004005".
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBGENG_DIR = PROJECT_ROOT / "vendor" / "dbgeng"

if not (DBGENG_DIR / "dbgeng.dll").exists():
    print(json.dumps({"ok": False, "error": f"dbgeng.dll not found at {DBGENG_DIR}"}))
    sys.exit(2)

os.environ["WINDBG_DIR"] = str(DBGENG_DIR)
os.environ["PATH"] = str(DBGENG_DIR) + os.pathsep + os.environ.get("PATH", "")

from pybag.userdbg import UserDbg                 # noqa: E402
from pybag.dbgeng import core as DbgEng           # noqa: E402
from pybag.dbgeng.idebugbreakpoint import DebugBreakpoint  # noqa: E402

# ---------------------------------------------------------------------------
# Event capture (the "blocking wait_event" foundation)
# ---------------------------------------------------------------------------
LAST_EVENT = {}


def on_breakpoint(*args):
    LAST_EVENT.clear()
    LAST_EVENT["type"] = "breakpoint"
    try:
        # args[0] is a POINTER(IDebugBreakpoint)
        bp = DebugBreakpoint(args[0])
        LAST_EVENT["bp_id"] = int(bp.GetId())
        LAST_EVENT["bp_offset"] = hex(int(bp.GetOffset()))
    except Exception as e:
        LAST_EVENT["handler_error"] = repr(e)
    return DbgEng.DEBUG_STATUS_BREAK


def on_exception(record, first_chance):
    LAST_EVENT.clear()
    LAST_EVENT["type"] = "exception"
    try:
        rec = getattr(record, "contents", record)   # unwrap POINTER if needed
        LAST_EVENT["code"] = hex(int(rec.ExceptionCode))
        LAST_EVENT["address"] = hex(int(rec.ExceptionAddress))
        LAST_EVENT["first_chance"] = bool(first_chance)
        try:
            LAST_EVENT["param0"] = int(rec.ExceptionInformation[0])
            LAST_EVENT["param1"] = int(rec.ExceptionInformation[1])
        except Exception:
            pass
    except Exception as e:
        LAST_EVENT["handler_error"] = repr(e)
    return DbgEng.DEBUG_STATUS_BREAK


# ---------------------------------------------------------------------------
# Structured state readers
# ---------------------------------------------------------------------------
KEY_REGS = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "rip",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags",
            "cs", "ss", "ds", "es", "fs", "gs"]


def read_regs(dbg):
    out = {}
    for r in KEY_REGS:
        try:
            out[r] = hex(dbg.reg[r])
        except Exception:
            pass
    return out


def disasm(dbg, addr, count=8):
    out = []
    try:
        from pybag.dbgeng import util as pu
        for _ in range(count):
            ins = pu.disassemble_instruction(dbg.bitness(), addr, dbg.read(addr, 15))
            if ins is None:
                break
            out.append({
                "address": hex(ins.address),
                "bytes": ins.bytes.hex(),
                "mnemonic": ins.mnemonic,
                "operands": ins.op_str,
            })
            addr += ins.size
    except Exception as e:
        out = [{"text": dbg.cmd("u %x L%d" % (addr, count)), "error": str(e)}]
    return out


def backtrace(dbg, max_frames=12):
    frames = []
    try:
        for f in dbg.backtrace_list():
            if len(frames) >= max_frames:
                break
            frames.append({
                "frame": f.FrameNumber,
                "ip": hex(f.InstructionOffset),
                "symbol": dbg.get_name_by_offset(f.InstructionOffset),
                "ret": hex(f.ReturnOffset),
                "stack": hex(f.StackOffset),
            })
    except Exception as e:
        frames = [{"error": repr(e)}]
    return frames


def snapshot(dbg, label, extra=None):
    pc = dbg.reg.get_pc()
    sp = dbg.reg.get_sp()
    snap = {
        "label": label,
        "status": dbg.exec_status(),
        "pid": int(dbg.pid),
        "pc": hex(pc),
        "sp": hex(sp),
        "symbol_at_pc": dbg.get_name_by_offset(pc),
        "registers": read_regs(dbg),
        "disasm": disasm(dbg, pc, 8),
        "backtrace": backtrace(dbg),
        "module_count": len(dbg.module_list()),
    }
    if extra:
        snap.update(extra)
    return snap


def resolve_symbol(dbg, name, module="crash_target"):
    for candidate in (f"{module}!{name}", name):
        try:
            off = dbg.symbol(candidate)
            if off and off != -1:
                return off
        except Exception:
            continue
    return None


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else str(
        PROJECT_ROOT / "benchmarks" / "targets" / "crash_target.exe")
    target = os.path.abspath(target)

    result = {
        "ok": False,
        "backend": "DbgEng (pybag)",
        "dbgeng_dir": str(DBGENG_DIR),
        "target": target,
        "steps": [],
    }

    dbg = UserDbg()
    # Silence DbgEng's banner / ModLoad / NatVis chatter so stdout stays JSON.
    try:
        dbg.callbacks.stdout = open(os.devnull, "w")
    except Exception:
        pass
    dbg.events.breakpoint(on_breakpoint)
    dbg.events.exception(on_exception)

    try:
        # 1. launch + initial breakpoint
        dbg.create(target, initial_break=True)
        result["steps"].append(snapshot(dbg, "initial_break"))

        # 2. resolve + set breakpoint on crash_here
        addr = resolve_symbol(dbg, "crash_here")
        if addr is None:
            result["error"] = "could not resolve crash_here"
            return result
        bpid = dbg.bp(addr)
        result["steps"].append({
            "label": "breakpoint_set",
            "symbol": "crash_here",
            "address": hex(addr),
            "id": bpid,
        })

        # 3. continue -> breakpoint hit (blocking wait)
        LAST_EVENT.clear()
        waited = dbg.go(10)
        result["steps"].append(snapshot(dbg, "breakpoint_hit", {
            "waited": waited,
            "last_event": dict(LAST_EVENT),
        }))

        # 4. continue -> access violation (blocking wait)
        LAST_EVENT.clear()
        waited = dbg.go(10)
        result["steps"].append(snapshot(dbg, "exception_hit", {
            "waited": waited,
            "last_event": dict(LAST_EVENT),
        }))

        # 5. deterministic memory reads (pc + stack)
        pc = dbg.reg.get_pc()
        sp = dbg.reg.get_sp()
        result["steps"].append({
            "label": "memory_read",
            "pc_bytes": dbg.read(pc, 16).hex(),
            "sp_bytes": dbg.read(sp, 32).hex(),
        })

        result["ok"] = True
    except Exception as e:
        result["error"] = repr(e)
    finally:
        try:
            dbg.terminate()
        except Exception:
            pass

    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, default=str))
