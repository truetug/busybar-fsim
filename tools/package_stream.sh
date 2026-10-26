#!/bin/sh
# Образ fsim-runtime-stream:<тег> поверх fsim-runtime:<тег> (сначала tools/package_runtime.sh).
# Использование: tools/package_stream.sh [тег]   (по умолчанию dev-current). Прокси для apt — FSIM_BUILD_PROXY.
set -eu
TAG="${1:-dev-current}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
cp "$ROOT/stream/Dockerfile" "$ROOT/stream/server.py" "$ROOT/proxy/netproxy.py" "$CTX/"
cp -R "$ROOT/recorder/net_app" "$CTX/net_app"
cp -R "$ROOT/stream/smoke_app" "$CTX/smoke_app"
PROXY=""
[ -n "${FSIM_BUILD_PROXY:-}" ] && PROXY="--build-arg HTTP_PROXY=$FSIM_BUILD_PROXY --build-arg HTTPS_PROXY=$FSIM_BUILD_PROXY"
SOURCE_LABEL=""
[ -n "${FSIM_IMAGE_SOURCE:-}" ] && SOURCE_LABEL="--label org.opencontainers.image.source=$FSIM_IMAGE_SOURCE"
# shellcheck disable=SC2086
docker build $SOURCE_LABEL --platform linux/amd64 --add-host host.docker.internal:host-gateway $PROXY \
    --build-arg "BASE=fsim-runtime:$TAG" -t "fsim-runtime-stream:$TAG" "$CTX"
