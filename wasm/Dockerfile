# Оценка FS-3 (WASM): emscripten из Debian + зависимости генератора картинок прошивки.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends emscripten cmake ninja-build python3 nodejs \
        ca-certificates python3-png python3-lz4 python3-colorlog pngquant \
    && rm -rf /var/lib/apt/lists/*
