"""Assembler utilities for x86-64 shellcode (Keystone-backed).

Kept outside benchmarks/: assembling is a runtime capability used by the MCP
layer, not a benchmark helper.
"""

from __future__ import annotations


def asm_x64(code: str) -> bytes:
    """Assemble x86-64 (Intel syntax) to bytes via Keystone.

    Supports labels (``msg:``), rip-relative refs (``lea rcx, [rip+msg]``) and
    data directives (``.string "calc"`` / ``.byte 0x90``). Raises ValueError
    with Keystone's message on a bad instruction.
    """
    from keystone import Ks, KS_ARCH_X86, KS_MODE_64
    ks = Ks(KS_ARCH_X86, KS_MODE_64)
    try:
        enc, _count = ks.asm(code)
    except Exception as e:
        raise ValueError(f"asm failed: {e}") from e
    return bytes(enc)
