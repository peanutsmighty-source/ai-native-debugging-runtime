/* branch_target.c — 错误分支定位 (wrong-branch) benchmark.
 * Bug: classify() uses `n > 10` instead of `n >= 10`, so n==10 falls into
 * error_path() and crashes. Ground truth: off-by-one in the branch condition. */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT int error_path(int n) {
    volatile int *p = (volatile int *)0x0;
    (void)n;
    return *p;                 /* NULL deref -> AV */
}

EXPORT int classify(int n) {
    /* BUG: should be `n >= 10` to route n==10 to the safe path */
    if (n > 10) return n * 2;
    return error_path(n);      /* n==10 lands here */
}

int main(void) {
    printf("branch_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    printf("classify(10)=%d\n", classify(10));
    return 0;
}
