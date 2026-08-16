/* heapcorrupt_target.c — 堆溢出/heap corruption benchmark.
 * Bug: memset overflows a 16-byte heap buffer, clobbering the adjacent chunk
 * header; free() then detects corruption. Ground truth: heap buffer overflow. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT void trigger_heap_corrupt(void) {
    char *a = (char *)malloc(16);
    char *b = (char *)malloc(16);
    (void)b;
    memset(a, 'A', 64);          /* overflow 48 bytes past a -> corrupt b's header */
    free(a);
    free(b);                     /* heap detects corruption -> crash */
}

int main(void) {
    printf("heapcorrupt_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    trigger_heap_corrupt();
    return 0;
}
