/* stack_target.c — 异常调用栈定位 (stack exhaustion) benchmark.
 * Bug: unbounded recursion with a large stack frame.
 * Ground truth: missing base case -> stack overflow (0xC00000FD). */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT void recurse(int depth) {
    volatile char pad[4096];     /* big frame to exhaust the stack quickly */
    pad[0] = (char)depth;
    recurse(depth + 1);          /* no base case */
}

int main(void) {
    printf("stack_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    recurse(0);
    return 0;
}
