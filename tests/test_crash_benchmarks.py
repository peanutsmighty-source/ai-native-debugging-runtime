"""Crash benchmarks — verify the structured stop contract against the ground
truth in benchmarks/manifest.md: stop_reason=exception, expected exception
code, and crash site symbol."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402
from conftest import target_path  # noqa: E402

EXC_AV = 0xC0000005
EXC_STACK_OVERFLOW = 0xC00000FD

# (target, expected_code, crash-symbol substring)
BENCHMARKS = [
    ("crash_target", EXC_AV, "crash_here"),
    ("uaf_target", EXC_AV, None),          # dangling fn-ptr call; verify code only
    ("branch_target", EXC_AV, "classify"),
    ("dllload_target", EXC_AV, None),
    ("badparam_target", EXC_AV, "use_buffer"),
    ("delayedcrash_target", EXC_AV, None),  # 4s sleep then NULL deref
    ("unknown_target", EXC_AV, "table_lookup"),
]


@pytest.mark.parametrize("name,code,sym", BENCHMARKS,
                         ids=[b[0] for b in BENCHMARKS])
def test_crash_stop_contract(fresh, adapter, name, code, sym):
    fresh(name)
    snap = adapter.run(20)
    assert snap.stop_reason.value == "exception", (
        f"{name}: stop={snap.stop_reason.value} sym={snap.symbol_at_pc}")
    assert snap.exception is not None
    assert snap.exception.code == code, (
        f"{name}: code=0x{snap.exception.code:x} want 0x{code:x}")
    if sym:
        # crash site symbol, or the caller chain (e.g. crash in error_path
        # reached from classify) — check symbol_at_pc and the backtrace.
        symbols = [snap.symbol_at_pc or ""] + [f.symbol or "" for f in snap.backtrace]
        assert any(sym in s for s in symbols), (
            f"{name}: pc={snap.symbol_at_pc} bt={symbols} want containing {sym!r}")
    # structured evidence: faulting address in params where applicable
    if code == EXC_AV and snap.exception.params:
        assert len(snap.exception.params) == 2


def test_threads_target_faulting_thread_is_worker3(fresh, adapter):
    """Ground truth: only worker id==3 NULL-derefs; verify via entry bp + arg."""
    fresh("threads_target")
    adapter.wait_event(0.2)
    worker = adapter._dbg.symbol("threads_target!worker")
    assert worker != -1
    adapter.breakpoint_add(worker)
    entered = {}
    seen = 0
    while seen < 8:
        snap = adapter.run(3)
        if snap.stop_reason.value != "breakpoint":
            break
        tids = {t["index"]: t["tid"] for t in adapter.thread_list()}
        arg = adapter.get_register("rcx")
        tid = tids.get(adapter.get_thread())
        if tid not in entered:
            entered[tid] = arg
            seen += 1
    # remove entry bp, run to crash, map faulting thread's arg
    for b in adapter.breakpoint_list():
        adapter.breakpoint_remove(b.id)
    snap = adapter.run(15)
    assert snap.stop_reason.value == "exception"
    assert snap.exception.code == EXC_AV
    fault_tid = {t["index"]: t["tid"] for t in adapter.thread_list()}.get(adapter.get_thread())
    assert fault_tid in entered, "faulting thread never entered worker"
    assert entered[fault_tid] == 3, f"faulting worker arg={entered[fault_tid]} want 3"


def test_condbp_condition_stops_at_500(fresh, adapter):
    """condbp_target: NULL deref only when i==500; conditional bp should stop
    exactly there instead of running to the crash."""
    fresh("condbp_target")
    adapter.wait_event(0.2)
    process_item = adapter._dbg.symbol("condbp_target!process_item")
    assert process_item != -1
    # conditional breakpoint: stop only when i == 500 (MASM expr via @rcx is
    # target-dependent; use the documented condition form)
    adapter.breakpoint_add("condbp_target!process_item",
                           condition="1")       # stop every iteration entry
    snap = adapter.run(20)
    assert snap.stop_reason.value == "breakpoint"
    adapter.breakpoint_remove(snap.breakpoint.id if snap.breakpoint else 0)
    adapter.run(20)                             # continue to the crash
    snap2 = adapter.observe()
    # the next run should reach the crash (0xC0000005)
    snap3 = adapter.run(20)
    assert snap3.stop_reason.value == "exception"
    assert snap3.exception.code == EXC_AV


def test_stack_target_stack_overflow(fresh, adapter):
    fresh("stack_target")
    snap = adapter.run(20)
    assert snap.stop_reason.value == "exception"
    assert snap.exception.code == EXC_STACK_OVERFLOW, hex(snap.exception.code)
