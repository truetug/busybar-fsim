/* fsim: заменяет applications/services/network/network.h — тот тянет lwip/netif.h.
   API тот же; интерфейсов lwip на хосте нет, сеть — сокеты хоста (fsim_net.c). */
#pragma once

#define RECORD_NETWORK "network"

typedef enum {
    NetworkNetifWifi,
    NetworkNetifUsb,
    NetworkNetifCount,
} NetworkNetif;

#ifdef __cplusplus
extern "C" {
#endif

struct netif;

struct netif* network_find_netif(NetworkNetif netif);

void network_netif_assign_name(struct netif* netif, NetworkNetif id);

typedef struct Network Network;

void network_init_current_thread(Network* instance);

void network_deinit_current_thread(Network* instance);

#ifdef __cplusplus
}
#endif
