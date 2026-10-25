#!/bin/sh
# Собрать образ fsim-runtime из готовых build23/fsim и data/sd.img.
# Использование: tools/package_runtime.sh [тег]   (по умолчанию fsim-runtime:dev)
# Переменные: FSIM_FIRMWARE (идентификатор прошивки для /v1/health рекордера, по умолчанию — тег), FSIM_BINARY (по умолчанию build23/fsim), FSIM_SD (по умолчанию data/sd.img).
set -eu
TAG="${1:-fsim-runtime:dev}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
cp "${FSIM_BINARY:-$ROOT/build23/fsim}" "$CTX/fsim"
gzip -1 -c "${FSIM_SD:-$ROOT/data/sd.img}" > "$CTX/sd.img.gz"
printf %s "${FSIM_FIRMWARE:-${TAG#*:}}" > "$CTX/FIRMWARE"
cp "$ROOT/runtime/entrypoint.sh" "$ROOT/runtime/Dockerfile" "$CTX/"
SOURCE_LABEL=""
[ -n "${FSIM_IMAGE_SOURCE:-}" ] && SOURCE_LABEL="--label org.opencontainers.image.source=$FSIM_IMAGE_SOURCE"
# shellcheck disable=SC2086
docker build $SOURCE_LABEL --platform linux/amd64 -t "$TAG" "$CTX"
