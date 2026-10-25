/* Подключается в каждый файл (-include): хостовые замены для ARM-примитивов furi. */
#pragma once
#define FURI_IS_IRQ_MASKED() (0)
#define FURI_IS_IRQ_MODE()   (0)
#define FURI_IS_ISR()        (0)
/* newlib-макрос, который furi/core/string.h использует для printf-атрибутов */
#ifndef _ATTRIBUTE
#define _ATTRIBUTE(attrs) __attribute__(attrs)
#endif
/* storage_external_api.c логирует указатели как смещение от начала SRAM */
#define SRAM_BASE 0
/* newlib объявляет их в stdlib.h, в glibc их нет (реализация — fsim_libc.c) */
char* itoa(int value, char* str, int base);
char* utoa(unsigned value, char* str, int base);
char* ultoa(unsigned long value, char* str, int base);
/* Сервисам прошивки нельзя возвращаться (svc_startup: «Service threads MUST NOT return»).
   Подмены fsim после регистрации записи засыпают навсегда. */
#define FSIM_SERVICE_PARK()                       \
    do {                                          \
        for(;;) {                                 \
            furi_delay_ms(60 * 60 * 1000);        \
        }                                         \
    } while(0)
/* битовые макросы CMSIS (stm32), которые сервисы используют как общие */
#ifndef READ_BIT
#define READ_BIT(REG, BIT)  ((REG) & (BIT))
#define SET_BIT(REG, BIT)   ((REG) |= (BIT))
#define CLEAR_BIT(REG, BIT) ((REG) &= ~(BIT))
#endif
