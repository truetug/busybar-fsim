# busybar-firmware-fsim

Хостовая цель прошивки BUSY Bar (`fsim`) — спайк трека FS (`busy-backend-docs/docs/services/firmware-host-target-spike-task.md`).
Исходники прошивки не копируются: CMake берёт их из клона `busy-app/busybar-firmware` (`FW_DIR`, только чтение).
Код здесь — GPLv2+, как прошивка.

```bash
docker build -t fsim-build .
docker run --rm -v "$PWD":/fsim -v "$FW_DIR":/fw:ro fsim-build sh -c 'cmake -S . -B build -G Ninja -DFW_DIR=/fw && cmake --build build && ./build/fsim'
```

## Рантайм-образ и запись сценариев (FS-4)

```sh
tools/package_runtime.sh                 # build23/fsim + data/sd.img → образ fsim-runtime:dev (~57 МБ)
python3 tools/record_scenario.py runtime/example_scenario.json out/   # сценарий → видео, скриншоты, бандл
```

- Образ: `runtime/Dockerfile`, `runtime/entrypoint.sh`. Чистый образ SD разворачивается при старте в tmpfs —
  каждый запуск с нуля, на диске ничего не остаётся. Порты: HTTP API бара `80`, управление `7001`.
- Запуск изолирован: `--read-only`, `--cap-drop=ALL --cap-add=NET_BIND_SERVICE`, `no-new-privileges`,
  лимиты памяти / процессора / процессов. Соединения не к loopback отклоняются (`FSIM_NETWORK=host` — разрешить).
- `tools/frames_to_video.py` — кадры + `frames.log` → mp4 / webm с реальными паузами и ≤ 6 скриншотов.
  Бандл — того же состава, что принимает `decode_video_bundle` каталога.
- Управляющий канал: `switch`, `short|long|press|release`, `wheel`, `wait`, `mark <метка>`.
