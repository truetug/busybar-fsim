/* Подмена сервиса power_srv: на хосте «питание от сети, 100%». Запись нужна gui первой. */
#include <furi.h>
#include <power/power_service/power.h>
#include <stdlib.h>

struct Power {
    uint32_t unused;
};

int32_t power_srv_app(void* p) {
    UNUSED(p);
    Power* instance = calloc(1, sizeof(Power));
    furi_record_create(RECORD_POWER, instance);
    FSIM_SERVICE_PARK();
}

/* Выключение на хосте — завершение процесса */
bool power_off(Power* power) {
    UNUSED(power);
    FURI_LOG_I("FsimPower", "power off");
    exit(0);
}
