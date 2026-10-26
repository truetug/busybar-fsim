# fsim-stream: виртуальный бар в браузере — стрим кадров и ввода с серверного fsim (FS-7). Поверх образа fsim-runtime.
ARG BASE=fsim-runtime:dev-current
FROM --platform=linux/amd64 ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-aiohttp ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY server.py netproxy.py /opt/stream/
COPY net_app /opt/stream/net_app
COPY smoke_app /opt/stream/smoke_app
ENV STREAM_LISTEN_PORT=8080 STREAM_RUN_DIR=/run/fsim-stream
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=6s --start-period=120s --retries=3 \
    CMD ["python3", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python3", "/opt/stream/server.py"]
