/* condbp_target.c — 条件断点验证 benchmark.
 * Bug: process_item() crashes on the 500th item inside a 10000-iteration loop.
 * Ground truth: i==500 triggers a NULL deref; a conditional breakpoint on the
 * loop index is the efficient way to reach it without 499 manual steps. */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT int process_item(int i) {
    if (i == 500) {                    /* crash exactly here */
        volatile int *p = (volatile int *)0x0;
        return *p;
    }
    return i * 2;
}

int main(void) {
    printf("condbp_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    for (int i = 0; i < 10000; i++) {
        process_item(i);
    }
    return 0;
}
