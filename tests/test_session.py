"""Session lifecycle + core primitives: launch / run / step / terminate /
restart / threads / modules / breakpoints."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from conftest import target_path  # noqa: E402


def test_launch_initial_break(fresh):
    fresh("crash_target")


def test_run_to_exception_structured(fresh, adapter):
    """run() past a NULL deref yields a structured exception, not raw text."""
    fresh("crash_target")
    snap = adapter.run(15)
    assert snap.stop_reason.value == "exception", snap.stop_reason
    assert snap.exception is not None
    assert snap.exception.code == 0xC0000005, hex(snap.exception.code)
    assert "crash_here" in (snap.symbol_at_pc or ""), snap.symbol_at_pc
    # AV params: (write=1, address=0x0) for crash_target
    assert len(snap.exception.params) == 2
    assert snap.exception.params[1] == 0, snap.exception.params


def test_run_lean_after_state(fresh, adapter):
    """run() returns a lean after-state: regs + disasm, no modules."""
    fresh("crash_target")
    snap = adapter.run(15)
    assert snap.registers.get("rip") is not None
    assert len(snap.disassembly) >= 1
    assert snap.modules == []          # lean: modules only via observe()


def test_observe_full_context(fresh, adapter):
    fresh("crash_target")
    obs = adapter.observe()
    assert obs.stop_reason.value == "initial_break"
    assert len(obs.modules) >= 1       # full context includes modules
    assert len(obs.backtrace) >= 1
    assert len(obs.registers) >= 10


def test_breakpoint_add_symbol_and_condition(fresh, adapter):
    fresh("crash_target")
    bp = adapter.breakpoint_add("crash_target!crash_here")
    assert bp.address is not None, "symbol should resolve via exports"
    snap = adapter.run(15)
    assert snap.stop_reason.value == "breakpoint", snap.stop_reason
    assert "crash_here" in (snap.symbol_at_pc or "")
    adapter.breakpoint_remove(bp.id)
    assert adapter.breakpoint_list() == []


def test_terminate_and_restart(fresh, adapter):
    fresh("crash_target")
    adapter.restart()
    obs = adapter.observe()
    assert obs.stop_reason.value == "initial_break"


def test_restart_gets_new_pid(fresh, adapter):
    fresh("crash_target")
    p1 = adapter.observe().pid
    adapter.restart()
    p2 = adapter.observe().pid
    assert p2 != p1, "restart should create a new process"


def test_thread_list_current(fresh, adapter):
    fresh("crash_target")
    threads = adapter.thread_list()
    assert len(threads) >= 1
    cur = adapter.get_thread()
    assert any(t["index"] == cur for t in threads)
    adapter.set_thread(cur)            # no-op switch back
    assert adapter.get_thread() == cur


def test_module_base_resolves(fresh, adapter):
    fresh("crash_target")
    m = adapter.module_base("crash_target")
    assert m.base > 0
    m2 = adapter.module_base("ntdll")
    assert m2.base > 0 and m2.base != m.base


def test_memory_read_write_roundtrip(fresh, adapter):
    fresh("crash_target")
    # read at the initial-break pc (executable code page)
    pc = adapter.observe().pc
    data = adapter.read_memory(pc, 16)
    assert len(data) == 16
    # write into an unused scratch area of the stack (writable, not executed)
    rsp = adapter.observe().registers["rsp"]
    orig = adapter.read_memory(rsp - 0x100, 8)
    adapter.write_memory(rsp - 0x100, b"\x11" * 8)
    assert adapter.read_memory(rsp - 0x100, 8) == b"\x11" * 8
    adapter.write_memory(rsp - 0x100, orig)
    assert adapter.read_memory(rsp - 0x100, 8) == orig


def test_register_get_set(fresh, adapter):
    fresh("crash_target")
    rax = adapter.get_register("rax")
    adapter.set_register("rax", 0x1234)
    assert adapter.get_register("rax") == 0x1234
    adapter.set_register("rax", rax)


def test_disassemble_and_search(fresh, adapter):
    fresh("crash_target")
    pc = adapter.observe().pc
    insns = adapter.disassemble(pc, 4)
    assert len(insns) >= 1
    assert insns[0].address == pc
    # search for the bytes of the first instruction in its own page
    first_bytes = bytes.fromhex(insns[0].bytes)
    hits = adapter.search_memory(pc, 0x1000, first_bytes)
    assert pc in hits


def test_step_into(fresh, adapter):
    fresh("crash_target")
    pc0 = adapter.observe().pc
    adapter.step("into")
    pc1 = adapter.observe().pc
    assert pc1 != pc0


def test_launch_with_stdin(fresh, adapter):
    """scanf_target reads a string from stdin into g_buf."""
    t = target_path("scanf_target")
    adapter.terminate()
    adapter.launch(t, [], stdin_data=b"hello\n")
    # run to completion; program exits after printing
    snap = adapter.run(10)
    assert snap.stop_reason.value in ("unknown", "exception", "breakpoint"), snap.stop_reason
    adapter.terminate()
