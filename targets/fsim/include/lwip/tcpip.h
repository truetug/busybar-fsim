/* fsim: lwip нет. web_server/api_root.c сверяет локальный адрес соединения с адресом netif
   (Wi-Fi или USB). network_find_netif в fsim всегда NULL, поэтому сравнение не выполняется:
   соединение — «не USB», доступ как у бара по Wi-Fi (режим доступа + токен), 127.0.0.1 открыт. */
#pragma once
#include <stdint.h>

typedef struct {
    uint32_t addr;
} ip4_addr_t;

#define LOCK_TCPIP_CORE()
#define UNLOCK_TCPIP_CORE()
#define netif_ip4_addr(netif) ((const ip4_addr_t*)0)
