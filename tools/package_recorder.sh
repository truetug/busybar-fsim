#!/bin/sh
# Образ fsim-runtime-recorder:<тег> поверх fsim-runtime:<тег> (сначала tools/package_runtime.sh).
# Использование: tools/package_recorder.sh [тег]   (по умолчанию dev-current)
# Прокси для apt внутри сборки — FSIM_BUILD_PROXY (например http://host.docker.internal:10809). Отдельная
# переменная: имя host.docker.internal существует только внутри Docker и сломало бы запросы самого скрипта.
set -eu
TAG="${1:-dev-current}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
cp "$ROOT/recorder/Dockerfile" "$ROOT/recorder/server.py" "$ROOT/tools/frames_to_video.py" "$CTX/"
PROXY=""
[ -n "${FSIM_BUILD_PROXY:-}" ] && PROXY="--build-arg HTTP_PROXY=$FSIM_BUILD_PROXY --build-arg HTTPS_PROXY=$FSIM_BUILD_PROXY"
# shellcheck disable=SC2086
docker build --platform linux/amd64 --add-host host.docker.internal:host-gateway $PROXY \
    --build-arg "BASE=fsim-runtime:$TAG" \
    -t "fsim-runtime-recorder:$TAG" "$CTX"
