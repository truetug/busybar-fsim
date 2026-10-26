"""Проверка контракта fsim-recorder (ТЗ каталога 10.25-9.1) на запущенном образе.

    test_recorder.py [--image fsim-runtime-recorder:dev-current]

Запускает контейнер, прогоняет health, успех (бандл проверяется так же, как decode_video_bundle каталога),
400 package_invalid / scenario_invalid, 422 от прошивки, 429 при втором одновременном запуске.
Код выхода 0 — всё прошло. Только стандартная библиотека и docker.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_runtime import smoke_package  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"
SCENARIO = {"duration_s": 10, "clock": "12:34:55", "intro_delay_ms": 800, "action_pause_ms": 600, "capture_interval_ms": 100,
            "return_command": "back",
            "actions": [{"screen": "s", "label": "ok", "command": "ok", "effect": "e"},
                        {"screen": "s", "label": "back", "command": "back", "effect": "e"}]}


def multipart(parts: dict[str, bytes]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = b""
    for name, data in parts.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}"\r\n\r\n'.encode()
        body += data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def post(base: str, parts: dict[str, bytes], timeout: int = 200):
    body, content_type = multipart(parts)
    request = urllib.request.Request(f"{base}/v1/record", data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def check_bundle(data: bytes, duration_s: int) -> str | None:
    """Те же правила, что у decode_video_bundle каталога; вернуть описание проблемы или None."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if not 3 <= len(members) <= 9 or len(names) != len(members):
            return f"число членов {len(members)}"
        allowed = {"video.mp4", "video.webm", "video.json"} | {f"shot-{i}.png" for i in range(6)}
        if not names <= allowed or not {"video.mp4", "video.webm", "video.json"} <= names:
            return f"состав {sorted(names)}"
        meta = json.loads(archive.extractfile("video.json").read())
        if meta.get("duration_s") != duration_s or not 1 <= meta.get("frame_count", 0) <= 300:
            return f"метаданные {meta.get('duration_s')} / {meta.get('frame_count')}"
        if meta["screenshots"] != [f"shot-{i}.png" for i in range(len(meta["screenshots"]))]:
            return "индекс скриншотов"
        for name in meta["screenshots"]:
            shot = archive.extractfile(name).read()
            if not shot.startswith(PNG) or int.from_bytes(shot[16:24], "big") != (72 << 32 | 16):
                return f"{name} не PNG 72×16"
        if archive.extractfile("video.mp4").read()[4:8] != b"ftyp":
            return "mp4"
        if not archive.extractfile("video.webm").read().startswith(b"\x1aE\xdf\xa3"):
            return "webm"
        if not meta.get("timeline") or meta["timeline"][0]["label"] != "start":
            return "нет таймлайна"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default="fsim-runtime-recorder:dev-current")
    args = parser.parse_args()
    results = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append(ok)
        print("PASS" if ok else "FAIL", name, detail)

    name = f"fsim-recorder-test-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "run", "-d", "--name", name, "--platform", "linux/amd64", "--read-only",
                    "--tmpfs", "/run/fsim-recorder:size=2500m", "--tmpfs", "/tmp", "--cap-drop=ALL",
                    "--cap-add=NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--memory=1g", "--cpus=2",
                    "--pids-limit=256", "-p", "127.0.0.1::8080", args.image], check=True, capture_output=True)
    try:
        port = subprocess.run(["docker", "port", name, "8080/tcp"], check=True, capture_output=True,
                              text=True).stdout.split(":")[-1].strip()
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        health = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/v1/health", timeout=5) as reply:
                    health = json.loads(reply.read())
                    break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        check("health", bool(health and health.get("status") == "ok" and health.get("api_semver")), str(health))

        package = smoke_package()
        scenario = json.dumps(SCENARIO).encode()
        status, _, body = post(base, {"package": b"not a tar", "scenario": scenario})
        check("400 package_invalid", status == 400 and b"package_invalid" in body, f"{status}")
        status, _, body = post(base, {"package": package, "scenario": b'{"duration_s": 99, "actions": []}'})
        check("400 scenario_invalid", status == 400 and b"scenario_invalid" in body, f"{status}")
        status, _, body = post(base, {"package": package, "scenario": json.dumps({**SCENARIO, "actions": [
            {"screen": "s", "label": "l", "command": "explode", "effect": "e"}]}).encode()})
        check("400 scenario_invalid (команда)", status == 400 and b"scenario_invalid" in body, f"{status}")

        status, _, body = post(base, {"package": package, "scenario": json.dumps({**SCENARIO, "clock": "25:61:00"}).encode()})
        check("400 scenario_invalid (clock)", status == 400 and b"scenario_invalid" in body, f"{status}")

        # Пакет с манифестом, но без значимых файлов: прошивка отказывает при stage
        broken = io.BytesIO()
        with tarfile.open(fileobj=broken, mode="w") as archive:
            data = b'{"id":"app.fsim.broken","name":"B"}'
            info = tarfile.TarInfo("app.fsim.broken/appmeta/manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        status, _, body = post(base, {"package": broken.getvalue(), "scenario": scenario})
        check("422 от прошивки", status == 422 and b"error_code" in body, f"{status} {body[:120]!r}")

        # Успех и 429: второй запрос во время первого
        outcome = {}

        def run_first():
            outcome["reply"] = post(base, {"package": package, "scenario": scenario})

        worker = threading.Thread(target=run_first)
        worker.start()
        time.sleep(4)
        status, headers, _ = post(base, {"package": package, "scenario": scenario}, timeout=30)
        check("429 + Retry-After", status == 429 and "Retry-After" in headers, f"{status}")
        worker.join()
        status, headers, data = outcome["reply"]
        check("200 application/gzip", status == 200 and headers.get("Content-Type") == "application/gzip",
              f"{status}, {len(data)} Б")
        check("заголовок X-Fsim-Firmware", bool(headers.get("X-Fsim-Firmware")), headers.get("X-Fsim-Firmware", ""))
        problem = check_bundle(data, 10) if status == 200 else "нет ответа"
        check("бандл по правилам decode_video_bundle", problem is None, problem or "")
        # Чистая прошивка: после прогона приложение, поставленное в прошлый раз, не видно
        status, _, again = post(base, {"package": package, "scenario": scenario})
        check("повторный прогон проходит (чистая прошивка)", status == 200, f"{status}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    print(f"\n{sum(results)}/{len(results)} проверок прошло")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
