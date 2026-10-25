# Сборка и запуск fsim — хостовой цели прошивки BUSY Bar (спайк FS-2). linux/amd64:
# макрос furi_crash кладёт сообщение в регистр r12, а он есть только на x86_64.
FROM --platform=linux/amd64 debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build python3 ca-certificates curl dosfstools mtools fdisk python3-png python3-lz4 python3-colorlog pngquant gdb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /fsim
