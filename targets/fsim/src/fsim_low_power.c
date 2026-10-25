/* Подмена сервиса low_power: на хосте сна нет, блокировки — счётчик. Запись нужна canvas. */
#include <furi.h>
#include <low_power/low_power.h>

struct LowPower {
    uint32_t locks;
};

void low_power_lock(LowPower* instance) {
    instance->locks++;
}

void low_power_unlock(LowPower* instance) {
    instance->locks--;
}

int32_t low_power_srv(void* p) {
    UNUSED(p);
    LowPower* instance = calloc(1, sizeof(LowPower));
    furi_record_create(RECORD_LOW_POWER, instance);
    FSIM_SERVICE_PARK();
}
