# Рантайм fsim: бинарник + чистый образ SD. Собирается tools/package_runtime.sh (контекст — во временной папке).
FROM --platform=linux/amd64 debian:trixie-slim
COPY fsim /opt/fsim/fsim
COPY sd.img.gz /opt/fsim/sd.img.gz
COPY entrypoint.sh /opt/fsim/entrypoint.sh
COPY FIRMWARE /opt/fsim/FIRMWARE
# Образ SD разворачивается при старте в /run/fsim (tmpfs): каждый запуск — чистый бар
ENV FSIM_RUN_DIR=/run/fsim
EXPOSE 80 7001
ENTRYPOINT ["/opt/fsim/entrypoint.sh"]
