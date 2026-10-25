/* fsim: GPIO нет. Нужен сервису input без intercom (запасная кнопка подтверждения):
   пин всегда «не нажат», события ввода приходят из управляющего канала (fsim_control.c). */
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    GpioModeInput,
    GpioModeOutputPushPull,
    GpioModeOutputOpenDrain,
    GpioModeAnalog,
} GpioMode;

typedef enum {
    GpioPullNo,
    GpioPullUp,
    GpioPullDown,
} GpioPull;

typedef enum {
    GpioSpeedLow,
    GpioSpeedMedium,
    GpioSpeedHigh,
    GpioSpeedVeryHigh,
} GpioSpeed;

typedef struct {
    uint32_t port;
    uint16_t pin;
} GpioPin;

void furi_hal_gpio_init(const GpioPin* gpio, GpioMode mode, GpioPull pull, GpioSpeed speed);

static inline void furi_hal_gpio_write(const GpioPin* gpio, bool state) {
    (void)gpio;
    (void)state;
}

static inline bool furi_hal_gpio_read(const GpioPin* gpio) {
    (void)gpio;
    return false;
}
