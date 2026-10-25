/* HAL-заглушки fsim для ядра furi (FS-2.1): ровно то, что зовёт fbt_layers/core_libs/lib/furi. */
#include <furi_hal.h>
#include <furi_hal_gpio.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#if defined(__EMSCRIPTEN__)
/* wasm: символов линкера нет — куча furi в статическом массиве (на Linux это fsim_heap.S) */
#define FSIM_HEAP_BYTES (64U * 1024 * 1024)
static uint8_t fsim_heap_area[FSIM_HEAP_BYTES] __attribute__((aligned(16)));
#define __heap_start__ fsim_heap_area
#define __heap_end__   (fsim_heap_area + FSIM_HEAP_BYTES)
#else
extern uint8_t __heap_start__[];
extern uint8_t __heap_end__[];
#endif

uint32_t furi_hal_cpu_get_frequency(void) {
    return 160000000UL;
}

uint32_t furi_hal_cpu_get_cycle_count(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint32_t)(now.tv_sec * 160000000ULL + now.tv_nsec * 16ULL / 100ULL);
}

uint32_t furi_hal_cpu_get_cycles_per_us(void) {
    return 160;
}

unsigned long fsim_run_time_counter(void) {
    return furi_hal_cpu_get_cycle_count();
}

void furi_hal_debug_enable(void) {
}

size_t furi_hal_flash_get_base(void) {
    return 0;
}

const void* furi_hal_flash_get_free_end_address(void) {
    return NULL;
}

const char* furi_hal_interrupt_get_name(uint8_t exception_number) {
    UNUSED(exception_number);
    return "host";
}

uint32_t furi_hal_interrupt_get_time_in_isr_total(void) {
    return 0;
}

void* furi_hal_memory_alloc(size_t size) {
    UNUSED(size);
    return NULL;
}

size_t furi_hal_memory_get_free(void) {
    return 0;
}

size_t furi_hal_memory_max_pool_block(void) {
    return 0;
}

static FuriHalMemoryHeapTrackMode fsim_track_mode = FuriHalMemoryHeapTrackModeNone;

void furi_hal_memory_set_heap_track_mode(FuriHalMemoryHeapTrackMode mode) {
    fsim_track_mode = mode;
}

FuriHalMemoryHeapTrackMode furi_hal_memory_get_heap_track_mode(void) {
    return fsim_track_mode;
}

FuriHalNvmBootMode furi_hal_nvm_get_boot_mode(void) {
    return FuriHalNvmBootModeNormal;
}

void furi_hal_nvm_set_boot_mode(FuriHalNvmBootMode mode) {
    UNUSED(mode);
}

void furi_hal_nvm_set_fault_data(uint32_t value) {
    UNUSED(value);
}

void furi_hal_power_reset(void) {
    fprintf(stderr, "[fsim] power reset requested\n");
    exit(3);
}

static FuriHalMemoryRegion fsim_heap_region;

const FuriHalMemoryRegion* furi_hal_memory_get_region(uint32_t index) {
    if(index != FuriHalMemoryRegionIdHeap) {
        return NULL;
    }
    fsim_heap_region.start = __heap_start__;
    fsim_heap_region.size_bytes = (size_t)(__heap_end__ - __heap_start__);
    return &fsim_heap_region;
}

uint32_t furi_hal_memory_get_region_count(void) {
    return 1;
}

void furi_hal_init_early(void) {
}

void furi_hal_init(void) {
    FURI_LOG_I("FuriHal", "fsim HAL init");
}

/* NVM: флаги и положение ручки — в памяти процесса (FS-2.2; в файл — если понадобится) */
static uint32_t fsim_nvm_flags;
static uint32_t fsim_switch_pos;

void furi_hal_nvm_reset(void) {
    fsim_nvm_flags = 0;
}

void furi_hal_nvm_set_flag(FuriHalNvmFlag flag) {
    fsim_nvm_flags |= 1u << flag;
}

void furi_hal_nvm_reset_flag(FuriHalNvmFlag flag) {
    fsim_nvm_flags &= ~(1u << flag);
}

bool furi_hal_nvm_is_flag_set(FuriHalNvmFlag flag) {
    return fsim_nvm_flags & (1u << flag);
}

void furi_hal_nvm_store_switch_pos(uint32_t pos) {
    fsim_switch_pos = pos;
}

uint32_t furi_hal_nvm_get_switch_pos(void) {
    return fsim_switch_pos;
}

time_t furi_hal_rtc_get_timestamp(void) {
    return time(NULL);
}

time_t furi_hal_rtc_get_timestamp_ms(void) {
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    return (time_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

DateTimeMs furi_hal_rtc_get_datetime(void) {
    return datetime_timestamp_ms_to_datetime(furi_hal_rtc_get_timestamp_ms());
}

void furi_hal_rtc_set_datetime(const DateTimeMs* datetime) {
    UNUSED(datetime);
}

/* «Бессонница» — запрет сна на баре; на хосте сна нет */
static uint16_t fsim_insomnia;

void furi_hal_power_insomnia_enter(void) {
    fsim_insomnia++;
}

void furi_hal_power_insomnia_exit(void) {
    fsim_insomnia--;
}

uint16_t furi_hal_power_insomnia_level(void) {
    return fsim_insomnia;
}

/* GPIO: пинов нет, см. furi_hal_gpio.h */
const GpioPin gpio_fallback_confirm = {0};

void furi_hal_gpio_init(const GpioPin* gpio, GpioMode mode, GpioPull pull, GpioSpeed speed) {
    UNUSED(gpio);
    UNUSED(mode);
    UNUSED(pull);
    UNUSED(speed);
}

/* Random: /dev/urandom хоста вместо RNG STM32 */
#include <furi_hal_random.h>
#include <sys/random.h>

void furi_hal_random_init(void) {
}

void furi_hal_random_fill_buf(uint8_t* buf, uint32_t len) {
    size_t filled = 0;
    while(filled < len) {
        ssize_t got = getrandom(buf + filled, len - filled, 0);
        furi_check(got > 0);
        filled += (size_t)got;
    }
}

uint32_t furi_hal_random_get(void) {
    uint32_t value;
    furi_hal_random_fill_buf((uint8_t*)&value, sizeof(value));
    return value;
}

/* Info: набор свойств устройства для device_info (на баре — OTP, ревизия платы, версии) */
#include <furi_hal_info.h>

void furi_hal_info_get(PropertyValueCallback callback, char sep, void* context) {
    UNUSED(sep);
    callback("hardware_model", "fsim", false, context);
    callback("firmware_version", "fsim", true, context);
}

/* Холостая задача. На микроконтроллере она засыпает по WFI; в POSIX-порту FreeRTOS она крутилась
   бы на 100 % ядра. Сон прерывается сигналом тика (1 кГц), так что переключение задач не
   задерживается. Так же сделано в демо POSIX-порта FreeRTOS. */
#include <time.h>

void vApplicationIdleHook(void) {
    struct timespec pause = {.tv_sec = 0, .tv_nsec = 1000000};
    nanosleep(&pause, NULL);
}
