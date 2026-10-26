/* Сеть fsim: подмены network (хук старта) и wifi (сервис) + ожидание сокетов.
   На баре: lwip + netif Wi-Fi через intercom к SiWG917. На хосте: сокеты ОС, Wi-Fi всегда
   «подключён», DNS — первый IPv4 nameserver из /etc/resolv.conf. */
#include <furi.h>
#include <network/network.h>
#include <wifi/wifi.h>
#include <mongoose_glue.h>
#include <mongoose_dns_i.h>
#include <mongoose_tls.h>
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <stdio.h>
#include <string.h>

#define TAG "FsimNet"

#define DUMMY_RECORD ((void*)0xD00DAD)

/* --- network --- */

struct netif* network_find_netif(NetworkNetif id) {
    UNUSED(id);
    return NULL;
}

void network_netif_assign_name(struct netif* netif, NetworkNetif id) {
    UNUSED(netif);
    UNUSED(id);
}

void network_init_current_thread(Network* instance) {
    furi_check(instance == DUMMY_RECORD);
}

void network_deinit_current_thread(Network* instance) {
    furi_check(instance == DUMMY_RECORD);
}

/* Как network_on_system_start на U5, без tcpip_init */
void network_on_system_start(void) {
    mg_init_early();
    furi_record_create(RECORD_NETWORK, DUMMY_RECORD);
    mongoose_dns_startup();
}

/* --- wifi --- */

struct Wifi {
    FuriState* state;
};

static uint32_t fsim_net_resolver(void) {
    uint32_t address = 0;
    FILE* file = fopen("/etc/resolv.conf", "r");
    if(!file) {
        return address;
    }
    char line[256];
    char host[64];
    while(fgets(line, sizeof(line), file)) {
        struct in_addr parsed;
        if(sscanf(line, "nameserver %63s", host) == 1 && inet_pton(AF_INET, host, &parsed) == 1) {
            address = parsed.s_addr;
            break;
        }
    }
    fclose(file);
    return address;
}

FuriState* wifi_get_state(Wifi* instance) {
    furi_check(instance);
    return instance->state;
}

WifiStatus wifi_get_info(Wifi* instance, WifiInfo* info) {
    furi_check(instance);
    furi_state_get(instance->state, info);
    return WifiStatusOk;
}

int32_t wifi_srv(void* arg) {
    UNUSED(arg);
    Wifi* instance = malloc(sizeof(Wifi));
    instance->state = furi_state_alloc(sizeof(WifiInfo));
    WifiInfo info = {
        .ssid = "fsim",
        .rssi = -40,
        .security_mode = WifiSecurityModeWpa2,
        .state = WifiStateConnected,
        .ip_config = {.mgmt = WifiIpManagementDynamic, .type = WifiIpTypeV4},
    };
    info.ip_config.ip4.dns.value = fsim_net_resolver();
    furi_state_set(instance->state, &info);
    furi_record_create(RECORD_WIFI, instance);
    FURI_LOG_I(TAG, "wifi connected (host network), dns " WIFI_IP4_ADDR_FORMAT,
        info.ip_config.ip4.dns.bytes[0], info.ip_config.ip4.dns.bytes[1],
        info.ip_config.ip4.dns.bytes[2], info.ip_config.ip4.dns.bytes[3]);
    FSIM_SERVICE_PARK();
}

/* --- TLS: пока нет --- */

bool mongoose_tls_init(struct mg_connection* conn, const char* url, const TlsConfig* tls_config) {
    UNUSED(conn);
    UNUSED(tls_config);
    FURI_LOG_E(TAG, "TLS is not built in fsim yet: %s", url);
    return false;
}

#if !defined(__EMSCRIPTEN__)
/* --- ожидание сокетов ---
   Поток задачи, заблокированный в poll() ядра, для FreeRTOS остаётся «готовым»: задачи с
   меньшим приоритетом не получают процессор, пока он ждёт. Поэтому ждём кусками по тику,
   отдавая процессор через furi_delay_ms. */
int __real_poll(struct pollfd* fds, nfds_t count, int timeout);

int __wrap_poll(struct pollfd* fds, nfds_t count, int timeout) {
    uint32_t started = furi_get_tick();
    while(true) {
        int ready = __real_poll(fds, count, 0);
        if(ready != 0 || timeout == 0) {
            return ready;
        }
        if(timeout > 0 && furi_get_tick() - started >= (uint32_t)timeout) {
            return 0;
        }
        furi_delay_ms(1);
    }
}

/* --- исходящие соединения: только localhost (решение владельца 25.10) ---
   Виртуальный бар не ходит в сеть: приложения получают отказ сразу, без таймаутов (Weather
   показывает свой экран ошибки), SNTP и DNS тоже. HTTP API самого бара (localhost) работает.
   FSIM_NETWORK=host — разрешить сеть хоста (отладка). */
int __real_connect(int fd, const struct sockaddr* address, socklen_t length);

static bool fsim_net_is_loopback(const struct sockaddr* address) {
    if(address->sa_family == AF_INET) {
        const struct sockaddr_in* v4 = (const struct sockaddr_in*)address;
        return (ntohl(v4->sin_addr.s_addr) >> 24) == 127;
    }
    if(address->sa_family == AF_INET6) {
        const struct sockaddr_in6* v6 = (const struct sockaddr_in6*)address;
        return IN6_IS_ADDR_LOOPBACK(&v6->sin6_addr);
    }
    return true; /* AF_UNIX и прочее — локальное */
}

/* Порт HTTP API бара зашит в прошивке (80). Несколько fsim на одной машине (сессии fsim-stream) — у каждого
   свой порт, FSIM_HTTP_PORT: слушающий сокет и обращения приложений на localhost:80 перенаправляются. */
static int fsim_http_port(void) {
    static int port = -1;
    if(port < 0) {
        const char* text = getenv("FSIM_HTTP_PORT");
        port = text ? atoi(text) : 80;
    }
    return port;
}

int __real_bind(int fd, const struct sockaddr* address, socklen_t length);

int __wrap_bind(int fd, const struct sockaddr* address, socklen_t length) {
    if(address && fsim_http_port() != 80) {
        if(address->sa_family == AF_INET && ntohs(((const struct sockaddr_in*)address)->sin_port) == 80) {
            struct sockaddr_in changed = *(const struct sockaddr_in*)address;
            changed.sin_port = htons((uint16_t)fsim_http_port());
            return __real_bind(fd, (const struct sockaddr*)&changed, sizeof(changed));
        }
        if(address->sa_family == AF_INET6 && ntohs(((const struct sockaddr_in6*)address)->sin6_port) == 80) {
            struct sockaddr_in6 changed = *(const struct sockaddr_in6*)address;
            changed.sin6_port = htons((uint16_t)fsim_http_port());
            return __real_bind(fd, (const struct sockaddr*)&changed, sizeof(changed));
        }
    }
    return __real_bind(fd, address, length);
}

int __wrap_connect(int fd, const struct sockaddr* address, socklen_t length) {
    static int allow_host = -1;
    if(address && address->sa_family == AF_INET && fsim_http_port() != 80 && fsim_net_is_loopback(address) &&
       ntohs(((const struct sockaddr_in*)address)->sin_port) == 80) {
        struct sockaddr_in changed = *(const struct sockaddr_in*)address;
        changed.sin_port = htons((uint16_t)fsim_http_port());
        return __real_connect(fd, (const struct sockaddr*)&changed, sizeof(changed));
    }
    if(allow_host < 0) {
        const char* mode = getenv("FSIM_NETWORK");
        allow_host = mode && strcmp(mode, "host") == 0;
    }
    if(!allow_host && address && !fsim_net_is_loopback(address)) {
        errno = ECONNREFUSED;
        return -1;
    }
    return __real_connect(fd, address, length);
}

#endif
