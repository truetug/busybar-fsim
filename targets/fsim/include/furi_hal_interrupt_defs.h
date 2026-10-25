/* fsim: прерываний нет, но типы нужны общим заголовкам HAL. */
#pragma once
typedef enum {
    FuriHalInterruptIdHostTick,
    FuriHalInterruptIdMax,
} FuriHalInterruptId;
