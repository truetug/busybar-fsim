/* Управляющий канал fsim: кнопки, колесо и ручка режимов вместо SiWG917.
   События идут в сервис input прошивки через его публичный API (input_key_press / release /
   toggle) — так же, как их доставляет intercom на баре.

   Команды, по одной в строке:
     switch busy|custom|off|apps|settings   — ручка режимов
     short|long|press|release <кнопка>      — ok, back, start (и up, down, left, right)
     wheel <N>                              — колесо: N > 0 вверх, N < 0 вниз
     wait <мс>
     mark <метка>                           — строка «мс mark метка» в frames.log (таймлайн записи)
     time set HH:MM:SS                      — часы прошивки: время суток в её часовом поясе (дата не меняется)
     time offset ±N                         — сдвинуть часы прошивки на N секунд
   FSIM_INPUT="switch apps; wait 2000; short ok" — сценарий при старте.
   FSIM_CONTROL_PORT=<порт> — TCP на FSIM_CONTROL_HOST (по умолчанию 127.0.0.1), ответ ok / error. */
#include <furi.h>
#include <input/input.h>
#include <desktop/desktop.h>
#include <loader/loader.h>
#include <time/time.h>
#include <utz/utz.h>
#include <datetime/datetime.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <time.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <unistd.h>

#define TAG "FsimControl"

#define FSIM_CONTROL_POLL_MS 10
#define FSIM_SHORT_PRESS_MS  50
/* Long в input — на втором срабатывании таймера (INPUT_PRESS_TICKS × INPUT_LONG_PRESS_COUNTS) */
#define FSIM_LONG_PRESS_MS   400

typedef struct {
    const char* name;
    InputKey key;
} FsimKeyName;

static const FsimKeyName fsim_keys[] = {
    {"up", InputKeyUp},
    {"down", InputKeyDown},
    {"right", InputKeyRight},
    {"left", InputKeyLeft},
    {"ok", InputKeyOk},
    {"back", InputKeyBack},
    {"start", InputKeyStart},
};

static const FsimKeyName fsim_switch_positions[] = {
    {"busy", InputKeyBusy},
    {"custom", InputKeyCustom},
    {"off", InputKeyOff},
    {"apps", InputKeyApps},
    {"settings", InputKeySettings},
};

static bool fsim_lookup(const FsimKeyName* names, size_t count, const char* name, InputKey* key) {
    for(size_t i = 0; i < count; i++) {
        if(strcasecmp(names[i].name, name) == 0) {
            *key = names[i].key;
            return true;
        }
    }
    return false;
}

int64_t fsim_clock_get_offset_ms(void);
void fsim_clock_add_offset_ms(int64_t delta_ms);

/* Время суток в часовом поясе прошивки (то, что показывает Clock) и сдвиг на нужное */
static bool fsim_clock_set_time_of_day(unsigned hours, unsigned minutes, unsigned seconds) {
    if(hours > 23 || minutes > 59 || seconds > 59) {
        return false;
    }
    Time* time_service = furi_record_open(RECORD_TIME);
    TimeSettings settings;
    time_get_settings(time_service, &settings);
    furi_record_close(RECORD_TIME);

    struct timespec real;
    clock_gettime(CLOCK_REALTIME, &real);
    int64_t now_ms = (int64_t)real.tv_sec * 1000 + real.tv_nsec / 1000000 + fsim_clock_get_offset_ms();
    DateTimeMs date = datetime_timestamp_ms_to_datetime((time_t)now_ms);
    utz_offset_t zone;
    utz_get_current_offset(&settings.timezone, &date.dt, &zone);
    int64_t zone_s = (int64_t)(zone.hours * 60 + zone.minutes) * 60;

    int64_t local_s = now_ms / 1000 + zone_s;
    int64_t time_of_day = ((local_s % 86400) + 86400) % 86400;
    int64_t target = (int64_t)hours * 3600 + minutes * 60 + seconds;
    fsim_clock_add_offset_ms((target - time_of_day) * 1000 - (now_ms % 1000));
    return true;
}

static bool fsim_control_execute(Input* input, char* line) {
    char* command = strtok(line, " \t\r\n");
    char* argument = strtok(NULL, " \t\r\n");
    char* value = strtok(NULL, " \t\r\n");
    if(!command) {
        return true;
    }
    if(strcasecmp(command, "mark") == 0 && argument) {
        const char* directory = getenv("FSIM_FRAMES");
        if(!directory) {
            return false;
        }
        char path[512];
        snprintf(path, sizeof(path), "%s/frames.log", directory);
        FILE* log = fopen(path, "a");
        if(!log) {
            return false;
        }
        fprintf(log, "%lu mark %s\n", (unsigned long)furi_get_tick(), argument);
        fclose(log);
        return true;
    }
    if(strcasecmp(command, "time") == 0 && argument && value) {
        unsigned hours, minutes, seconds;
        char tail;
        if(strcasecmp(argument, "set") == 0 &&
           sscanf(value, "%2u:%2u:%2u%c", &hours, &minutes, &seconds, &tail) == 3) {
            return fsim_clock_set_time_of_day(hours, minutes, seconds);
        }
        long offset;
        if(strcasecmp(argument, "offset") == 0 && sscanf(value, "%ld%c", &offset, &tail) == 1) {
            fsim_clock_add_offset_ms((int64_t)offset * 1000);
            return true;
        }
        return false;
    }
    InputKey key;
    if(strcasecmp(command, "switch") == 0 && argument &&
       fsim_lookup(fsim_switch_positions, COUNT_OF(fsim_switch_positions), argument, &key)) {
        input_key_toggle(input, key);
    } else if(strcasecmp(command, "wheel") == 0 && argument) {
        int steps = atoi(argument);
        for(int i = 0; i < abs(steps); i++) {
            input_key_toggle(input, steps > 0 ? InputKeyUp : InputKeyDown);
            furi_delay_ms(FSIM_CONTROL_POLL_MS);
        }
    } else if(strcasecmp(command, "wait") == 0 && argument) {
        furi_delay_ms((uint32_t)atoi(argument));
    } else if(argument && fsim_lookup(fsim_keys, COUNT_OF(fsim_keys), argument, &key)) {
        if(strcasecmp(command, "press") == 0) {
            input_key_press(input, key);
        } else if(strcasecmp(command, "release") == 0) {
            input_key_release(input, key);
        } else if(strcasecmp(command, "short") == 0 || strcasecmp(command, "long") == 0) {
            input_key_press(input, key);
            furi_delay_ms(command[0] == 's' ? FSIM_SHORT_PRESS_MS : FSIM_LONG_PRESS_MS);
            input_key_release(input, key);
        } else {
            return false;
        }
    } else {
        return false;
    }
    FURI_LOG_I(TAG, "%s %s", command, argument ? argument : "");
    return true;
}

static void fsim_control_run_script(Input* input, const char* script) {
    char* copy = strdup(script);
    char* saved = NULL;
    for(char* step = strtok_r(copy, ";", &saved); step; step = strtok_r(NULL, ";", &saved)) {
        if(!fsim_control_execute(input, step)) {
            FURI_LOG_E(TAG, "bad FSIM_INPUT step: %s", step);
        }
    }
    free(copy);
}

/* Сокеты неблокирующие и опрашиваются из потока furi: блокирующий вызов в задаче POSIX-порта
   FreeRTOS остановил бы планировщик. */
static int fsim_control_listen(void) {
    const char* port = getenv("FSIM_CONTROL_PORT");
    if(!port) {
        return -1;
    }
    const char* host = getenv("FSIM_CONTROL_HOST") ? getenv("FSIM_CONTROL_HOST") : "127.0.0.1";
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons((uint16_t)atoi(port))};
    inet_pton(AF_INET, host, &address.sin_addr);
    if(bind(listener, (struct sockaddr*)&address, sizeof(address)) != 0 || listen(listener, 1) != 0) {
        FURI_LOG_E(TAG, "cannot listen on %s:%s: %s", host, port, strerror(errno));
        close(listener);
        return -1;
    }
    fcntl(listener, F_SETFL, O_NONBLOCK);
    FURI_LOG_I(TAG, "control on %s:%s", host, port);
    return listener;
}

static int32_t fsim_control_thread(void* context) {
    UNUSED(context);
    Input* input = furi_record_open(RECORD_INPUT);
    /* desktop создаёт свою запись после подписки на положение ручки. Начальный сценарий («switch apps») до этого
       момента теряется: на быстрой машине прошивка грузится за ~10 мс, desktop игнорирует запросы запуска, пока не
       получит начальное положение, и приложение, запущенное по API сразу после старта, не запускается (в эмуляции
       всё медленнее, поэтому там гонки не видно). */
    furi_record_open(RECORD_DESKTOP);
    furi_record_close(RECORD_DESKTOP);
    /* desktop реагирует на положение ручки, только пока загрузчик считает текущим приложением «Power On»
       (desktop_startup_app_is_running). В первые миллисекунды после старта это ещё не так: ждём. */
    Loader* loader = furi_record_open(RECORD_LOADER);
    FuriString* current = furi_string_alloc();
    for(int attempt = 0; attempt < 600; attempt++) {
        loader_get_application_name(loader, current);
        if(furi_string_equal(current, "Power On")) {
            break;
        }
        furi_delay_ms(10);
    }
    furi_string_free(current);
    furi_record_close(RECORD_LOADER);
    furi_delay_ms(50);
    const char* script = getenv("FSIM_INPUT");
    if(script) {
        fsim_control_run_script(input, script);
    }
    int listener = fsim_control_listen();
    if(listener < 0) {
        return 0;
    }
    int client = -1;
    char buffer[256];
    size_t length = 0;
    while(true) {
        if(client < 0) {
            client = accept(listener, NULL, NULL);
            if(client >= 0) {
                fcntl(client, F_SETFL, O_NONBLOCK);
                length = 0;
            }
        }
        if(client >= 0) {
            ssize_t received = recv(client, buffer + length, sizeof(buffer) - 1 - length, 0);
            if(received == 0 || (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
                close(client);
                client = -1;
            } else if(received > 0) {
                length += (size_t)received;
                buffer[length] = 0;
                char* newline;
                while((newline = strchr(buffer, '\n'))) {
                    *newline = 0;
                    const char* answer = fsim_control_execute(input, buffer) ? "ok\n" : "error\n";
                    send(client, answer, strlen(answer), MSG_NOSIGNAL);
                    length -= (size_t)(newline + 1 - buffer);
                    memmove(buffer, newline + 1, length + 1);
                }
                if(length == sizeof(buffer) - 1) {
                    length = 0;
                }
            }
        }
        furi_delay_ms(FSIM_CONTROL_POLL_MS);
    }
}

void fsim_control_start(void) {
    furi_thread_start(furi_thread_alloc_ex("FsimControl", 8192, fsim_control_thread, NULL));
}
