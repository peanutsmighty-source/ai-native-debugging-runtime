/* threads_target.c — 多线程状态观察 benchmark.
 * Bug: 8 worker threads; thread id 3 crashes. An agent must observe per-thread
 * state to see WHICH thread faulted and why.
 * Ground truth: worker thread #3 NULL-derefs. */
#include <stdio.h>
#include <stdint.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT DWORD WINAPI worker(LPVOID arg) {
    int id = (int)(intptr_t)arg;
    Sleep(id * 100);
    if (id == 3) {
        volatile int *p = (volatile int *)0x0;
        *p = 1;                          /* only thread 3 crashes */
    }
    return 0;
}

int main(void) {
    printf("threads_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    HANDLE th[8];
    for (int i = 0; i < 8; i++) {
        th[i] = CreateThread(NULL, 0, worker, (LPVOID)(intptr_t)i, 0, NULL);
    }
    WaitForMultipleObjects(8, th, TRUE, INFINITE);
    return 0;
}
