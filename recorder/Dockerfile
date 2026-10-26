# fsim-recorder: HTTP-сервис записи сценариев поверх образа fsim-runtime (бинарник и чистый образ SD уже внутри).
# Собирается tools/package_recorder.sh. Сеть из прошивки закрыта самим fsim; Docker внутри не нужен.
ARG BASE=fsim-runtime:dev-current
FROM --platform=linux/amd64 ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-aiohttp ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY server.py frames_to_video.py netproxy.py /opt/recorder/
COPY net_app /opt/recorder/net_app
ENV RECORDER_PORT=8080 RECORDER_RUN_DIR=/run/fsim-recorder
EXPOSE 8080
# Проверка без curl (его в образе нет): /v1/health отвечает 200 только после самопроверки прошивки при старте,
# до этого 503 / отказ соединения. start-period — самопроверка в эмуляции amd64 идёт до ~1 минуты.
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 \
    CMD ["python3", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python3", "/opt/recorder/server.py"]
