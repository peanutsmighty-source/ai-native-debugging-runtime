/* dllload_target.c — DLL 加载失败 benchmark.
 * Bug: LoadLibrary result not checked; GetProcAddress(NULL,...) returns NULL
 * and the code calls it. Ground truth: missing NULL check on module handle. */
#include <stdio.h>
#include <windows.h>
#define EXPORT __declspec(dllexport)

EXPORT void load_and_call(void) {
    HMODULE h = LoadLibraryA("definitely_missing_xyz.dll");   /* -> NULL */
    FARPROC fn = GetProcAddress(h, "DoThing");                /* -> NULL */
    ((void (*)(void))fn)();                                    /* call NULL -> AV */
}

int main(void) {
    printf("dllload_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(20);
    load_and_call();
    return 0;
}
