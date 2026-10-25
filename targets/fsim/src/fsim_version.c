/* Замена core_libs/lib/version/version.c: оригинал проверяет static_assert-ами смещения полей под
   32-битные указатели и не собирается на 64-битном хосте. API тот же, значения — из CMake. */
#include <version/version.h>
#include <furi_hal_version.h>
#include <stdbool.h>
#include <stdint.h>

struct Version {
    const char* git_hash;
    const char* git_branch;
    const char* build_date;
    const char* version;
    uint8_t target;
};

static const Version fsim_version = {
    .git_hash = FSIM_GIT_HASH,
    .git_branch = FSIM_GIT_BRANCH,
    .build_date = __DATE__,
    .version = "fsim",
    .target = 22,
};

const Version* version_get(void) {
    return &fsim_version;
}

const char* version_get_githash(const Version* v) {
    return v ? v->git_hash : "";
}

const char* version_get_gitbranch(const Version* v) {
    return v ? v->git_branch : "";
}

const char* version_get_gitbranchnum(const Version* v) {
    return "0";
}

const char* version_get_builddate(const Version* v) {
    return v ? v->build_date : "";
}

const char* version_get_version(const Version* v) {
    return v ? v->version : "";
}

uint8_t version_get_target(const Version* v) {
    return v ? v->target : 0;
}

bool version_get_dirty_flag(const Version* v) {
    return false;
}

const char* version_get_firmware_origin(const Version* v) {
    return "fsim";
}

const char* version_get_git_origin(const Version* v) {
    return "https://github.com/busy-app/busybar-firmware";
}

/* MAC USB-интерфейса: из него web_server строит имя mDNS-сервиса busybar-<mac>.
   Локально администрируемый адрес, чтобы не совпасть с настоящими барами. */
const uint8_t* furi_hal_version_get_usb_mac(void) {
    static const uint8_t mac[FURI_HAL_VERSION_MAC_LENGTH] = {0x02, 0xf5, 0x1d, 0x00, 0x00, 0x01};
    return mac;
}
