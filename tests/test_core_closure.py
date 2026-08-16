#!/usr/bin/env python3
"""M2 closure test — exercises the Core DebugSession interface end-to-end.

Runs the full PRD loop through the Core abstraction (not the raw spike):
    launch -> breakpoint_add -> run(to breakpoint) -> read_memory/disassemble
    -> run(to access violation) -> terminate

Exits non-zero on any failed assertion.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backends.dbgeng.adapter import DbgEngAdapter  # noqa: E402

TARGET = str(PROJECT_ROOT / "benchmarks" / "targets" / "crash_target.exe")


def main() -> int:
    steps = []
    s = DbgEngAdapter()

    # 1. launch -> initial breakpoint
    snap = s.launch(TARGET)
    steps.append({"op": "launch", "snapshot": snap.to_dict()})
    assert snap.stop_reason.value == "initial_break", snap.stop_reason
    assert snap.pid > 0

    # 2. breakpoint add (symbol resolution via exports)
    bp = s.breakpoint_add("crash_target!crash_here")
    steps.append({"op": "breakpoint_add", "bp": bp.to_dict()})
    assert bp.address is not None, "symbol did not resolve"

    # 3. run -> stop at breakpoint (blocking)
    snap = s.run(10)
    steps.append({"op": "run_to_breakpoint", "snapshot": snap.to_dict()})
    assert snap.stop_reason.value == "breakpoint", snap.stop_reason
    assert "crash_here" in snap.symbol_at_pc

    # 4. inspection: memory + disassembly
    mem = s.read_memory(snap.pc, 16)
    dis = s.disassemble(snap.pc, 3)
    steps.append({"op": "read_memory", "pc": hex(snap.pc), "bytes": mem.hex()})
    steps.append({"op": "disassemble", "insns": [i.to_dict() for i in dis]})
    assert len(mem) == 16
    assert len(dis) >= 1

    # 5. run -> access violation (blocking, structured fault info)
    snap = s.run(10)
    steps.append({"op": "run_to_exception", "snapshot": snap.to_dict()})
    assert snap.stop_reason.value == "exception", snap.stop_reason
    assert snap.exception is not None
    assert snap.exception.code == 0xC0000005, hex(snap.exception.code)

    # 6. terminate
    s.terminate()
    steps.append({"op": "terminate", "ok": True})

    print(json.dumps({"ok": True, "steps": steps}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(json.dumps({"ok": False, "error": f"ASSERT: {e}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"ok": False, "error": repr(e)}))
        sys.exit(1)
