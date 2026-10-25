# fsim-recorder: HTTP-сервис записи сценариев поверх образа fsim-runtime (бинарник и чистый образ SD уже внутри).
# Собирается tools/package_recorder.sh. Сеть из прошивки закрыта самим fsim; Docker внутри не нужен.
ARG BASE=fsim-runtime:dev-current
FROM --platform=linux/amd64 ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY server.py frames_to_video.py /opt/recorder/
ENV RECORDER_PORT=8080 RECORDER_RUN_DIR=/run/fsim-recorder
EXPOSE 8080
ENTRYPOINT ["python3", "/opt/recorder/server.py"]
