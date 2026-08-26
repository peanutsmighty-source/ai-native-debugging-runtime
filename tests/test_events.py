"""Event primitive: FIFO queue preserves intermediate events (thread create /
module load / exit), not just the stop event."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from conftest import target_path  # noqa: E402


def test_launch_queues_initial_break_and_modules(fresh, adapter):
    fresh("crash_target")
    r = adapter.wait_event(0.5)
    assert r["waited"] is False          # events already queued
    types = [e["type"] for e in r["events"]]
    assert "module_load" in types
    # initial break is tagged on its event (exception for DbgEng 0x80000003,
    # breakpoint-hit for gdb tbreak main) — backend-specific but must exist
    assert any(e.get("initial_break") for e in r["events"]), \
        "initial break must be tagged on its event"


def test_wait_event_blocks_when_empty(fresh, adapter):
    import time
    fresh("crash_target")
    adapter.wait_event(0.5)              # drain launch events
    t0 = time.time()
    r = adapter.wait_event(1.0)          # nothing queued -> block till timeout
    assert time.time() - t0 >= 0.8
    assert r["events"] == []
    assert r["waited"] is False


def test_run_after_event_order(fresh, adapter):
    """After a run that hits a breakpoint, the queued events end with the
    breakpoint stop and preserve earlier events."""
    fresh("crash_target")
    adapter.wait_event(0.2)              # drain launch
    adapter.breakpoint_add("crash_target!crash_here")
    snap = adapter.run(15)
    assert snap.stop_reason.value == "breakpoint"
    evs = adapter.wait_event(0.2)["events"]
    assert evs, "run() must queue its stop event"
    assert evs[-1]["type"] == "breakpoint", evs[-1]
    assert all(e["seq"] < evs[-1]["seq"] for e in evs[:-1]), "seqs must increase"


def test_threads_target_queues_thread_events(fresh, adapter):
    """threads_target spawns 8 workers; the crash run must queue thread_create
    events (the reason wait_event exists as a queue)."""
    fresh("threads_target")
    adapter.wait_event(0.2)
    snap = adapter.run(20)
    assert snap.stop_reason.value == "exception"
    assert snap.exception.code == 0xC0000005
    evs = adapter.wait_event(0.2)["events"]
    types = [e["type"] for e in evs]
    creates = types.count("thread_create")
    assert creates >= 8, f"expected 8 thread_create, got {creates}: {types}"
    assert types[-1] == "exception"
    # every event carries a strictly increasing sequence number
    seqs = [e["seq"] for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_process_exit_event(fresh, adapter):
    """A cleanly-exiting target queues a process_exit event."""
    fresh("crash_target", args=["--no-crash"])   # exits cleanly
    adapter.wait_event(0.2)
    adapter.run(15)
    evs = adapter.wait_event(0.2)["events"]
    types = [e["type"] for e in evs]
    assert "process_exit" in types, types
