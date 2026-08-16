#!/usr/bin/env python3
"""
M1 extra checks: (1) attach, (2) whether a handler's GO return value is honored.

Usage:
    python m1_extra_checks.py attach
    python m1_extra_checks.py go
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DBGENG_DIR = PROJECT_ROOT / "vendor" / "dbgeng"

os.environ["WINDBG_DIR"] = str(DBGENG_DIR)
os.environ["PATH"] = str(DBGENG_DIR) + os.pathsep + os.environ.get("PATH", "")

from pybag.userdbg import UserDbg           # noqa: E402
from pybag.dbgeng import core as DbgEng     # noqa: E402

TARGET = str(PROJECT_ROOT / "benchmarks" / "targets" / "crash_target.exe")


def silent(dbg):
    try:
        dbg.callbacks.stdout = open(os.devnull, "w")
    except Exception:
        pass


def check_attach():
    p = subprocess.Popen([TARGET, "--spin"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    pid = p.pid

    dbg = UserDbg()
    silent(dbg)
    try:
        dbg.attach(pid, initial_break=True)
        pc = dbg.reg.get_pc()
        res = {
            "ok": True,
            "pid": pid,
            "status": dbg.exec_status(),
            "pc": hex(pc),
            "symbol_at_pc": dbg.get_name_by_offset(pc),
            "module_count": len(dbg.module_list()),
            "modules": [m[0][0] for m in dbg.module_list()][:10],
        }
        try:
            dbg.detach()
        except Exception:
            pass
        return res
    except Exception as e:
        return {"ok": False, "error": repr(e)}
    finally:
        try:
            p.kill()
        except Exception:
            pass


def check_go_honored():
    events = []

    def on_bp(*args):
        events.append("bp")
        return DbgEng.DEBUG_STATUS_GO

    def on_exc(record, fc):
        rec = getattr(record, "contents", record)
        events.append("exc:0x%x" % int(rec.ExceptionCode))
        return DbgEng.DEBUG_STATUS_BREAK

    dbg = UserDbg()
    silent(dbg)
    dbg.events.breakpoint(on_bp)
    dbg.events.exception(on_exc)
    try:
        dbg.create(TARGET, initial_break=True)
        addr = dbg.symbol("crash_target!crash_here")
        dbg.bp(addr)
        dbg.go(10)
        pc = dbg.reg.get_pc()
        res = {
            "ok": True,
            "events": events,
            "pc": hex(pc),
            "symbol_at_pc": dbg.get_name_by_offset(pc),
            "status": dbg.exec_status(),
            # if GO is honored, we should have run PAST the breakpoint and
            # stopped at the access violation (crash_here+0x14); if not, we
            # stop AT the breakpoint (crash_here+0x0).
            "go_honored": ("exc" in " ".join(events)) and ("+0x14" in dbg.get_name_by_offset(pc)),
        }
        return res
    except Exception as e:
        return {"ok": False, "error": repr(e), "events": events}
    finally:
        try:
            dbg.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "attach"
    if mode == "go":
        print(json.dumps(check_go_honored(), indent=2))
    else:
        print(json.dumps(check_attach(), indent=2))
