#!/bin/sh
# Сборка fsim в WASM (эксперимент FS-3). НЕ рабочий порт: прошивка грузится, но FreeRTOS POSIX-порт даёт
# недетерминированные сбои (см. docs/FS_SPIKE_REPORT.md, раздел FS-3).
#   wasm/build.sh <каталог прошивки> <каталог сборки>
# Образ: docker build -t fsim-wasm-spike wasm   (за прокси: --build-arg HTTP_PROXY=… HTTPS_PROXY=…)
# Запуск под node: node <сборка>/fsim.js FSIM_SD_IMAGE=/путь/sd.img FSIM_RUN_SECONDS=20 …
set -eu
FW="$(cd "$1" && pwd)"
OUT="$2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$OUT/overlay" "$OUT/build"
OUT="$(cd "$OUT" && pwd)"
# Оверлей исходников: вложенные функции GNU C и трюк с r12 заменены (прошивка не меняется)
python3 "$ROOT/tools/wasm_overlay.py" "$FW" "$OUT/overlay"
docker run --rm -v "$ROOT":/fsim:ro -v "$OUT/overlay":/fwstage:ro -v "$FW":/fw:ro -v "$OUT/build":/build -w /fsim \
    fsim-wasm-spike bash -c "emcmake cmake -S . -B /build -G Ninja -DFW_DIR=/fwstage -DFSIM_OPT='-O2 -g0' \
        -DFSIM_WASM_NODERAWFS=ON && cmake --build /build"
ls -la "$OUT/build"/fsim.js "$OUT/build"/fsim.wasm
