"""Experiment primitive: snapshot()/restore() round-trips for registers,
memory regions and breakpoints."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from conftest import target_path  # noqa: E402


def test_snapshot_restore_registers(fresh, adapter):
    fresh("crash_target")
    snap = adapter.snapshot()
    assert "registers" in snap and "thread" in snap
    rax = snap["registers"]["rax"]
    assert rax.startswith("0x")
    adapter.set_register("rax", 0x12345678)
    assert adapter.get_register("rax") == 0x12345678
    adapter.restore(snap)
    assert adapter.get_register("rax") == int(rax, 16)


def test_snapshot_restore_memory(fresh, adapter):
    fresh("crash_target")
    # use the initial-break pc (executable code page, guaranteed readable)
    pc = adapter.observe().pc
    snap = adapter.snapshot(regions=[(pc, 16)])
    assert len(snap["memory"]) == 1
    orig = bytes.fromhex(snap["memory"][0]["data"])
    adapter.write_memory(pc, b"\xAA" * 16)
    adapter.restore(snap)
    assert adapter.read_memory(pc, 16) == orig


def test_snapshot_restore_breakpoints(fresh, adapter):
    fresh("crash_target")
    bp = adapter.breakpoint_add("crash_target!crash_here")
    snap = adapter.snapshot()
    assert len(snap["breakpoints"]) >= 1
    adapter.breakpoint_remove(bp.id)
    assert adapter.breakpoint_list() == []
    adapter.restore(snap)
    addrs = {b.address for b in adapter.breakpoint_list()}
    assert bp.address in addrs, "restore must re-add missing breakpoints"


def test_snapshot_after_crash(fresh, adapter):
    """snapshot() must work while stopped at an exception (crash state)."""
    fresh("crash_target")
    snap_run = adapter.run(15)
    assert snap_run.stop_reason.value == "exception"
    snap = adapter.snapshot()
    assert "pc" in snap
    assert int(snap["pc"], 16) == snap_run.pc
    # restore after a register poke at the crash site
    adapter.set_register("rax", 0)
    adapter.restore(snap)
    assert adapter.get_register("rax") == snap_run.registers["rax"]


def test_snapshot_is_json_serializable(fresh, adapter):
    import json
    fresh("crash_target")
    snap = adapter.snapshot(regions=[(adapter.observe().pc, 16)])
    json.dumps(snap)                   # must not raise
