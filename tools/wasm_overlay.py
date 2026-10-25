"""Оверлей исходников прошивки для сборки под WASM (emscripten / clang).

    wasm_overlay.py <каталог прошивки> <каталог оверлея>

Прошивка остаётся нетронутой: в оверлее — символьные ссылки на /fw/... (внутри контейнера каталог прошивки
смонтирован как /fw), а несколько файлов подменены исправленными копиями. Правки нужны, потому что прошивка
написана под GCC и использует то, чего нет в clang:

  * вложенные функции GNU C (canvas.c, anim_file.c, anim_file_img.c) — clang их не собирает;
  * трюк с регистром r12 для сообщения о падении (core/check.h) — в wasm регистров нет.

Каждая правка применяется по точному тексту и проверяется: если прошивка изменилась, скрипт падает с
понятной ошибкой, а не собирает молча что-то другое.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

FW, STAGE = Path(sys.argv[1]), Path(sys.argv[2])


def link_tree_with_real_file(relative: str) -> Path:
    """Каталоги вдоль relative — настоящие, всё остальное — ссылки на /fw/…; вернуть путь настоящего файла."""
    parts = relative.split("/")
    current = ""
    STAGE.mkdir(parents=True, exist_ok=True)
    for index, part in enumerate(parts):
        destination = STAGE / current
        for name in os.listdir(FW / current):
            path = destination / name
            if name == part and index < len(parts) - 1:
                if path.is_symlink():  # ссылка от прошлой правки — на пути к файлу нужен настоящий каталог
                    path.unlink()
                path.mkdir(exist_ok=True)
            elif name != part and not os.path.lexists(path):
                os.symlink("/fw/" + str(Path(current) / name), path)
        current = str(Path(current) / part)
    target = STAGE / relative
    if target.is_symlink() or target.exists():
        target.unlink()
    return target


def must_replace(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        sys.exit(f"wasm_overlay: не нашли ровно одно вхождение для «{what}» — прошивка изменилась, правку надо обновить")
    return text.replace(old, new)


def hoist_before_function(text: str, marker: str, hoisted: str, what: str) -> str:
    """Вставить hoisted сразу после закрывающей `}` предыдущей функции — перед функцией со вложенной."""
    position = text.index(marker)
    end_of_previous = text.rfind("\n}\n", 0, position)
    if end_of_previous < 0:
        sys.exit(f"wasm_overlay: не нашли конец предыдущей функции перед «{what}»")
    at = end_of_previous + len("\n}\n")
    return text[:at] + "\n" + hoisted + text[at:]


def patch(relative: str, transform) -> None:
    source = (FW / relative).read_text()
    target = link_tree_with_real_file(relative)
    target.write_text(transform(source))


# ---- core/check.h: трюк с r12 только на ARM / x86_64 ----
def check_h(text: str) -> str:
    r12 = 'register const void* r12 asm("r12") = (void*)message; \\'
    sukima = 'asm volatile("sukima%=:" : : "r"(r12));               \\'
    if text.count(r12) != 2 or text.count(sukima) != 2:
        sys.exit("wasm_overlay: core/check.h изменился")
    text = text.replace(r12, "FSIM_CRASH_PREPARE(message) \\").replace(sukima, "\\")
    helpers = (
        "#if defined(__EMSCRIPTEN__)\n"
        "/* wasm: регистров нет, сообщение о падении не передаётся через r12 */\n"
        "#define FSIM_CRASH_PREPARE(message) (void)(message);\n"
        "#else\n"
        '#define FSIM_CRASH_PREPARE(message) register const void* r12 asm("r12") = (void*)message; '
        'asm volatile("sukima%=:" : : "r"(r12));\n'
        "#endif\n"
    )
    return must_replace(text, "/** Crash system with message. Show message after reboot. */",
                        helpers + "/** Crash system with message. Show message after reboot. */", "check.h helpers")


# ---- canvas.c: компаратор qsort без замыкания ----
def canvas_c(text: str) -> str:
    nested = """    int compare_z_index(const void* untyped_a, const void* untyped_b) {
        CanvasWidget* const* a = untyped_a;
        CanvasWidget* const* b = untyped_b;
        return (*a)->z_index - (*b)->z_index;
    }
"""
    hoisted = """static int compare_z_index(const void* untyped_a, const void* untyped_b) {
    CanvasWidget* const* a = untyped_a;
    CanvasWidget* const* b = untyped_b;
    return (*a)->z_index - (*b)->z_index;
}
"""
    text = hoist_before_function(text, nested, hoisted, "canvas compare_z_index")
    return must_replace(text, nested, "", "canvas nested compare_z_index")


# ---- anim_file.c: колбэк поиска секции — контекст вместо захвата name / section ----
def anim_file_c(text: str) -> str:
    nested = """    void callback(size_t cur_index, const AnimFileSection* cur_section, void* context) {
        UNUSED(cur_index);
        UNUSED(context);
        if(strcmp(cur_section->name, name) == 0) section = cur_section;
    }

    if(!anim_file_load_iterate_sections(header, sections, callback, &section)) return false;
"""
    replacement = """    FsimFindSection find = {.name = name, .section = NULL};
    if(!anim_file_load_iterate_sections(header, sections, fsim_find_section_callback, &find)) return false;
    section = find.section;
"""
    hoisted = """typedef struct {
    const char* name;
    const AnimFileSection* section;
} FsimFindSection;

static void
    fsim_find_section_callback(size_t cur_index, const AnimFileSection* cur_section, void* context) {
    UNUSED(cur_index);
    FsimFindSection* find = context;
    if(strcmp(cur_section->name, find->name) == 0) find->section = cur_section;
}
"""
    text = hoist_before_function(text, nested, hoisted, "anim_file find section")
    return must_replace(text, nested, replacement, "anim_file nested callback")


# ---- anim_file_img.c: чистые функции — наружу, output_pixel — макрос, place_pixels — контекст ----
def anim_file_img_c(text: str) -> str:
    pixel_hash = """    inline size_t pixel_hash(uint32_t pixel) {
        size_t a = (pixel >> 24) & 0xff;
        size_t r = (pixel >> 16) & 0xff;
        size_t g = (pixel >> 8) & 0xff;
        size_t b = pixel & 0xff;
        return ((r * 3) + (g * 5) + (b * 7) + (a * 11)) % 64;
    }
"""
    output_pixel = """    inline void output_pixel(uint32_t pixel) {
        hash_lut[pixel_hash(pixel)] = pixel;
        last_px = pixel;
        if(dest_pixels < max_pixels) dest_buf[dest_pixels] = pixel;
        dest_pixels++;
    }
"""
    wrapping = """    inline uint8_t wrapping_add8(uint8_t old, int8_t diff) {
        return (uint8_t)(((int)old + (int)diff) & 0xff);
    }
"""
    pure = (
        "static inline size_t pixel_hash(uint32_t pixel) {\n"
        "    size_t a = (pixel >> 24) & 0xff;\n    size_t r = (pixel >> 16) & 0xff;\n"
        "    size_t g = (pixel >> 8) & 0xff;\n    size_t b = pixel & 0xff;\n"
        "    return ((r * 3) + (g * 5) + (b * 7) + (a * 11)) % 64;\n}\n\n"
        "static inline uint8_t wrapping_add8(uint8_t old, int8_t diff) {\n"
        "    return (uint8_t)(((int)old + (int)diff) & 0xff);\n}\n"
    )
    macro = (
        "#define output_pixel(value) \\\n    do { \\\n        uint32_t fsim_px = (value); \\\n"
        "        hash_lut[pixel_hash(fsim_px)] = fsim_px; \\\n        last_px = fsim_px; \\\n"
        "        if(dest_pixels < max_pixels) dest_buf[dest_pixels] = fsim_px; \\\n"
        "        dest_pixels++; \\\n    } while(0)\n"
    )
    text = hoist_before_function(text, pixel_hash, pure, "anim_file_img pure helpers")
    text = must_replace(text, pixel_hash, "", "anim_file_img pixel_hash")
    text = must_replace(text, wrapping, "", "anim_file_img wrapping_add8")
    text = must_replace(text, output_pixel, macro, "anim_file_img output_pixel")

    place = """    void place_pixels(AnimFileMaskPixelRange range, void* context) {
        UNUSED(context);

        if(margin_needed) {
            range.y += half_margin;
            range.x_start += half_margin;
            range.x_end += half_margin;
        }

        size_t start_idx = (range.y * effective_w) + range.x_start;
        uint32_t* dst_start = (uint32_t*)(destination->data + (start_idx * sizeof(uint32_t)));

        size_t wanted_pixel_cnt = range.x_end - range.x_start;
        size_t pixel_cnt = MIN(wanted_pixel_cnt, src_pixels_left);
        if(wanted_pixel_cnt > src_pixels_left)
            src_pixels_overrun += (wanted_pixel_cnt - src_pixels_left);

#ifdef ANIM_FILE_SHOW_MASK_INSTEAD_OF_IMAGE
        memset(dst_start, 0xff, wanted_pixel_cnt * ANIM_FILE_OUT_BYTES_PER_PIXEL);
#else
        memcpy(dst_start, src_pixel, pixel_cnt * ANIM_FILE_OUT_BYTES_PER_PIXEL);
#endif
        src_pixel += pixel_cnt;
        src_pixels_left -= pixel_cnt;
    }

    anim_file_mask_iterate(anim, frame_hdr, place_pixels, NULL);
"""
    hoisted = """typedef struct {
    bool margin_needed;
    size_t half_margin;
    size_t effective_w;
    AnimFileBuffer* destination;
    uint32_t* src_pixel;
    size_t src_pixels_left;
    size_t src_pixels_overrun;
} FsimPlacePixels;

static void fsim_place_pixels(AnimFileMaskPixelRange range, void* context) {
    FsimPlacePixels* state = context;

    if(state->margin_needed) {
        range.y += state->half_margin;
        range.x_start += state->half_margin;
        range.x_end += state->half_margin;
    }

    size_t start_idx = (range.y * state->effective_w) + range.x_start;
    uint32_t* dst_start = (uint32_t*)(state->destination->data + (start_idx * sizeof(uint32_t)));

    size_t wanted_pixel_cnt = range.x_end - range.x_start;
    size_t pixel_cnt = MIN(wanted_pixel_cnt, state->src_pixels_left);
    if(wanted_pixel_cnt > state->src_pixels_left)
        state->src_pixels_overrun += (wanted_pixel_cnt - state->src_pixels_left);

#ifdef ANIM_FILE_SHOW_MASK_INSTEAD_OF_IMAGE
    memset(dst_start, 0xff, wanted_pixel_cnt * ANIM_FILE_OUT_BYTES_PER_PIXEL);
#else
    memcpy(dst_start, state->src_pixel, pixel_cnt * ANIM_FILE_OUT_BYTES_PER_PIXEL);
#endif
    state->src_pixel += pixel_cnt;
    state->src_pixels_left -= pixel_cnt;
}
"""
    call = """    FsimPlacePixels place_state = {
        .margin_needed = margin_needed,
        .half_margin = half_margin,
        .effective_w = effective_w,
        .destination = destination,
        .src_pixel = src_pixel,
        .src_pixels_left = src_pixels_left,
        .src_pixels_overrun = 0,
    };
    anim_file_mask_iterate(anim, frame_hdr, fsim_place_pixels, &place_state);
    src_pixels_left = place_state.src_pixels_left;
    src_pixels_overrun = place_state.src_pixels_overrun;
"""
    text = hoist_before_function(text, place, hoisted, "anim_file_img place_pixels")
    return must_replace(text, place, call, "anim_file_img nested place_pixels")


patch("fbt_layers/core_libs/lib/furi/core/check.h", check_h)
patch("applications/services/canvas/canvas.c", canvas_c)
patch("lib/anim_file/anim_file.c", anim_file_c)
patch("lib/anim_file/components/anim_file_img.c", anim_file_img_c)
print("overlay ok:", STAGE)
