"""Pytest fixtures for the AI Debugging Runtime test suite.

CRITICAL: DbgEng/pybag allows only ONE working adapter per process — creating
a second DbgEngAdapter() in the same process yields a broken client (symbol
resolution returns -1, wait/run return unknown). This was verified empirically.
The whole suite therefore shares a single session-scoped adapter and each test
is responsible for terminate()ing the previous debuggee before launch()ing the
next one (the same pattern the MCP server uses).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Backend selection: DSH_TEST_BACKEND=dbgeng (default) | gdb
BACKEND = os.environ.get("DSH_TEST_BACKEND", "dbgeng")
if BACKEND == "gdb":
    from backends.gdb.adapter import GdbAdapter as AdapterCls
else:
    from backends.dbgeng.adapter import DbgEngAdapter as AdapterCls

# Marker for tests that exercise DbgEng-specific capabilities.
dbgeng_only = pytest.mark.skipif(BACKEND == "gdb",
                                 reason="DbgEng-specific capability (ROP gadget scan / condition syntax)")

TARGETS = PROJECT_ROOT / "benchmarks" / "targets"
EXPLOITS = PROJECT_ROOT / "benchmarks" / "exploit_targets"
WORK = EXPLOITS / "work"


def _compile_all():
    """Ensure every benchmark target is compiled (exes are gitignored)."""
    gcc = (Path(os.environ.get("USERPROFILE", "C:/Users/WHO"))
           / "AppData/Local/Microsoft/WinGet/Packages"
           / "BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe"
           / "mingw64/bin/gcc.exe")
    if not gcc.exists():
        # fall back to PATH
        gcc = "gcc"
    for src_dir in (TARGETS, EXPLOITS):
        for c in src_dir.glob("*.c"):
            exe = src_dir / (c.stem + ".exe")
            if not exe.exists():
                r = subprocess.run([str(gcc), "-O0", "-g", "-o", str(exe), str(c)],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    raise RuntimeError(f"compile {c.name} failed: {r.stderr}")


@pytest.fixture(scope="session", autouse=True)
def _ensure_targets():
    _compile_all()


if BACKEND == "gdb":
    @pytest.fixture()
    def adapter():
        """gdb backend: one fresh gdb process per test (no singleton limit,
        and fully independent state kills cross-test pollution)."""
        a = AdapterCls()
        yield a
        try:
            a._release()
        except Exception:
            pass
else:
    @pytest.fixture(scope="session")
    def adapter():
        """The single DbgEngAdapter shared by the whole suite.

        pybag allows only ONE working adapter per process (verified: a second
        DbgEngAdapter breaks symbol resolution), so a session-scoped singleton
        is mandatory.
        """
        a = AdapterCls()
        yield a
        try:
            a.terminate()
        except Exception:
            pass
        try:
            a._release()
        except Exception:
            pass
        # shellcode/ROP tests may have spawned CalculatorApp — clean up.
        try:
            subprocess.run(["powershell", "-Command",
                            "Stop-Process -Name CalculatorApp -Force -ErrorAction SilentlyContinue"],
                           capture_output=True)
        except Exception:
            pass


@pytest.fixture()
def fresh(adapter):
    """Yield a helper that launches a target cleanly on the shared adapter.

    terminate() is asynchronous (hard-kill by PID); give the old debuggee a
    beat to die before CreateProcess, otherwise DbgEng state from the
    previous target leaks into the new one (observed as broken symbol
    resolution / empty memory reads in sequential tests).
    """
    def launch(name: str, args=None, exe_dir=TARGETS):
        try:
            adapter.terminate()
        except Exception:
            pass
        time.sleep(0.4)
        exe = exe_dir / (name + ".exe")
        assert exe.exists(), f"missing target {exe}"
        snap = adapter.launch(str(exe), args or [])
        assert snap.stop_reason.value == "initial_break", snap.stop_reason
        return snap
    return launch


def target_path(name: str, exe_dir=TARGETS) -> str:
    return str(exe_dir / (name + ".exe"))


def pe_export_rva(dll_path: str, name: str) -> int:
    """Resolve one export's RVA from a PE file on disk (System32 DLLs).

    Robust against the ctypes GetProcAddress 64->32-bit truncation issue in
    sandboxed shells; used to locate WinExec/VirtualProtect for exploit tests.
    """
    import struct
    data = Path(dll_path).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    assert struct.unpack_from("<H", data, pe + 24)[0] == 0x20B, "not PE32+"
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    exp_rva, exp_size = struct.unpack_from("<II", data, opt + 112)
    sec = opt + opt_size
    sections = []
    for i in range(nsec):
        vsize, va, raw_size, raw_off = struct.unpack_from("<IIII", data, sec + i * 40 + 8)
        sections.append((va, vsize, raw_size, raw_off))

    def r2o(rva):
        for va, vsize, raw_size, raw_off in sections:
            if va <= rva < va + max(vsize, raw_size):
                return raw_off + (rva - va)
        raise ValueError(f"rva 0x{rva:x} not in sections")

    eo = r2o(exp_rva)
    n_names = struct.unpack_from("<I", data, eo + 0x18)[0]
    fo = r2o(struct.unpack_from("<I", data, eo + 0x1c)[0])
    no = r2o(struct.unpack_from("<I", data, eo + 0x20)[0])
    oo = r2o(struct.unpack_from("<I", data, eo + 0x24)[0])
    for i in range(n_names):
        nrva = struct.unpack_from("<I", data, no + 4 * i)[0]
        noff = r2o(nrva)
        end = data.index(b"\x00", noff)
        if data[noff:end].decode() == name:
            ord_idx = struct.unpack_from("<H", data, oo + 2 * i)[0]
            return struct.unpack_from("<I", data, fo + 4 * ord_idx)[0]
    raise KeyError(name)


def system32_export(dll: str, name: str) -> int:
    """RVA of an export in a System32 DLL (handles forwarders)."""
    import struct
    dll_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / dll
    rva = pe_export_rva(str(dll_path), name)
    data = dll_path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    exp_rva, exp_size = struct.unpack_from("<II", data, pe + 24 + 112)
    if exp_rva <= rva < exp_rva + exp_size:
        # forwarder string "KERNELBASE.Name" -> resolve recursively
        nsec = struct.unpack_from("<H", data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        sec = opt + opt_size
        sections = []
        for i in range(nsec):
            vsize, va, raw_size, raw_off = struct.unpack_from("<IIII", data, sec + i * 40 + 8)
            sections.append((va, vsize, raw_size, raw_off))
        for va, vsize, raw_size, raw_off in sections:
            if va <= rva < va + max(vsize, raw_size):
                off = raw_off + (rva - va)
                break
        end = data.index(b"\x00", off)
        target = data[off:end].decode()
        tdll, _, tname = target.partition(".")
        return system32_export(tdll + ".dll", tname)
    return rva
