/* Подмены сервисов front_display и back_display: тот же API (front_display.h, back_display.h),
   вместо SPI/DMA-развёртки — буфер кадра в памяти и PNG при каждом изменении.
   FSIM_FRAMES=<каталог> — куда писать кадры: front.png / back.png (последний) и, при
   FSIM_FRAMES_HISTORY=1, нумерованные front_000001.png … */
#include <furi.h>
#include <front_display/front_display.h>
#include <back_display/back_display.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include <stb_image_write.h>

#define TAG "FsimDisplay"

void fsim_stream_publish(int display, const uint8_t* data, size_t length);

struct FrontDisplaySrv {
    FuriMutex* mutex;
    uint8_t frame[FRONT_DISPLAY_BUF_SIZE];
    uint32_t number;
    uint32_t draws;
    uint8_t brightness;
    bool blanked;
};

struct BackDisplaySrv {
    FuriMutex* mutex;
    uint8_t frame[BACK_DISPLAY_BUF_SIZE];
    uint32_t number;
    uint32_t draws;
};

static void fsim_display_save(const char* name, uint32_t number, int width, int height, int channels, const uint8_t* pixels) {
    const char* directory = getenv("FSIM_FRAMES");
    if(!directory) {
        return;
    }
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.png", directory, name);
    stbi_write_png(path, width, height, channels, pixels, width * channels);
    if(getenv("FSIM_FRAMES_HISTORY")) {
        /* frames.log: «мс_от_старта экран номер» — таймлайн для сборки видео по реальным паузам */
        snprintf(path, sizeof(path), "%s/frames.log", directory);
        FILE* log = fopen(path, "a");
        if(log) {
            fprintf(log, "%lu %s %lu\n", (unsigned long)furi_get_tick(), name, (unsigned long)number);
            fclose(log);
        }
        snprintf(path, sizeof(path), "%s/%s_%06lu.png", directory, name, (unsigned long)number);
        stbi_write_png(path, width, height, channels, pixels, width * channels);
    }
}

void front_display_draw(FrontDisplaySrv* instance, const uint8_t* buf) {
    furi_check(instance);
    furi_mutex_acquire(instance->mutex, FuriWaitForever);
    instance->draws++;
    if(getenv("FSIM_DRAW_LOG") && (instance->draws <= 3 || instance->draws % 100 == 0)) {
        FURI_LOG_I(TAG, "front draw #%lu", (unsigned long)instance->draws);
    }
    if(instance->number == 0 || memcmp(instance->frame, buf, FRONT_DISPLAY_BUF_SIZE) != 0) {
        memcpy(instance->frame, buf, FRONT_DISPLAY_BUF_SIZE);
        instance->number++;
        /* LVGL RGB888 в памяти — B, G, R */
        uint8_t rgb[FRONT_DISPLAY_BUF_SIZE];
        for(size_t i = 0; i < FRONT_DISPLAY_BUF_SIZE; i += 3) {
            rgb[i] = buf[i + 2];
            rgb[i + 1] = buf[i + 1];
            rgb[i + 2] = buf[i];
        }
        fsim_display_save("front", instance->number, FRONT_DISPLAY_W, FRONT_DISPLAY_H, 3, rgb);
        fsim_stream_publish(0, rgb, FRONT_DISPLAY_W * FRONT_DISPLAY_H * 3);
    }
    furi_mutex_release(instance->mutex);
}

void front_display_set_brightness(FrontDisplaySrv* instance, FrontDisplayBrightness brightness) {
    instance->brightness = brightness.val;
}

void front_display_set_blanked(FrontDisplaySrv* instance, bool is_blanked) {
    instance->blanked = is_blanked;
}

void front_display_sleep_mode(FrontDisplaySrv* instance, bool sleep) {
    UNUSED(instance);
    UNUSED(sleep);
}

void back_display_draw(BackDisplaySrv* instance, const uint8_t* data) {
    furi_check(instance);
    furi_mutex_acquire(instance->mutex, FuriWaitForever);
    instance->draws++;
    if(instance->number == 0 || memcmp(instance->frame, data, BACK_DISPLAY_BUF_SIZE) != 0) {
        memcpy(instance->frame, data, BACK_DISPLAY_BUF_SIZE);
        instance->number++;
        fsim_display_save("back", instance->number, BACK_DISPLAY_W, BACK_DISPLAY_H, 1, data);
        fsim_stream_publish(1, data, BACK_DISPLAY_W * BACK_DISPLAY_H);
    }
    furi_mutex_release(instance->mutex);
}

void back_display_sleep_mode(BackDisplaySrv* instance, bool sleep) {
    UNUSED(instance);
    UNUSED(sleep);
}

void back_display_set_contrast(BackDisplaySrv* instance, BackDisplayContrast contrast) {
    UNUSED(instance);
    UNUSED(contrast);
}

void back_display_set_gamma_table(BackDisplaySrv* instance, const BackDisplayGammaTable* gamma_table) {
    UNUSED(instance);
    UNUSED(gamma_table);
}

size_t back_display_get_width(void) {
    return BACK_DISPLAY_W;
}

size_t back_display_get_height(void) {
    return BACK_DISPLAY_H;
}

int32_t front_display_srv(void* arg) {
    UNUSED(arg);
    FrontDisplaySrv* instance = calloc(1, sizeof(FrontDisplaySrv));
    instance->mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    instance->brightness = 100;
    furi_record_create(RECORD_FRONT_DISPLAY, instance);
    FURI_LOG_I(TAG, "front display %dx%d -> %s", FRONT_DISPLAY_W, FRONT_DISPLAY_H, getenv("FSIM_FRAMES") ? getenv("FSIM_FRAMES") : "(no FSIM_FRAMES)");
    FSIM_SERVICE_PARK();
}

int32_t back_display_srv(void* arg) {
    UNUSED(arg);
    BackDisplaySrv* instance = calloc(1, sizeof(BackDisplaySrv));
    instance->mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    furi_record_create(RECORD_BACK_DISPLAY, instance);
    FSIM_SERVICE_PARK();
}
