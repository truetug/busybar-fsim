/* SD-карта fsim — файл-образ (FSIM_SD_IMAGE, по умолчанию data/sd.img), блоки по 512 байт.
   FatFs и связка targets/f21/fatfs работают поверх этого без изменений. */
#include <furi.h>
#include <furi_hal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

#define TAG        "FsimSd"
#define BLOCK_SIZE 512

static FILE* fsim_sd;
static uint32_t fsim_sd_blocks;

static const char* fsim_sd_path(void) {
    const char* path = getenv("FSIM_SD_IMAGE");
    return path ? path : "data/sd.img";
}

void furi_hal_sdmmc_init(bool have_rtos) {
    UNUSED(have_rtos);
}

FuriHalSdError furi_hal_sdmmc_init_card(void) {
    if(fsim_sd) {
        return FuriHalSdErrorNone;
    }
    fsim_sd = fopen(fsim_sd_path(), "r+b");
    if(!fsim_sd) {
        FURI_LOG_E(TAG, "No SD image at %s", fsim_sd_path());
        return FuriHalSdErrorCardAbsent;
    }
    fseek(fsim_sd, 0, SEEK_END);
    fsim_sd_blocks = (uint32_t)(ftell(fsim_sd) / BLOCK_SIZE);
    FURI_LOG_I(TAG, "SD image %s, %lu blocks", fsim_sd_path(), (unsigned long)fsim_sd_blocks);
    return FuriHalSdErrorNone;
}

void furi_hal_sdmmc_deinit_card(void) {
    if(fsim_sd) {
        fclose(fsim_sd);
        fsim_sd = NULL;
    }
}

bool furi_hal_sd_alive(void) {
    return fsim_sd != NULL || access(fsim_sd_path(), R_OK) == 0;
}

FuriHalSdError furi_hal_sdmmc_get_card_info(FuriHalSdInfo* info) {
    if(!fsim_sd) {
        return FuriHalSdErrorCardAbsent;
    }
    memset(info, 0, sizeof(*info));
    info->logical_block_count = fsim_sd_blocks;
    info->logical_block_size = BLOCK_SIZE;
    info->version = FuriHalSdVersion2;
    info->type = FuriHalSdTypeHCXC;
    info->speed = FuriHalSdSpeedHigh;
    strcpy(info->oem_id, "FS");
    strcpy(info->product_name, "FSIM");
    return FuriHalSdErrorNone;
}

FuriHalSdError furi_hal_sdmmc_read_blocks(
    uint8_t* buffer,
    uint32_t address,
    uint32_t count,
    size_t timeout_ms_per_block) {
    UNUSED(timeout_ms_per_block);
    if(!fsim_sd || address + count > fsim_sd_blocks) {
        return FuriHalSdErrorAddrOutOfRange;
    }
    fseek(fsim_sd, (long)address * BLOCK_SIZE, SEEK_SET);
    return fread(buffer, BLOCK_SIZE, count, fsim_sd) == count ? FuriHalSdErrorNone :
                                                                 FuriHalSdErrorOther;
}

FuriHalSdError furi_hal_sdmmc_write_blocks(
    const uint8_t* buffer,
    uint32_t address,
    uint32_t count,
    size_t timeout_ms) {
    UNUSED(timeout_ms);
    if(!fsim_sd || address + count > fsim_sd_blocks) {
        return FuriHalSdErrorAddrOutOfRange;
    }
    fseek(fsim_sd, (long)address * BLOCK_SIZE, SEEK_SET);
    bool written = fwrite(buffer, BLOCK_SIZE, count, fsim_sd) == count;
    fflush(fsim_sd);
    return written ? FuriHalSdErrorNone : FuriHalSdErrorOther;
}
