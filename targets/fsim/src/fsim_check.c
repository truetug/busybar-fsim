/* Замена core/check.c: падение и остановка без ARM-ассемблера. Макрос furi_crash кладёт
   указатель на сообщение в r12 — на x86_64 это тот же регистр, читаем его первым делом. */
#include <furi.h>
#include <stdio.h>
#include <stdlib.h>

static const char* fsim_take_message(void) {
    const char* message;
    __asm__ volatile("mov %%r12, %0" : "=r"(message));
    return message;
}

FURI_NORETURN void __furi_crash_implementation(void) {
    const char* message = fsim_take_message();
    fprintf(stderr, "\n[fsim] CRASH: %s\n", message ? message : "(no message)");
    fflush(stderr);
    abort();
}

FURI_NORETURN void __furi_halt_implementation(void) {
    const char* message = fsim_take_message();
    fprintf(stderr, "\n[fsim] HALT: %s\n", message ? message : "(no message)");
    fflush(stderr);
    exit(2);
}
