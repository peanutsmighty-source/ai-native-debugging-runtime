/* unknown_target.c — 未知 crash 初步 root-cause 定位 benchmark.
 * Bug: table_lookup() multiplies an unvalidated index by 2 and reads OOB; the
 * crash is NOT an obvious NULL deref — the agent must notice the huge index.
 * Ground truth: missing bounds check + index*2 overflow -> OOB read. */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT int table_lookup(int *table, int n, int index) {
    (void)n;
    return table[index * 2];          /* index unvalidated, *2 -> OOB */
}

EXPORT void trigger_unknown(void) {
    int table[16] = {0};
    volatile int v = table_lookup(table, 16, 0x40000000);   /* huge index */
    (void)v;
}

int main(void) {
    printf("unknown_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    trigger_unknown();
    return 0;
}
