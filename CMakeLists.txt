cmake_minimum_required(VERSION 3.20)
project(fsim C ASM)

set(FW_DIR "" CACHE PATH "Клон busy-app/busybar-firmware (только чтение)")
if(NOT FW_DIR)
    message(FATAL_ERROR "Укажите -DFW_DIR=<путь к прошивке>")
endif()

set(CORE ${FW_DIR}/fbt_layers/core_libs/lib)
set(RTOS ${FW_DIR}/fbt_layers/freertos/lib/FreeRTOS-Kernel)
set(TARGET_DIR ${CMAKE_SOURCE_DIR}/targets/fsim)

set(CMAKE_C_STANDARD 23) # как прошивка: gnu23 (разделители разрядов и т.п.)
set(CMAKE_C_EXTENSIONS ON)
# Оптимизация: по умолчанию как раньше; для wasm — -DFSIM_OPT="-O2 -g0"
set(FSIM_OPT "-O0" CACHE STRING "Флаги оптимизации")
separate_arguments(FSIM_OPT_LIST UNIX_COMMAND "${FSIM_OPT}")
add_compile_options(-g ${FSIM_OPT_LIST} -Wall -Wno-unused-parameter -fno-strict-aliasing
                    "$<$<COMPILE_LANGUAGE:C>:SHELL:-include ${TARGET_DIR}/include/fsim_preinclude.h>")
add_compile_definitions(_GNU_SOURCE FURI_DEBUG HAVE_UNISTD_H=1 HAVE_STDARG_H=1)

# Сгенерированные файлы пишем, только если содержимое изменилось: иначе каждая перенастройка
# CMake обновляет их время, и ninja пересобирает всё, что их подключает (~10 мин в эмуляции)
function(fsim_write PATH CONTENT)
    file(WRITE ${PATH}.tmp "${CONTENT}")
    file(COPY_FILE ${PATH}.tmp ${PATH} ONLY_IF_DIFFERENT)
    file(REMOVE ${PATH}.tmp)
endfunction()

# FreeRTOS: ядро + POSIX-порт.
# port.c патчится: yield внутри критической секции откладывается до выхода из неё, как PendSV на
# Cortex-M. Без этого furi_event_flag_set будит ожидающий поток посреди своей критической секции,
# тот освобождает флаг (api_lock хранилища), и дальше идёт запись в освобождённую память.
file(GLOB RTOS_SOURCES ${RTOS}/*.c)
set(RTOS_PORT ${RTOS}/portable/ThirdParty/GCC/Posix/port.c)
file(READ ${RTOS_PORT} PORT_SOURCE)
set(PORT_YIELD_OLD "void vPortYield( void )\n{\n    vPortEnterCritical();")
set(PORT_YIELD_NEW "static volatile BaseType_t xFsimYieldPending = pdFALSE;\n\nvoid vPortYield( void )\n{\n    if( uxCriticalNesting > 0 )\n    {\n        xFsimYieldPending = pdTRUE;\n        return;\n    }\n\n    vPortEnterCritical();")
set(PORT_EXIT_OLD "    if( uxCriticalNesting == 0 )\n    {\n        vPortEnableInterrupts();\n    }\n}")
set(PORT_EXIT_NEW "    if( uxCriticalNesting == 0 )\n    {\n        vPortEnableInterrupts();\n\n        if( xFsimYieldPending != pdFALSE )\n        {\n            xFsimYieldPending = pdFALSE;\n            vPortYield();\n        }\n    }\n}")
foreach(PART YIELD EXIT)
    string(FIND "${PORT_SOURCE}" "${PORT_${PART}_OLD}" PORT_FOUND)
    if(PORT_FOUND EQUAL -1)
        message(FATAL_ERROR "POSIX port.c изменился: не нашли место патча ${PART}")
    endif()
    string(REPLACE "${PORT_${PART}_OLD}" "${PORT_${PART}_NEW}" PORT_SOURCE "${PORT_SOURCE}")
endforeach()
# xFsimYieldPending объявляется перед vPortYield, а vPortExitCritical стоит выше — объявить заранее
string(REPLACE "static volatile portBASE_TYPE uxCriticalNesting;"
    "static volatile portBASE_TYPE uxCriticalNesting;\nstatic volatile BaseType_t xFsimYieldPending;" PORT_SOURCE "${PORT_SOURCE}")
string(REPLACE "static volatile BaseType_t xFsimYieldPending = pdFALSE;\n\nvoid vPortYield" "void vPortYield" PORT_SOURCE "${PORT_SOURCE}")
fsim_write(${CMAKE_BINARY_DIR}/gen/port_patched.c "#line 1 \"${RTOS_PORT}\"\n${PORT_SOURCE}")
list(APPEND RTOS_SOURCES
    ${CMAKE_BINARY_DIR}/gen/port_patched.c
    ${RTOS}/portable/ThirdParty/GCC/Posix/utils/wait_for_event.c)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${RTOS_PORT})

# furi: всё, кроме check.c (ARM-ассемблер) — его заменяет fsim_check.c
file(GLOB FURI_SOURCES ${CORE}/furi/core/*.c ${CORE}/furi/furi.c)
list(REMOVE_DUPLICATES FURI_SOURCES)
list(FILTER FURI_SOURCES EXCLUDE REGEX ".*/core/check\\.c$")

# Переносимые (без STM32) заголовки HAL цели f21 — как есть
set(FSIM_F21_HEADERS furi_hal_sdmmc.h furi_hal_nvm.h furi_hal_power.h furi_hal_random.h
    furi_hal_version.h furi_hal_info.h furi_hal_serial.h furi_hal_serial_types.h
    furi_hal_serial_types_i.h furi_hal_serial_control.h furi_hal_flash_otp.h furi_hal_bits.h)
foreach(HEADER ${FSIM_F21_HEADERS})
    configure_file(${FW_DIR}/targets/f21/furi_hal/${HEADER} ${CMAKE_BINARY_DIR}/gen/include/${HEADER} COPYONLY)
endforeach()

# Сервисы прошивки: applications.c и исходники — генератором из application.fam
set(GEN_DIR ${CMAKE_BINARY_DIR}/gen)
execute_process(
    COMMAND python3 -I ${CMAKE_SOURCE_DIR}/tools/gen_applications.py ${FW_DIR} ${GEN_DIR}
            ${TARGET_DIR}/services.txt
    COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    ${TARGET_DIR}/services.txt ${CMAKE_SOURCE_DIR}/tools/gen_applications.py)
file(READ ${GEN_DIR}/services.json SERVICES_JSON)
string(JSON SERVICE_SOURCE_COUNT LENGTH ${SERVICES_JSON} sources)
set(SERVICE_SOURCES "")
if(SERVICE_SOURCE_COUNT GREATER 0)
    math(EXPR LAST "${SERVICE_SOURCE_COUNT} - 1")
    foreach(I RANGE ${LAST})
        string(JSON SOURCE GET ${SERVICES_JSON} sources ${I})
        list(APPEND SERVICE_SOURCES ${SOURCE})
    endforeach()
endif()
string(JSON SERVICE_DEFINE_COUNT LENGTH ${SERVICES_JSON} defines)
if(SERVICE_DEFINE_COUNT GREATER 0)
    math(EXPR LAST "${SERVICE_DEFINE_COUNT} - 1")
    foreach(I RANGE ${LAST})
        string(JSON DEFINE GET ${SERVICES_JSON} defines ${I})
        add_compile_definitions(${DEFINE})
    endforeach()
endif()

set(FSIM_GIT_HASH "7afbacbd" CACHE STRING "Коммит прошивки (для version)")
set(FSIM_GIT_BRANCH "dev" CACHE STRING "Ветка прошивки (для version)")
add_compile_definitions(FSIM_GIT_HASH="${FSIM_GIT_HASH}" FSIM_GIT_BRANCH="${FSIM_GIT_BRANCH}")

# Картинки, вшитые в прошивку (assets/images/internal) — конвертером прошивки, как lib/internal_assets.scons
set(INTERNAL_DIR ${CMAKE_BINARY_DIR}/gen/internal_assets)
file(MAKE_DIRECTORY ${INTERNAL_DIR}/tmp)
file(GLOB_RECURSE INTERNAL_PNGS ${FW_DIR}/assets/images/internal/*.png)
set(INTERNAL_SOURCES "")
set(INTERNAL_HEADER "#include <lvgl.h>\n\n")
foreach(PNG ${INTERNAL_PNGS})
    get_filename_component(STEM ${PNG} NAME_WE)
    set(OUT ${INTERNAL_DIR}/I_${STEM}.c)
    execute_process(
        COMMAND python3 ${FW_DIR}/scripts/image.py -i ${PNG} -o ${INTERNAL_DIR}/tmp/I_${STEM}.c -f C
        WORKING_DIRECTORY ${FW_DIR}/scripts
        COMMAND_ERROR_IS_FATAL ANY)
    # image.py требует имя на .c — пишем в tmp/, копируем при изменении
    file(COPY_FILE ${INTERNAL_DIR}/tmp/I_${STEM}.c ${OUT} ONLY_IF_DIFFERENT)
    list(APPEND INTERNAL_SOURCES ${OUT})
    string(APPEND INTERNAL_HEADER "extern const lv_image_dsc_t I_${STEM};\n")
endforeach()
fsim_write(${INTERNAL_DIR}/assets_images.h "${INTERNAL_HEADER}")

# Библиотеки прошивки, которые нужны сервисам (растёт по частям FS-2)
file(GLOB FW_LIB_SOURCES
    ${FW_DIR}/lib/toolbox/*.c
    ${FW_DIR}/lib/toolbox/stream/*.c
    ${FW_DIR}/lib/storage_utils/*.c
    ${FW_DIR}/lib/containers/*.c
    ${FW_DIR}/lib/setting_provider/*.c
    ${FW_DIR}/lib/setting_provider/types/*.c
    ${FW_DIR}/lib/js_app/*.c
    ${FW_DIR}/lib/js_app/setting_types/*.c
    ${FW_DIR}/lib/cjson/cJSON.c
    ${FW_DIR}/lib/heatshrink/heatshrink_*.c
    ${FW_DIR}/lib/zlib/*.c
    ${CORE}/utz/*.c
    ${CORE}/datetime/*.c
    ${FW_DIR}/lib/stb/stb.c
    ${FW_DIR}/lib/anim_file/*.c
    ${FW_DIR}/lib/anim_file/components/*.c)
# md5_calc — пока не нужен; profiler — 32-битный формат адресов; dsp — ARM-ассемблер
list(FILTER FW_LIB_SOURCES EXCLUDE REGEX ".*/toolbox/(md5_calc|profiler|dsp)\\.c$")

# LVGL и надстройки прошивки — те же каталоги, что в lib/lvgl.scons и lib/lvgl_addons.scons
set(LVGL ${FW_DIR}/lib/lvgl)
set(LVA ${FW_DIR}/lib/lvgl_addons)
file(GLOB LVGL_SOURCES
    ${LVGL}/src/*.c ${LVGL}/src/core/*.c ${LVGL}/src/display/*.c ${LVGL}/src/draw/*.c
    ${LVGL}/src/draw/sw/*.c ${LVGL}/src/draw/sw/blend/*.c ${LVGL}/src/font/*.c
    ${LVGL}/src/indev/*.c ${LVGL}/src/layouts/*.c ${LVGL}/src/layouts/*/*.c ${LVGL}/src/misc/*.c
    ${LVGL}/src/misc/cache/*.c ${LVGL}/src/stdlib/*.c ${LVGL}/src/stdlib/clib/*.c
    ${LVGL}/src/osal/*.c ${LVGL}/src/others/sysmon/*.c ${LVGL}/src/others/observer/*.c
    ${LVGL}/src/themes/*.c ${LVGL}/src/themes/*/*.c
    ${LVGL}/src/tick/*.c ${LVGL}/src/widgets/*/*.c ${LVGL}/src/libs/lodepng/*.c
    ${LVGL}/src/libs/qrcode/*.c
    ${LVA}/extensions/*.c ${LVA}/fonts/*.c ${LVA}/themes/*.c ${LVA}/fs/*.c ${LVA}/osal/*.c)
# Как lib/lvgl.scons: декодер LVGL-бинарников читает файлы .image (формат ассетов прошивки)
file(READ ${LVGL}/src/libs/bin_decoder/lv_bin_decoder.c BIN_DECODER)
string(REPLACE "\"bin\"" "\"image\"" BIN_DECODER "${BIN_DECODER}")
string(REPLACE "../.." "src" BIN_DECODER "${BIN_DECODER}")
string(REPLACE "\"lv_bin_decoder.h\"" "\"src/libs/bin_decoder/lv_bin_decoder.h\"" BIN_DECODER "${BIN_DECODER}")
fsim_write(${CMAKE_BINARY_DIR}/gen/lv_bin_decoder_patched.c "${BIN_DECODER}")
list(APPEND LVGL_SOURCES ${CMAKE_BINARY_DIR}/gen/lv_bin_decoder_patched.c)
add_compile_definitions(LV_CONF_INCLUDE_SIMPLE LV_LVGL_H_INCLUDE_SIMPLE)

# toolbox/dsp.c: ARM-ассемблер только в dsp_2d_kernel_iteration — подменяем её переносимой версией
file(READ ${FW_DIR}/lib/toolbox/dsp.c DSP_SOURCE)
file(READ ${TARGET_DIR}/src/fsim_dsp_iteration.inc DSP_ITERATION)
string(FIND "${DSP_SOURCE}" "static uint32_t dsp_2d_kernel_iteration(" DSP_BEGIN)
string(FIND "${DSP_SOURCE}" "void dsp_2d_kernel_apply(" DSP_END)
if(DSP_BEGIN EQUAL -1 OR DSP_END EQUAL -1)
    message(FATAL_ERROR "toolbox/dsp.c изменился: не нашли dsp_2d_kernel_iteration / dsp_2d_kernel_apply")
endif()
string(SUBSTRING "${DSP_SOURCE}" 0 ${DSP_BEGIN} DSP_HEAD)
string(SUBSTRING "${DSP_SOURCE}" ${DSP_END} -1 DSP_TAIL)
fsim_write(${CMAKE_BINARY_DIR}/gen/dsp_patched.c "#line 1 \"${FW_DIR}/lib/toolbox/dsp.c\"\n${DSP_HEAD}${DSP_ITERATION}${DSP_TAIL}")
list(APPEND FW_LIB_SOURCES ${CMAKE_BINARY_DIR}/gen/dsp_patched.c)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    ${FW_DIR}/lib/toolbox/dsp.c ${TARGET_DIR}/src/fsim_dsp_iteration.inc)

# mongoose на Linux сам выбрал бы MG_ARCH_UNIX и не подключил mongoose_config.h — а на него
# (и на furi.h в нём) рассчитаны glue прошивки и lib/fetch. CUSTOM → конфиг подключится, и он
# переключает архитектуру на UNIX.
add_compile_definitions(MG_ARCH=0 "MBEDTLS_CONFIG_FILE=\"mbedtls_cfg.h\"")
# Сеть: mongoose прошивки на сокетах хоста (свой mongoose_config.h), glue и DNS из f21 — как есть
set(FSIM_NET_SOURCES
    ${FW_DIR}/lib/xpm/xpm.c
    ${FW_DIR}/lib/mbedtls/library/base64.c
    ${FW_DIR}/lib/mbedtls/library/constant_time.c
    ${FW_DIR}/lib/mbedtls/library/sha256.c
    ${FW_DIR}/lib/mbedtls/library/platform_util.c
    ${FW_DIR}/lib/toolbox/tar/tar_archive.c
    ${FW_DIR}/lib/microtar/src/microtar.c
    ${FW_DIR}/lib/mongoose/mongoose.c
    ${FW_DIR}/targets/f21/mongoose/mongoose_glue.c
    ${FW_DIR}/targets/f21/mongoose/mongoose_dns.c
    ${FW_DIR}/lib/fetch/fetch.c
    ${FW_DIR}/lib/fetch/fetch_loader.c
    ${FW_DIR}/lib/http/http_headers.c
    ${FW_DIR}/lib/http/http_response.c)

# JerryScript: amalgam и glue f21 — как есть; настройки JERRY_* берём из lib/jerryscript.scons
set(JERRY_DIR ${FW_DIR}/lib/jerryscript/amalgam)
# только незакомментированные строки: в scons есть «# "JERRY_NUMBER_TYPE_FLOAT64=0"»
file(STRINGS ${FW_DIR}/lib/jerryscript.scons JERRY_LINES REGEX "^[ \t]*\"JERRY_[A-Z0-9_]+=[0-9]+\"")
string(REGEX MATCHALL "JERRY_[A-Z0-9_]+=[0-9]+" JERRY_DEFINES "${JERRY_LINES}")
if(NOT JERRY_DEFINES)
    message(FATAL_ERROR "lib/jerryscript.scons изменился: не нашли JERRY_*")
endif()
set_source_files_properties(${JERRY_DIR}/jerryscript.c PROPERTIES
    COMPILE_DEFINITIONS "${JERRY_DEFINES}"
    COMPILE_OPTIONS "-w")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${FW_DIR}/lib/jerryscript.scons)
set(FSIM_JS_SOURCES
    ${JERRY_DIR}/jerryscript.c
    ${FW_DIR}/targets/f21/jerryscript/jerryscript_glue.c)

add_executable(fsim
    ${RTOS_SOURCES}
    ${FURI_SOURCES}
    ${CORE}/furi/flipper.c
    ${FW_DIR}/lib/firmware_applications_u5/svc_startup.c
    ${GEN_DIR}/applications.c
    ${SERVICE_SOURCES}
    ${CORE}/fatfs/ff.c
    ${CORE}/fatfs/diskio.c
    ${CORE}/fatfs/ff_gen_drv.c
    ${CORE}/fatfs/option/unicode.c
    ${FW_LIB_SOURCES}
    ${FSIM_NET_SOURCES}
    ${FSIM_JS_SOURCES}
    ${LVGL_SOURCES}
    ${INTERNAL_SOURCES}
    ${FW_DIR}/targets/f21/fatfs/fatfs.c
    ${FW_DIR}/targets/f21/fatfs/user_diskio.c
    ${TARGET_DIR}/src/fsim_sdmmc.c
    ${TARGET_DIR}/src/fsim_display.c
    ${TARGET_DIR}/src/fsim_low_power.c
    ${TARGET_DIR}/src/fsim_power.c
    ${TARGET_DIR}/src/fsim_version.c
    ${TARGET_DIR}/src/main.c
    ${TARGET_DIR}/src/fsim_hal.c
    ${TARGET_DIR}/src/fsim_control.c
    ${TARGET_DIR}/src/fsim_net.c
    ${TARGET_DIR}/src/fsim_peripherals.c
    ${TARGET_DIR}/src/fsim_virtual_apps.c
    ${TARGET_DIR}/src/fsim_web_stubs.c
    ${TARGET_DIR}/src/fsim_check.c
    ${TARGET_DIR}/src/fsim_libc.c
    $<$<NOT:$<BOOL:${EMSCRIPTEN}>>:${TARGET_DIR}/src/fsim_heap.S>)

target_include_directories(fsim PRIVATE
    ${TARGET_DIR}/include
    ${CMAKE_BINARY_DIR}/gen/include
    ${FW_DIR}/fbt_layers/fbtng/targets/furi_hal_include
    ${CORE}/furi
    ${FW_DIR}/lib/firmware_applications_u5
    ${FW_DIR}/targets/f21/fatfs
    ${CORE}/fatfs
    ${FW_DIR}/targets/f21/config
    ${FW_DIR}/applications/services
    ${FW_DIR}/applications/system
    ${FW_DIR}/applications/main
    ${CORE}/mlib
    ${CORE}
    ${FW_DIR}/lib
    ${FW_DIR}/lib/cjson
    ${FW_DIR}/lib/mongoose
    ${CORE}/datetime
    ${CORE}/version
    ${FW_DIR}/lib/microtar/src
    ${FW_DIR}/lib/mbedtls_patch
    ${FW_DIR}/targets/f21/mbedtls
    ${FW_DIR}/lib/mbedtls/include
    ${FW_DIR}/lib/mbedtls/library
    ${FW_DIR}/targets/f21/mongoose
    ${FW_DIR}/lib/http
    ${JERRY_DIR}
    ${LVGL}
    ${LVGL}/src
    ${INTERNAL_DIR}
    ${LVA}
    ${FW_DIR}/lib/stb/stb_repo
    ${FW_DIR}/lib/stb
    ${FW_DIR}/lib/anim_file
    ${FW_DIR}/lib/setting_provider
    ${FW_DIR}/lib/js_app
    ${FW_DIR}/lib/heatshrink
    ${FW_DIR}/lib/zlib
    ${CORE}/utz
    ${RTOS}/include
    ${FW_DIR}/fbt_layers/freertos/lib
    ${FW_DIR}/fbt_layers/freertos/lib/FreeRTOS-glue
    ${RTOS}/portable/ThirdParty/GCC/Posix
    ${RTOS}/portable/ThirdParty/GCC/Posix/utils
    # после FreeRTOS: в toolbox есть свой timers.h (js_app ищет здесь <color.h>)
    ${FW_DIR}/lib/toolbox)

# Без PIE адреса из backtrace() при падении сразу годятся для addr2line (gdb в эмуляции amd64 не работает)
if(EMSCRIPTEN)
    # WASM (оценка FS-3): потоки через Web Workers и SharedArrayBuffer — странице нужны COOP / COEP
    target_compile_options(fsim PRIVATE -pthread)
    target_link_options(fsim PRIVATE -pthread -sPROXY_TO_PTHREAD -sEXIT_RUNTIME=0 -sINITIAL_MEMORY=160MB
        -sSTACK_SIZE=1MB -sERROR_ON_UNDEFINED_SYMBOLS=0 -sWARN_ON_UNDEFINED_SYMBOLS=0 -sEXPORTED_RUNTIME_METHODS=ENV -sPTHREAD_POOL_SIZE=24 -sFORCE_FILESYSTEM=1 -sNO_DISABLE_EXCEPTION_CATCHING=0)
    if(FSIM_WASM_NODERAWFS)  # только для замеров под node: образ SD читается прямо с диска хоста
        target_link_options(fsim PRIVATE -sNODERAWFS=1 -sENVIRONMENT=node)
    endif()
    set_target_properties(fsim PROPERTIES SUFFIX .js)
    target_link_libraries(fsim PRIVATE m)
else()
    set_target_properties(fsim PROPERTIES POSITION_INDEPENDENT_CODE OFF)
    target_link_options(fsim PRIVATE -no-pie -Wl,--wrap=pthread_sigmask -Wl,--wrap=poll -Wl,--wrap=connect)
    target_link_libraries(fsim PRIVATE pthread m)
endif()
# патченный port.c лежит в build — его локальные #include ищем рядом с оригиналом
set_source_files_properties(${CMAKE_BINARY_DIR}/gen/port_patched.c PROPERTIES
    INCLUDE_DIRECTORIES ${RTOS}/portable/ThirdParty/GCC/Posix)
# патченный dsp.c лежит в build — его локальные #include "dsp.h" ищем в lib/toolbox
set_source_files_properties(${CMAKE_BINARY_DIR}/gen/dsp_patched.c PROPERTIES
    INCLUDE_DIRECTORIES ${FW_DIR}/lib/toolbox)
