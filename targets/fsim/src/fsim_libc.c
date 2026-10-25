/* Расширения newlib, которых нет в glibc: furi печатает числа через itoa/ultoa/utoa. */
#include <stdbool.h>
#include <stddef.h>

static char* fsim_format(unsigned long value, bool negative, char* str, int base) {
    static const char digits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
    char buffer[66];
    size_t length = 0;
    if(base < 2 || base > 36) {
        str[0] = '\0';
        return str;
    }
    do {
        buffer[length++] = digits[value % (unsigned long)base];
        value /= (unsigned long)base;
    } while(value);
    size_t position = 0;
    if(negative) {
        str[position++] = '-';
    }
    while(length) {
        str[position++] = buffer[--length];
    }
    str[position] = '\0';
    return str;
}

char* itoa(int value, char* str, int base) {
    bool negative = base == 10 && value < 0;
    unsigned long magnitude = negative ? (unsigned long)(-(long)value) : (unsigned long)(unsigned int)value;
    return fsim_format(magnitude, negative, str, base);
}

char* utoa(unsigned value, char* str, int base) {
    return fsim_format(value, false, str, base);
}

char* ultoa(unsigned long value, char* str, int base) {
    return fsim_format(value, false, str, base);
}

/* Прошивка подменяет printf линковщиком (--wrap, core_libs/lib/print). На хосте — сразу в libc. */
#include <stdarg.h>
#include <stdio.h>

int __wrap_vsnprintf(char* str, size_t size, const char* format, va_list args) {
    return vsnprintf(str, size, format, args);
}

int __wrap_fflush(FILE* stream) {
    return fflush(stream);
}

/* strlcpy есть в newlib, в glibc появилась только в 2.38 (в bookworm — 2.36) */
#include <string.h>
size_t strlcpy(char* dst, const char* src, size_t size) {
    size_t length = strlen(src);
    if(size) {
        size_t copy = length >= size ? size - 1 : length;
        memcpy(dst, src, copy);
        dst[copy] = '\0';
    }
    return length;
}

#if !defined(__EMSCRIPTEN__)
/* POSIX-порт FreeRTOS блокирует в потоках задач все сигналы. Заблокированный синхронный
   SIGSEGV убивает процесс молча, мимо обработчика с backtrace — поэтому их не блокируем. */
#include <signal.h>

int __real_pthread_sigmask(int how, const sigset_t* set, sigset_t* old);

int __wrap_pthread_sigmask(int how, const sigset_t* set, sigset_t* old) {
    if(set && how != SIG_UNBLOCK) {
        sigset_t allowed = *set;
        sigdelset(&allowed, SIGSEGV);
        sigdelset(&allowed, SIGBUS);
        sigdelset(&allowed, SIGABRT);
        return __real_pthread_sigmask(how, &allowed, old);
    }
    return __real_pthread_sigmask(how, set, old);
}

#endif
