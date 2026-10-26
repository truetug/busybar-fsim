"""Смоук образа fsim-runtime: поднимается ли прошивка и работают ли основные пути.

    smoke_runtime.py <образ> [--report report.json]

Код выхода: 0 — все проверки прошли, 1 — есть падения (подробности в отчёте и на stdout).
Проверки идут по пути настоящего бара: HTTP API, пакет приложения через stage → install, режимы ручки,
запуск JS-приложения, логи прошивки без падений.

Только стандартная библиотека и docker.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import struct
import subprocess
import sys
import tarfile
import time
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_scenario import call, start_container, wait_for_api  # noqa: E402

HERE = Path(__file__).resolve().parent
SMOKE_APP = HERE.parent / "runtime" / "smoke_app" / "app.fsim.smoke"
CRASH_MARKERS = ("fsim: signal", "Kernel is Dead", "furi_crash", "Assertion", "BusFault", "HardFault")


def icon_png(size: int = 8) -> bytes:
    """Крохотный PNG-значок: пакет с иконкой ближе к настоящим приложениям."""
    rows = b"".join(b"\x00" + b"\xff\xff\xff" * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def smoke_package() -> bytes:
    """tar пакета приложения через tarfile (системный tar на macOS добавляет ._*)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(SMOKE_APP.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"{SMOKE_APP.name}/{path.relative_to(SMOKE_APP)}", recursive=False)
        icon = icon_png()
        info = tarfile.TarInfo(f"{SMOKE_APP.name}/appmeta/icon_front_8x8.png")
        info.size = len(icon)
        archive.addfile(info, io.BytesIO(icon))
    return buffer.getvalue()


def frame(base: str, display: int = 0) -> bytes:
    import urllib.request

    with urllib.request.urlopen(f"{base}/api/screen?display={display}", timeout=10) as response:
        return response.read()


def container_logs(name: str) -> str:
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return result.stdout + result.stderr


def container_running(name: str) -> bool:
    result = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
    return result.stdout.strip() == "true"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS" if ok else "FAIL"), name, detail)

    name = f"fsim-smoke-{uuid.uuid4().hex[:8]}"
    frames_dir = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
    address, control_address = start_container(args.image, name, frames_dir, "Europe/Moscow", 120)
    control_host, control_port = control_address.rsplit(":", 1)
    base = f"http://{address}"
    try:
        started = time.monotonic()
        wait_for_api(base)
        version = call(base, "GET", "version")
        check("boot: API отвечает", bool(version and version.get("api_semver")),
              f"api {version and version.get('api_semver')}, {time.monotonic() - started:.1f} с")
        logs = container_logs(name)
        check("boot: «Startup complete» в логе", "Startup complete" in logs)

        names = [app["id"] for app in call(base, "GET", "apps/list")["apps"]]
        check("предустановленный Clock", "app.busy.clock" in names, ", ".join(names))

        staged = call(base, "POST", "apps/stage", body=smoke_package())
        call(base, "POST", "apps/install", {"install_key": staged["install_key"]})
        names = [app["id"] for app in call(base, "GET", "apps/list")["apps"]]
        check("установка пакета (stage → install)", "app.fsim.smoke" in names)

        def control(command: str) -> bool:
            with socket.create_connection((control_host, int(control_port)), timeout=5) as connection:
                connection.sendall(f"{command}\n".encode())
                return connection.recv(16).startswith(b"ok")

        # Часы прошивки: time set / time offset (сценарий Clock показывает смену минуты)
        ok_set = control("time set 12:34:55")
        stamp = call(base, "GET", "time")["timestamp"]
        check("часы: time set 12:34:55", ok_set and "T12:34:5" in stamp, stamp)
        control("time offset 10")
        stamp = call(base, "GET", "time")["timestamp"]
        check("часы: time offset +10 с переводит минуту", "T12:35:0" in stamp, stamp)
        check("часы: плохое время отвергнуто", not control("time set 25:00:00"))

        seen: dict[str, bytes] = {}
        for key in ("busy", "custom", "settings", "off", "apps"):
            call(base, "POST", "input", {"key": key})
            time.sleep(2.5)
            data = frame(base)
            seen[key] = data
            blank = not any(data)
            check(f"режим {key}: контейнер жив, кадр {'есть' if not blank else 'пустой'} ({len(data)} Б)",
                  container_running(name) and data and not blank)
        check("размер кадра один во всех режимах", len({len(item) for item in seen.values()}) == 1)
        check("режимы busy / settings / apps рисуют разное",
              len({seen["busy"], seen["settings"], seen["apps"]}) == 3)

        before = frame(base)
        call(base, "POST", "apps/launch", {"app_id": "app.fsim.smoke"})
        time.sleep(4)
        shown = frame(base)
        check("приложение запущено и рисует через API", shown != before and any(shown))
        logs = container_logs(name)
        check("js_runner выполнил скрипт приложения", "app.fsim.smoke/scripts/main.js" in logs)
        check("в консоли приложения нет ошибок", "app.fsim.smoke:" not in logs.replace("Running script", ""))
        call(base, "POST", "apps/quit")
        time.sleep(1.5)

        logs = container_logs(name)
        crashes = [marker for marker in CRASH_MARKERS if marker in logs]
        check("в логе нет падений", not crashes, ", ".join(crashes))
        check("контейнер жив к концу", container_running(name))
    except BaseException as error:  # noqa: BLE001 — любая ошибка = провал смоука, но отчёт нужен
        check("смоук дошёл до конца", False, f"{type(error).__name__}: {error}")
    finally:
        tail = container_logs(name)[-3000:]
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    failed = [item for item in results if not item["ok"]]
    report = {"image": args.image, "ok": not failed, "checks": results, "log_tail": tail if failed else ""}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n{len(results) - len(failed)}/{len(results)} проверок прошло")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
