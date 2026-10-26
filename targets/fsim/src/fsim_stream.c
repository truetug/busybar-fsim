/* Поток кадров fsim для сервиса fsim-stream (виртуальный бар в браузере).

   FSIM_STREAM_PORT=<порт> (хост FSIM_STREAM_HOST, по умолчанию 127.0.0.1) — TCP-сервер, один клиент за раз.
   Клиенту сразу отправляются последние кадры обоих экранов, дальше — каждый новый кадр.
   Сообщение: u8 экран (0 передний 72×16 RGB, 1 задний 160×80 L8), u32 BE метка мс от старта (furi_get_tick),
   u32 BE длина, кадр. Если клиент не успевает, промежуточные кадры пропускаются (остаётся последний).

   Публикация идёт из потока GUI: только копия кадра под коротким замком и счётчик, без сети. */
#include <furi.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define TAG                 "FsimStream"
#define STREAM_POLL_MS      8
#define STREAM_MAX_FRAME    (160 * 80)
#define STREAM_DISPLAYS     2

typedef struct {
    uint8_t data[STREAM_MAX_FRAME];
    size_t length;
    uint32_t tick;
    uint32_t sequence; /* растёт на каждый новый кадр; 0 — кадра ещё не было */
} StreamSlot;

static StreamSlot fsim_stream_slots[STREAM_DISPLAYS];
static pthread_mutex_t fsim_stream_lock = PTHREAD_MUTEX_INITIALIZER;

void fsim_stream_publish(int display, const uint8_t* data, size_t length) {
    if(display < 0 || display >= STREAM_DISPLAYS || length > STREAM_MAX_FRAME) {
        return;
    }
    pthread_mutex_lock(&fsim_stream_lock);
    StreamSlot* slot = &fsim_stream_slots[display];
    memcpy(slot->data, data, length);
    slot->length = length;
    slot->tick = furi_get_tick();
    slot->sequence++;
    pthread_mutex_unlock(&fsim_stream_lock);
}

static bool fsim_stream_send_all(int client, const uint8_t* data, size_t length) {
    while(length > 0) {
        ssize_t sent = send(client, data, length, MSG_NOSIGNAL);
        if(sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            furi_delay_ms(2);
            continue;
        }
        if(sent <= 0) {
            return false;
        }
        data += sent;
        length -= (size_t)sent;
    }
    return true;
}

static bool fsim_stream_send_frame(int client, int display, const StreamSlot* frame) {
    uint8_t header[9] = {(uint8_t)display};
    uint32_t tick = htonl(frame->tick);
    uint32_t length = htonl((uint32_t)frame->length);
    memcpy(header + 1, &tick, 4);
    memcpy(header + 5, &length, 4);
    return fsim_stream_send_all(client, header, sizeof(header)) &&
           fsim_stream_send_all(client, frame->data, frame->length);
}

static int32_t fsim_stream_thread(void* context) {
    UNUSED(context);
    const char* port = getenv("FSIM_STREAM_PORT");
    const char* host = getenv("FSIM_STREAM_HOST") ? getenv("FSIM_STREAM_HOST") : "127.0.0.1";
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    struct sockaddr_in address = {.sin_family = AF_INET, .sin_port = htons((uint16_t)atoi(port))};
    inet_pton(AF_INET, host, &address.sin_addr);
    if(bind(listener, (struct sockaddr*)&address, sizeof(address)) != 0 || listen(listener, 1) != 0) {
        FURI_LOG_E(TAG, "cannot listen on %s:%s: %s", host, port, strerror(errno));
        close(listener);
        return 0;
    }
    fcntl(listener, F_SETFL, O_NONBLOCK);
    FURI_LOG_I(TAG, "frames on %s:%s", host, port);

    int client = -1;
    uint32_t sent_sequence[STREAM_DISPLAYS] = {0};
    static StreamSlot snapshot; /* копия кадра, чтобы не держать замок во время send */
    while(true) {
        if(client < 0) {
            client = accept(listener, NULL, NULL);
            if(client >= 0) {
                fcntl(client, F_SETFL, O_NONBLOCK);
                memset(sent_sequence, 0, sizeof(sent_sequence)); /* новый клиент — снова все кадры */
            }
        }
        if(client >= 0) {
            char probe;
            ssize_t received = recv(client, &probe, 1, 0);
            if(received == 0 || (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
                close(client);
                client = -1;
            }
        }
        for(int display = 0; client >= 0 && display < STREAM_DISPLAYS; display++) {
            pthread_mutex_lock(&fsim_stream_lock);
            bool fresh = fsim_stream_slots[display].sequence != sent_sequence[display] &&
                         fsim_stream_slots[display].length > 0;
            if(fresh) {
                memcpy(&snapshot, &fsim_stream_slots[display], sizeof(snapshot));
            }
            pthread_mutex_unlock(&fsim_stream_lock);
            if(fresh) {
                sent_sequence[display] = snapshot.sequence;
                if(!fsim_stream_send_frame(client, display, &snapshot)) {
                    close(client);
                    client = -1;
                }
            }
        }
        furi_delay_ms(STREAM_POLL_MS);
    }
}

void fsim_stream_start(void) {
    if(getenv("FSIM_STREAM_PORT")) {
        furi_thread_start(furi_thread_alloc_ex("FsimStream", 8192, fsim_stream_thread, NULL));
    }
}
