/* fsim: таргетная часть furi_hal.h. Переносимые заголовки f21 копируются при конфигурации CMake
   (FSIM_F21_HEADERS), реализации — в targets/fsim/src. */
#pragma once
/* На баре FuriString приходит транзитивно через таргетные заголовки HAL (log.c на это рассчитывает). */
#include <core/string.h>
#include <furi_hal_nvm.h>
#include <furi_hal_power.h>
#include <furi_hal_sdmmc.h>
#include <furi_hal_random.h>
#include <furi_hal_version.h>
