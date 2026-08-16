/*
 * uaf_target.c — realistic use-after-free crash sample (benchmark task).
 *
 * Bug pattern: a heap object is freed, its slot is immediately reused and
 * overwritten with attacker-controlled data ('A' fill), then the dangling
 * pointer is dereferenced to CALL through a corrupted function pointer.
 *
 * Deterministic crash: access violation, faulting address 0x4141414141414141
 * (the 'A' fill leaking into the call target) inside uaf_target!trigger_uaf.
 *
 * Build: gcc -O0 -o uaf_target.exe uaf_target.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

typedef struct {
    int   id;
    char  name[32];
    int (*handler)(void *);
} Widget;

EXPORT void trigger_uaf(void) {
    Widget *w = (Widget *)malloc(sizeof(Widget));
    w->id = 0x41414141;
    strcpy(w->name, "sensor");
    w->handler = (int (*)(void *))(void *)0x4141414141414141ULL;

    free(w);                       /* <-- object freed here */

    /* Reuse the freed slot: attacker-controlled 'A' fill overwrites the chunk. */
    char *reuse = (char *)malloc(sizeof(Widget));
    memset(reuse, 0x41, sizeof(Widget));

    /* Use-after-free: call through the dangling pointer -> jump to 0x4141... */
    w->handler(w);                 /* <-- crashes here (AV @ 0x4141414141414141) */
}

int main(void) {
    printf("uaf_target pid=%lu\n", (unsigned long)GetCurrentProcessId());
    fflush(stdout);
    Sleep(30);
    trigger_uaf();
    return 0;
}
