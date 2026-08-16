/* delayedcrash_target.c — 启动后延迟崩溃 benchmark.
 * Bug: process runs fine for a few seconds, then NULL-derefs. The crash is NOT
 * at startup, so an agent must attach/keep running until it fires.
 * Ground truth: NULL deref after a Sleep delay. */
#include <stdio.h>
#include <windows.h>

int main(void) {
    printf("delayedcrash_target pid=%lu: running...\n",
           (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(4000);                       /* 4s delay */
    volatile int *p = (volatile int *)0x0;
    *p = 1;                            /* crash after delay */
    return 0;
}
