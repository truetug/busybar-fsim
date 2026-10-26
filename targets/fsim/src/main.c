/* fsim: точка входа как в targets/f21/src/main.c, без загрузчика обновлений и NVM-режимов.
   FSIM_RUN_SECONDS=N — выйти через N секунд (для смоука). */
#include <furi.h>
#include <furi_hal.h>
#include <flipper.h>
#if !defined(__EMSCRIPTEN__)
#include <execinfo.h>
#endif
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>
#include <storage/storage.h>
#include <loader/loader.h>
#include <time/time.h>
#include <utz/utz.h>

#define TAG "Fsim"

/* На баре лог идёт в последовательный порт (furi_hal_serial); на хосте — в stdout. */
static void fsim_log_to_stdout(const uint8_t* data, size_t size, void* context) {
    UNUSED(context);
    fwrite(data, 1, size, stdout);
}

/* Падение: адреса стека в stderr. Разбор: addr2line -f -e build23/fsim <адреса> */
#if !defined(__EMSCRIPTEN__)
static void fsim_crash_handler(int signal_number) {
    void* frames[64];
    int count = backtrace(frames, COUNT_OF(frames));
    dprintf(STDERR_FILENO, "fsim: signal %d, backtrace:\n", signal_number);
    backtrace_symbols_fd(frames, count, STDERR_FILENO);
    _exit(128 + signal_number);
}
#endif

static int32_t fsim_watchdog(void* context) {
    UNUSED(context);
    const char* seconds = getenv("FSIM_RUN_SECONDS");
    if(!seconds) {
        return 0;
    }
    furi_delay_ms((uint32_t)atoi(seconds) * 1000);
    FURI_LOG_I(TAG, "FSIM_RUN_SECONDS reached, exiting");
    exit(0);
}

/* FSIM_PROBE_STORAGE=1: прочитать через API хранилища прошивки то, что лежит на SD из CI-сборки */
static int32_t fsim_probe_storage(void* context) {
    UNUSED(context);
    furi_delay_ms(500);
    Storage* storage = furi_record_open(RECORD_STORAGE);
    const char* manifest = EXT_PATH("user_assets/app.busy.clock/appmeta/manifest.json");
    File* file = storage_file_alloc(storage);
    if(storage_file_open(file, manifest, FSAM_READ, FSOM_OPEN_EXISTING)) {
        char buffer[256] = {0};
        size_t read = storage_file_read(file, buffer, sizeof(buffer) - 1);
        FURI_LOG_I(TAG, "%s (%zu bytes): %s", manifest, read, buffer);
    } else {
        FURI_LOG_E(TAG, "cannot open %s", manifest);
    }
    storage_file_close(file);
    size_t entries = 0;
    if(storage_dir_open(file, EXT_PATH("apps_assets"))) {
        FileInfo info;
        char name[128];
        while(storage_dir_read(file, &info, name, sizeof(name))) {
            entries++;
        }
    }
    storage_dir_close(file);
    storage_file_free(file);
    FURI_LOG_I(TAG, "/ext/apps_assets: %zu entries", entries);
    furi_record_close(RECORD_STORAGE);
    return 0;
}

/* FSIM_START_APP=<appid> [FSIM_START_ARGS]: запустить приложение через loader прошивки.
   Временная замена desktop (он запускает apps_menu по положению ручки) до FS-2.3b. */
static int32_t fsim_start_app(void* context) {
    UNUSED(context);
    furi_delay_ms(500);
    const char* name = getenv("FSIM_START_APP");
    Loader* loader = furi_record_open(RECORD_LOADER);
    FuriString* error = furi_string_alloc();
    LoaderStatus status = loader_start(loader, name, getenv("FSIM_START_ARGS"), error);
    FURI_LOG_I(TAG, "start %s: status %d %s", name, status, furi_string_get_cstr(error));
    furi_string_free(error);
    furi_record_close(RECORD_LOADER);
    return 0;
}

/* Часовой пояс — из окружения: FSIM_TZ (имя зоны utz, «Moscow») или TZ (IANA, «Europe/Moscow»
   → «Moscow»). SNTP выключаем: часы — хостовые, сеть наружу закрыта (fsim_net.c). */
static void fsim_apply_time_settings(void) {
    Time* time = furi_record_open(RECORD_TIME);
    TimeSettings settings;
    time_get_settings(time, &settings);
    settings.is_enabled = false;
    const char* zone = getenv("FSIM_TZ");
    char name[64] = {0};
    if(!zone && getenv("TZ")) {
        const char* tz = getenv("TZ");
        const char* slash = strrchr(tz, '/');
        strlcpy(name, slash ? slash + 1 : tz, sizeof(name));
        for(char* c = name; *c; c++) {
            if(*c == '_') *c = ' ';
        }
        zone = name;
    }
    if(zone && !utz_get_zone_by_name(zone, &settings.timezone)) {
        FURI_LOG_E(TAG, "unknown time zone '%s', keeping firmware default", zone);
    } else if(zone) {
        FURI_LOG_I(TAG, "time zone: %s", zone);
    }
    time_set_settings(time, &settings);
    furi_record_close(RECORD_TIME);
}

void fsim_control_start(void);
void fsim_stream_start(void);

static int32_t init_task(void* context) {
    UNUSED(context);
    furi_hal_init();
    flipper_init();
    if(getenv("FSIM_PROBE_STORAGE")) {
        furi_thread_start(furi_thread_alloc_ex("FsimProbe", 8192, fsim_probe_storage, NULL));
    }
    fsim_apply_time_settings();
    fsim_control_start();
    fsim_stream_start();
    if(getenv("FSIM_START_APP")) {
        furi_thread_start(furi_thread_alloc_ex("FsimStart", 4096, fsim_start_app, NULL));
    }
    FuriThread* watchdog = furi_thread_alloc_ex("FsimWatchdog", 4096, fsim_watchdog, NULL);
    furi_thread_start(watchdog);
    furi_thread_set_current_priority(FuriThreadPriorityHighest);
    furi_background();
    return 0;
}

int main(int argc, char** argv) {
#if defined(__EMSCRIPTEN__)
    /* wasm: main идёт в воркере со своим ENV, поэтому настройки приходят аргументами KEY=VALUE
       (Module.arguments в браузере, argv в node) */
    for(int i = 1; i < argc; i++) {
        if(strchr(argv[i], '=')) {
            putenv(strdup(argv[i]));
        }
    }
#else
    UNUSED(argc);
    UNUSED(argv);
#endif
    setvbuf(stdout, NULL, _IONBF, 0);
#if !defined(__EMSCRIPTEN__)
    signal(SIGSEGV, fsim_crash_handler);
    signal(SIGBUS, fsim_crash_handler);
    signal(SIGABRT, fsim_crash_handler);
#endif
    furi_init();
    furi_log_add_handler((FuriLogHandler){.callback = fsim_log_to_stdout, .context = NULL});
    furi_hal_init_early();
    /* FSIM_LOG_LEVEL=debug|trace — подробнее, чем по умолчанию */
    FuriLogLevel level;
    if(getenv("FSIM_LOG_LEVEL") && furi_log_level_from_string(getenv("FSIM_LOG_LEVEL"), &level)) {
        furi_log_set_level(level);
    }
    FuriThread* main_thread = furi_thread_alloc_ex("Init", 4096, init_task, NULL);
    furi_thread_set_priority(main_thread, FuriThreadPriorityInit);
    furi_thread_start(main_thread);
    furi_run();
    furi_crash("Kernel is Dead");
}
