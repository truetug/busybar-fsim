#!/bin/sh
# Старт виртуального бара: чистый образ SD в tmpfs, ручка в APPS, HTTP API на :80.
#   FSIM_RECORD=1        — писать кадры PNG (+ frames.log) в /run/fsim/frames
#   FSIM_NETWORK=host    — разрешить выход в сеть (по умолчанию соединения не к loopback отклоняются)
#   FSIM_INPUT           — сценарий ввода при старте (по умолчанию «switch apps»)
#   управление           — TCP :7001 (команды fsim_control.c: switch, short, wheel, mark …); порт публикуется
#                          только на 127.0.0.1 хоста, внутри контейнера слушает все интерфейсы
#   FSIM_TZ / TZ         — часовой пояс
#   FSIM_RUN_SECONDS     — завершиться через N секунд (потолок для ревью)
set -eu
RUN="${FSIM_RUN_DIR:-/run/fsim}"
mkdir -p "$RUN/frames"
gunzip -c /opt/fsim/sd.img.gz | dd of="$RUN/sd.img" bs=1M conv=sparse status=none
export FSIM_SD_IMAGE="$RUN/sd.img"
export FSIM_INPUT="${FSIM_INPUT:-switch apps}"
export FSIM_CONTROL_PORT="${FSIM_CONTROL_PORT:-7001}" FSIM_CONTROL_HOST="${FSIM_CONTROL_HOST:-0.0.0.0}"
if [ "${FSIM_RECORD:-0}" = "1" ]; then
    export FSIM_FRAMES="$RUN/frames" FSIM_FRAMES_HISTORY=1
fi
exec /opt/fsim/fsim
