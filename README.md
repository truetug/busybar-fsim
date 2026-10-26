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
- `fsim-runtime-stream` (`stream/server.py`, FS-7): виртуальный бар в браузере — сессия = один процесс fsim;
  WebSocket (кадры по изменению ← → ввод), прокси HTTP API бара сессии, лимиты и метрики. Проверка: `tools/test_stream.py`.
- Сеть стенда (FS-8, `proxy/netproxy.py`): поле `network` (`allow`, `mocks`) в сценарии рекордера и в теле сессии стрима;
  DNS-ответчик + HTTP-прокси с белым списком, моками (`delay_ms`) и логом (`network_log` в `video.json`, `GET /v1/sessions/{id}/network`).
  Наружу ходит только прокси. Проверка: `tools/test_network.py`.
- Управляющий канал: `switch`, `short|long|press|release`, `wheel`, `wait`, `mark <метка>`.

## Лицензия

GPL-2.0-or-later (текст — `LICENSE`). fsim собирается из исходников прошивки BUSY Bar
(`busy-app/busybar-firmware`, GPLv2+) и распространяется на тех же условиях. Образы
`ghcr.io/truetug/fsim-runtime` и `fsim-runtime-recorder` — производные от прошивки; исходный код образов —
этот репозиторий, исходники прошивки — upstream по коммиту из тега образа (`dev-<коммит>`, `release-<версия>`).
