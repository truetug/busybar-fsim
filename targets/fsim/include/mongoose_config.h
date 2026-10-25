/* fsim: mongoose на сокетах хоста вместо lwip (на баре — targets/f21/mongoose/mongoose_config.h).
   millis, random и лог — из прошивки (targets/f21/mongoose/mongoose_glue.c), как на баре. */
#pragma once

#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <time.h>
#include <furi.h>

#define MG_LOG_LEVEL            MG_LL_INFO
/* Сюда попадаем через MG_ARCH=MG_ARCH_CUSTOM (CMakeLists.txt), дальше — обычная UNIX-сборка */
#undef MG_ARCH
#define MG_ARCH                 MG_ARCH_UNIX
/* TLS (mbedtls + tls_crypto + ca_storage) — следующим шагом; пока https честно не работает */
#define MG_TLS                  MG_TLS_NONE
#define MG_ENABLE_SOCKET        1
#define MG_ENABLE_TCPIP         0
#define MG_ENABLE_CUSTOM_MILLIS 1
#define MG_ENABLE_CUSTOM_RANDOM 1
#define MG_ENABLE_CUSTOM_LOG    1
#define MG_ENABLE_LOG           1
#define MG_ENABLE_POSIX_FS      0
#define MG_ENABLE_DIRLIST       1
/* poll(), а не epoll: poll обёрнут в fsim_net.c, чтобы ожидание не держало планировщик */
#define MG_ENABLE_EPOLL         0
#define MG_ENABLE_POLL          1

/* На баре 32: web_server кладёт в conn->data свой ConnectionContext (union с указателями на функции)
   и проверяет static_assert(sizeof == MG_DATA_SIZE). На x86_64 указатели вдвое длиннее. */
#define MG_DATA_SIZE 64

#define MG_MAX_RECV_SIZE (256 * 1024)
#define MG_IO_SIZE       1460
