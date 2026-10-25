#!/bin/sh
# SD (eMMC) fsim из CI-сборки прошивки — та же раскладка, что делает прошивка
# (storage_ext_sdmmc.c, storage_ext_mk_partititons): MBR, раздел 1 — backup 256 МБ (STORAGE_FIRST_PARTITION_SIZE_MB),
# раздел 2 — ext на остальное. В ext — resources.tar пакета обновления (/ext после обновления), в backup — пакет bkp.
# Использование: tools/make_sd.sh <ветка|версия> [файл образа] [коммит]
# Коммит (короткий хеш из имени артефакта) — брать пакеты именно этой сборки, а не последние в каталоге.
set -eu
BUILD="${1:-dev}"
IMAGE="${2:-data/sd.img}"
COMMIT="${3:-}"
INDEX="https://update.flipperzero.one/builds/busybar-firmware/${BUILD}/"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SECTOR=512
DISK_MB=1024
BKP_MB=256

fetch() {
    NAME="$(curl -fsS "$INDEX" | grep -oE "busybar-f22-$1-[^\"<> ]+\.tgz" | grep -v signed | grep -- "$COMMIT" | head -1)"
    [ -n "$NAME" ] || { echo "no $1 package in $INDEX" >&2; exit 1; }
    curl -fsS -o "$WORK/$1.tgz" "$INDEX$NAME"
    grep " $NAME\$\|  $NAME\$" "$WORK/sha256sum.txt" | awk '{print $1"  '"$WORK"'/'"$1"'.tgz"}' | sha256sum -c - >/dev/null
    echo "$NAME"
}

curl -fsS -o "$WORK/sha256sum.txt" "${INDEX}busybar-f22-sha256sum.txt"
UPDATE="$(fetch update)"
BKP="$(fetch bkp)"
mkdir -p "$WORK/ext" "$WORK/bkp" "$(dirname "$IMAGE")"
tar -xzf "$WORK/update.tgz" -C "$WORK" resources.tar update.json
tar -xf "$WORK/resources.tar" -C "$WORK/ext"
# HTTP API без PIN (решение владельца 25.10): режим доступа «enabled» (ApiAccessEnabled = 1 в
# web_server/http_api/api_root.c) — то же, что «Settings → доступ к API» на баре
mkdir -p "$WORK/ext/apps_data/web_server"
printf '{"access_mode":1}\n' > "$WORK/ext/apps_data/web_server/access.json"
tar -xzf "$WORK/bkp.tgz" -C "$WORK/bkp"

START=2048
BKP_SECTORS=$((BKP_MB * 1024 * 1024 / SECTOR))
EXT_START=$((START + BKP_SECTORS))
EXT_SECTORS=$((DISK_MB * 1024 * 1024 / SECTOR - EXT_START))

rm -f "$IMAGE"
truncate -s "${DISK_MB}M" "$IMAGE"
printf 'label: dos\n%s,%s,c\n%s,%s,c\n' "$START" "$BKP_SECTORS" "$EXT_START" "$EXT_SECTORS" | sfdisk -q "$IMAGE"

part() { # $1 — имя, $2 — секторов, $3 — каталог с содержимым, $4 — стартовый сектор
    truncate -s $(($2 * SECTOR)) "$WORK/$1.img"
    mkfs.fat -F 32 -n "$(echo "$1" | tr a-z A-Z)" "$WORK/$1.img" >/dev/null
    MTOOLS_SKIP_CHECK=1 mcopy -s -i "$WORK/$1.img" "$3/"* ::/
    dd if="$WORK/$1.img" of="$IMAGE" bs=$SECTOR seek="$4" conv=notrunc status=none
}
part bkp "$BKP_SECTORS" "$WORK/bkp" "$START"
part ext "$EXT_SECTORS" "$WORK/ext" "$EXT_START"
cp "$WORK/update.json" "$(dirname "$IMAGE")/update.json"
echo "$UPDATE + $BKP -> $IMAGE ($(find "$WORK/ext" -type f | wc -l) files in /ext)"
