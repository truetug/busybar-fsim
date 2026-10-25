/* fsim: ресурсы платы. Пинов нет; InputKey — как в targets/f21/furi_hal/furi_hal_resources.h
   (положения ручки режимов — тоже клавиши). */
#pragma once
#include <furi.h>
#include <furi_hal_gpio.h>

typedef enum {
    InputKeyUp,
    InputKeyDown,
    InputKeyRight,
    InputKeyLeft,
    InputKeyOk,
    InputKeyBack,
    InputKeyStart,
    InputKeyBusy,
    InputKeyCustom,
    InputKeyOff,
    InputKeyApps,
    InputKeySettings,
    InputKeyMAX,
} InputKey;

extern const GpioPin gpio_fallback_confirm;
