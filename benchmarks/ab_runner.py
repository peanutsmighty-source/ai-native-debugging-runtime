#!/usr/bin/env python3
"""
A/B runner — high-level abstraction (DbgEngAdapter) vs low-level raw tools
(RawDbgEng), same DbgEng backend, same "extract root-cause evidence" task.

For each sample it runs a canonical evidence-extraction procedure against BOTH
toolsets and counts:
  * tool calls      (agent round-trips — the PRD headline metric)
  * output bytes    (JSON size of every returned value — token proxy)
  * wall time
  * evidence completeness (did every key piece come back)

Procedure (identical task, different abstraction):
  launch -> run(to crash) -> [low-level: get_exception_info, get_breakpoint_info,
  get_pc, get_symbol_name, get_registers, disassemble, get_backtrace]
  -> terminate
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backends.dbgeng.adapter import DbgEngAdapter  # noqa: E402
from backends.dbgeng.raw import RawDbgEng           # noqa: E402

TARGETS = ROOT / "benchmarks" / "targets"

SAMPLES = [
    ("crash_target.exe", "NULL deref"),
    ("uaf_target.exe", "UAF"),
    ("branch_target.exe", "wrong branch"),
    ("dllload_target.exe", "DLL load fail"),
    ("badparam_target.exe", "bad param"),
    ("stack_target.exe", "stack overflow"),
    ("heapcorrupt_target.exe", "heap corruption"),
    ("delayedcrash_target.exe", "delayed crash"),
    ("unknown_target.exe", "unknown crash"),
]


def evidence_high_level(t: str):
    s = DbgEngAdapter()
    calls = 0
    out_bytes = 0

    def rec(obj):
        nonlocal out_bytes
        out_bytes += len(json.dumps(obj, default=str))

    t0 = time.time()
    rec(s.launch(t)); calls += 1
    snap = s.run(10); calls += 1
    rec(snap.to_dict())
    ev = {
        "stop_reason": snap.stop_reason.value,
        "exception": snap.exception.to_dict() if snap.exception else None,
        "breakpoint": snap.breakpoint.to_dict() if snap.breakpoint else None,
        "pc": hex(snap.pc),
        "symbol": snap.symbol_at_pc,
        "regs": {k: hex(v) for k, v in snap.registers.items()},
        "disasm": [i.to_dict() for i in snap.disassembly],
        "backtrace": [f.to_dict() for f in snap.backtrace],
    }
    rec(s.terminate()); calls += 1
    return calls, out_bytes, ev, time.time() - t0


def evidence_low_level(t: str):
    s = RawDbgEng()
    calls = 0
    out_bytes = 0

    def rec(obj):
        nonlocal out_bytes
        out_bytes += len(json.dumps(obj, default=str))

    t0 = time.time()
    rec(s.launch(t)); calls += 1
    rec(s.run(10)); calls += 1
    exc = s.get_exception_info(); rec(exc); calls += 1
    bp = s.get_breakpoint_info(); rec(bp); calls += 1
    pc = s.get_pc(); rec(pc); calls += 1
    sym = s.get_symbol_name(int(pc, 16)); rec(sym); calls += 1
    regs = s.get_registers(); rec(regs); calls += 1
    dis = s.disassemble(int(pc, 16), 3); rec(dis); calls += 1
    bt = s.get_backtrace(3); rec(bt); calls += 1
    rec(s.terminate()); calls += 1
    ev = {
        "exception": exc,
        "breakpoint": bp,
        "pc": pc,
        "symbol": sym,
        "regs": regs,
        "disasm": dis,
        "backtrace": bt,
    }
    return calls, out_bytes, ev, time.time() - t0


def complete(ev: dict) -> bool:
    stop = ev.get("exception") or ev.get("breakpoint")
    regs = ev.get("regs") or {}
    bt = ev.get("backtrace") or []
    return bool(stop) and len(regs) > 0 and len(bt) > 0


def signature(ev: dict) -> str:
    if ev.get("exception"):
        return "exc " + ev["exception"].get("code", "?")
    if ev.get("breakpoint"):
        return "bp " + str(ev["breakpoint"].get("address"))
    return "stop_reason=" + str(ev.get("stop_reason", "?"))


def main():
    rows = []
    agg = {"hi": {"calls": 0, "bytes": 0, "time": 0.0},
           "lo": {"calls": 0, "bytes": 0, "time": 0.0}}
    for name, label in SAMPLES:
        t = str(TARGETS / name)
        try:
            h = evidence_high_level(t)
        except Exception as e:
            print(f"[hi ERROR] {name}: {e!r}")
            h = (0, 0, {}, 0.0)
        try:
            l = evidence_low_level(t)
        except Exception as e:
            print(f"[lo ERROR] {name}: {e!r}")
            l = (0, 0, {}, 0.0)

        h_calls, h_bytes, h_ev, h_dt = h
        l_calls, l_bytes, l_ev, l_dt = l
        agg["hi"]["calls"] += h_calls
        agg["hi"]["bytes"] += h_bytes
        agg["hi"]["time"] += h_dt
        agg["lo"]["calls"] += l_calls
        agg["lo"]["bytes"] += l_bytes
        agg["lo"]["time"] += l_dt
        rows.append({
            "sample": label,
            "hi_calls": h_calls, "lo_calls": l_calls,
            "hi_bytes": h_bytes, "lo_bytes": l_bytes,
            "hi_sig": signature(h_ev), "lo_sig": signature(l_ev),
            "hi_complete": complete(h_ev), "lo_complete": complete(l_ev),
        })

    print("\n===== per-sample =====")
    print(f"{'sample':<16} {'hi_calls':>8} {'lo_calls':>8} {'hi_bytes':>9} {'lo_bytes':>9} {'sig':<16} {'complete':>10}")
    for r in rows:
        comp = f"{r['hi_complete']}/{r['lo_complete']}"
        print(f"{r['sample']:<16} {r['hi_calls']:>8} {r['lo_calls']:>8} "
              f"{r['hi_bytes']:>9} {r['lo_bytes']:>9} {r['hi_sig']:<16} {comp:>10}")

    n = len(rows)
    print("\n===== aggregate (n=%d) =====" % n)
    print(f"total calls:   high-level={agg['hi']['calls']}  low-level={agg['lo']['calls']}")
    print(f"total bytes:   high-level={agg['hi']['bytes']}  low-level={agg['lo']['bytes']}")
    print(f"total time:    high-level={agg['hi']['time']:.1f}s  low-level={agg['lo']['time']:.1f}s")
    if agg["lo"]["calls"]:
        print(f"call reduction: {(1 - agg['hi']['calls']/agg['lo']['calls'])*100:.0f}%")
    if agg["lo"]["bytes"]:
        print(f"bytes reduction: {(1 - agg['hi']['bytes']/agg['lo']['bytes'])*100:.0f}%")
    print(json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    main()
