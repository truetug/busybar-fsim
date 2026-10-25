/* Подмены периферии, которой нет на хосте: яркость (датчик света + ШИМ), статус-диоды (через
   intercom), mDNS (lwip). API — как у сервисов прошивки; состояние хранится, действия — в лог. */
#include <furi.h>
#include <brightness_control/brightness_control.h>
#include <status_lights/status_lights.h>
#include <discovery/discovery.h>
#include <netstat/netstat.h>

#define TAG "FsimPeriph"

/* --- brightness_control --- */

struct BrightnessControl {
    FuriState* state;
};

void brightness_control_set_auto_brightness(BrightnessControl* instance) {
    BrightnessControlState state;
    furi_state_get(instance->state, &state);
    state.mode = BrightnessControlBrightnessModeAuto;
    state.effective_brightness = BRIGHTNESS_MAX;
    furi_state_set(instance->state, &state);
}

void brightness_control_set_manual_brightness(BrightnessControl* instance, uint8_t brightness) {
    BrightnessControlState state = {
        .mode = BrightnessControlBrightnessModeManual,
        .effective_brightness = brightness,
        .brightness_setting = brightness,
    };
    furi_state_set(instance->state, &state);
}

void brightness_control_set_brightness_override(
    BrightnessControl* instance,
    BrightnessControlModule module,
    uint8_t override) {
    UNUSED(instance);
    UNUSED(module);
    UNUSED(override);
}

void brightness_control_reset_brightness_override(
    BrightnessControl* instance,
    BrightnessControlModule module) {
    UNUSED(instance);
    UNUSED(module);
}

FuriState* brightness_control_get_state(const BrightnessControl* instance) {
    return instance->state;
}

int32_t brightness_control_srv(void* arg) {
    UNUSED(arg);
    BrightnessControl* instance = malloc(sizeof(BrightnessControl));
    instance->state = furi_state_alloc(sizeof(BrightnessControlState));
    BrightnessControlState state = {
        .mode = BrightnessControlBrightnessModeAuto,
        .effective_brightness = BRIGHTNESS_MAX,
        .brightness_setting = BRIGHTNESS_MAX,
    };
    furi_state_set(instance->state, &state);
    furi_record_create(RECORD_BRIGHTNESS_CONTROL, instance);
    FSIM_SERVICE_PARK();
}

/* --- status_lights --- */

struct StatusLights {
    StatusLightsBrightness brightness;
};

StatusLightsStatus
    status_lights_run_preset(StatusLights* instance, StatusLightsPreset preset, Color color) {
    UNUSED(instance);
    FURI_LOG_I(TAG, "status lights preset %d color %02x%02x%02x", preset, color.r, color.g, color.b);
    return StatusLightsStatusOk;
}

StatusLightsStatus
    status_lights_set_brightness(StatusLights* instance, StatusLightsBrightness brightness) {
    instance->brightness = brightness;
    return StatusLightsStatusOk;
}

StatusLightsStatus
    status_lights_get_brightness(StatusLights* instance, StatusLightsBrightness* brightness) {
    *brightness = instance->brightness;
    return StatusLightsStatusOk;
}

int32_t status_lights_srv(void* arg) {
    UNUSED(arg);
    StatusLights* instance = malloc(sizeof(StatusLights));
    instance->brightness.val = 100;
    furi_record_create(RECORD_STATUS_LIGHTS, instance);
    FSIM_SERVICE_PARK();
}

/* --- discovery: mDNS на хосте не анонсируем, адрес fsim и так известен --- */

struct Discovery {
    uint32_t services;
};

bool discovery_add_service(Discovery* discovery, const DiscoveryServiceInfo* info, void* context) {
    UNUSED(context);
    discovery->services++;
    FURI_LOG_I(TAG, "mDNS (not announced in fsim): %s.%s port %u", info->name, info->service, info->port);
    return true;
}

int32_t discovery_srv(void* arg) {
    UNUSED(arg);
    Discovery* instance = malloc(sizeof(Discovery));
    instance->services = 0;
    furi_record_create(RECORD_DISCOVERY, instance);
    FSIM_SERVICE_PARK();
}

/* --- netstat: счётчики соединений lwip; на хосте стек не перегружается --- */

bool netstat_is_overloaded(NetstatLog log) {
    UNUSED(log);
    return false;
}

/* --- mqtt: облако BUSY в fsim не подключаем --- */
#include <mqtt/mqtt.h>

struct Mqtt {
    FuriPubSub* pubsub;
};

FuriPubSub* mqtt_get_pubsub(Mqtt* instance) {
    return instance->pubsub;
}

bool mqtt_publish(Mqtt* instance, MqttQos qos, const char* topic, const void* data, size_t data_size) {
    UNUSED(instance);
    UNUSED(qos);
    UNUSED(data);
    FURI_LOG_D(TAG, "mqtt (no cloud in fsim): %s, %zu bytes", topic, data_size);
    return false;
}

int32_t mqtt_srv(void* arg) {
    UNUSED(arg);
    Mqtt* instance = malloc(sizeof(Mqtt));
    instance->pubsub = furi_pubsub_alloc();
    furi_record_create(RECORD_MQTT, instance);
    FSIM_SERVICE_PARK();
}
