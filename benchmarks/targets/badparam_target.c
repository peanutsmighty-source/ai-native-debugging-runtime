/* badparam_target.c — 错误参数追踪 benchmark.
 * Bug: use_buffer() is called with a huge length against an 8-byte buffer.
 * Ground truth: length parameter not validated -> out-of-bounds read. */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT int use_buffer(const char *buf, int len) {
    int sum = 0;
    for (int i = 0; i < len; i++) sum += buf[i];   /* reads len bytes */
    return sum;
}

EXPORT void trigger_badparam(void) {
    char small[8] = {0};
    volatile int s = use_buffer(small, 0x40000);   /* way past small[] -> AV */
    (void)s;
}

int main(void) {
    printf("badparam_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    trigger_badparam();
    return 0;
}
