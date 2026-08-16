/*
 * crash_target.c — M1 spike / benchmark target
 *
 * A tiny native x64 Windows program used to verify that the AI Debugger
 * Runtime can, fully headless: launch a process, set a breakpoint, wait for
 * the breakpoint event, read registers/memory, disassemble, then catch an
 * access-violation exception and read the fault state.
 *
 * Key functions are exported (__declspec(dllexport)) so DbgEng can resolve
 * them by name from the PE export table even though this binary is built
 * with mingw (DWARF debug info, no .pdb).
 *
 * Build (mingw-w64):
 *   gcc -O0 -o crash_target.exe crash_target.c
 *
 * Usage:
 *   crash_target.exe            -> runs, then NULL-derefs (access violation)
 *   crash_target.exe --no-crash -> runs and exits cleanly
 */

#include <stdio.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

EXPORT int add_numbers(int a, int b) {
    return a + b;
}

/* Dereference NULL: raises a first-chance access violation (0xc0000005). */
EXPORT void crash_here(void) {
    volatile int *p = (volatile int *)0x0;
    *p = 0xdeadbeef;          /* write to NULL -> AV */
}

int main(int argc, char **argv) {
    int mode = 0;              /* 0 = crash (default), 1 = no-crash, 2 = spin */
    if (argc > 1 && strcmp(argv[1], "--no-crash") == 0) {
        mode = 1;
    } else if (argc > 1 && strcmp(argv[1], "--spin") == 0) {
        mode = 2;
    }

    printf("crash_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);

    int x = add_numbers(20, 22);
    printf("add_numbers(20,22)=%d\n", x);
    fflush(stdout);

    if (mode == 2) {
        printf("crash_target: spinning forever (for attach tests)...\n");
        fflush(stdout);
        for (;;) Sleep(1000);
    }

    Sleep(30);                 /* small deterministic window for attach tests */

    if (mode == 0) {
        printf("crash_target: about to dereference NULL...\n");
        fflush(stdout);
        crash_here();
        printf("crash_target: survived (unexpected)\n");
    } else {
        printf("crash_target: --no-crash, exiting cleanly\n");
    }
    fflush(stdout);
    return 0;
}
