/* Приложения ручки режимов, которых нет в виртуальном баре (решение владельца 25.10):
   Busy, Custom (busy с аргументом BUSY_APP_CUSTOM_MODE) и Settings. Экран — модулем
   status_view прошивки, как у системного приложения message. Back не выходит: на баре из
   этих режимов уходят ручкой; desktop останавливает приложение сигналом выхода. */
#include <furi.h>
#include <gui/gui.h>
#include <gui/modules/status_view.h>
#include <storage/storage.h>
#include <lvgl.h>
#include <lvgl_addons/extensions/lv_label_ext.h>
#include <string.h>

#define FSIM_CUSTOM_MODE "custom" /* BUSY_APP_CUSTOM_MODE из applications/main/busy/busy.h */

typedef struct {
    FuriEventLoop* event_loop;
    Gui* gui;
    lv_obj_t* front_label;
    StatusView* back_status;
} FsimStubApp;

static bool fsim_stub_app_input_callback(const InputEvent* event, void* context) {
    UNUSED(event);
    UNUSED(context);
    return true;
}

static int32_t fsim_stub_app_run(const char* message) {
    FsimStubApp* instance = malloc(sizeof(FsimStubApp));
    instance->event_loop = furi_event_loop_alloc();
    instance->gui = furi_record_open(RECORD_GUI);

    with_gui(instance->gui, {
        GuiLayer* main_layer = gui_get_layer(instance->gui, GuiLayerIdMain);
        gui_layer_add_input_callback(main_layer, fsim_stub_app_input_callback, instance);
        /* Передний экран 72 px: status_view переносит текст (LV_LABEL_LONG_WRAP) и обрезает его.
           Своя надпись с бегущей строкой — как у var_item_list. */
        lv_obj_t* front_root = (lv_obj_t*)gui_layer_get_root_widget(main_layer, GuiDisplayIdFront);
        instance->front_label = lv_label_create(front_root);
        lv_obj_set_style_text_color(instance->front_label, lv_color_white(), LV_PART_MAIN);
        lv_label_set_long_mode(instance->front_label, LV_LABEL_LONG_MODE_SCROLL_CIRCULAR);
        lv_label_ext_set_anim_speed(instance->front_label, 1000); /* px/мин, как LONG_TEXT_ANIM_SPEED_PX_PER_M в submenu.c */
        lv_obj_set_width(instance->front_label, LV_PCT(100));
        lv_label_set_text(instance->front_label, message);
        lv_obj_center(instance->front_label);
        instance->back_status =
            status_view_alloc(gui_layer_get_root_widget(main_layer, GuiDisplayIdBack));
        status_view_set_icon(instance->back_status, SHARED_IMG_PATH("info_back_11x11.image"), false);
        status_view_set_primary_text(instance->back_status, message);
    });

    furi_event_loop_run(instance->event_loop);

    with_gui(instance->gui, {
        GuiLayer* main_layer = gui_get_layer(instance->gui, GuiLayerIdMain);
        gui_layer_remove_input_callback(main_layer, fsim_stub_app_input_callback);
        lv_obj_delete(instance->front_label);
        status_view_free(instance->back_status);
    });
    furi_record_close(RECORD_GUI);
    furi_event_loop_free(instance->event_loop);
    free(instance);
    return 0;
}

int32_t busy_app(void* arg) {
    const bool custom = arg && strcmp(arg, FSIM_CUSTOM_MODE) == 0;
    return fsim_stub_app_run(
        custom ? "Custom not implemented on virtual bar" : "Busy not implemented on virtual bar");
}

int32_t settings_app(void* arg) {
    UNUSED(arg);
    return fsim_stub_app_run("Settings not implemented on virtual bar");
}
