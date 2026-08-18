"""Redirect the debuggee's std handles via inheritable Win32 handles.

DbgEng's CreateProcess doesn't expose STARTUPINFO, so the way to give a
headless debuggee a working stdin (and silence its stdout) is: temporarily
point the DEBUGGER's own std handles at inheritable file handles, create the
debuggee with DEBUG_ECREATE_PROCESS_INHERIT_HANDLES, then restore.

Returns a ``restore()`` callable that the caller must invoke after CreateProcess.
"""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes

from core.session import BackendError

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x80


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_ulong),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


def _open_inheritable(path: str, access: int, disposition: int):
    sa = _SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
    sa.bInheritHandle = 1
    h = ctypes.windll.kernel32.CreateFileW(
        path, access, 0, ctypes.byref(sa), disposition, FILE_ATTRIBUTE_NORMAL, None)
    if h in (-1, 0xFFFFFFFFFFFFFFFF, None, ctypes.c_void_p(-1).value):
        raise BackendError(f"CreateFileW({path}) failed")
    return wintypes.HANDLE(h)


def redirect_stdlib(stdin_data: bytes):
    """Point the current process's std handles at inheritable files.

    stdin_data -> a temp file; stdout/stderr -> NUL. Returns a restore() callable.
    """
    k32 = ctypes.windll.kernel32

    # stdin payload file (inheritable read handle)
    fd, tmp_path = tempfile.mkstemp(prefix="dbg_stdin_")
    try:
        os.write(fd, bytes(stdin_data))
    finally:
        os.close(fd)
    h_in = _open_inheritable(tmp_path, GENERIC_READ, OPEN_EXISTING)
    h_out = _open_inheritable("NUL", GENERIC_WRITE, CREATE_ALWAYS)
    h_err = _open_inheritable("NUL", GENERIC_WRITE, CREATE_ALWAYS)

    old_in = k32.GetStdHandle(STD_INPUT_HANDLE)
    old_out = k32.GetStdHandle(STD_OUTPUT_HANDLE)
    old_err = k32.GetStdHandle(STD_ERROR_HANDLE)
    k32.SetStdHandle(STD_INPUT_HANDLE, h_in)
    k32.SetStdHandle(STD_OUTPUT_HANDLE, h_out)
    k32.SetStdHandle(STD_ERROR_HANDLE, h_err)

    def restore():
        k32.SetStdHandle(STD_INPUT_HANDLE, old_in)
        k32.SetStdHandle(STD_OUTPUT_HANDLE, old_out)
        k32.SetStdHandle(STD_ERROR_HANDLE, old_err)
        for h in (h_in, h_out, h_err):
            try:
                k32.CloseHandle(h)
            except Exception:
                pass
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return restore
