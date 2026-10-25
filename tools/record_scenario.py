"""Прогнать сценарий на виртуальном баре (fsim) и записать видео и скриншоты.

    record_scenario.py <сценарий.json> <выходная папка> [--image fsim-runtime:dev]

Что делает:
  1. запускает контейнер fsim-runtime (изолированно: без лишних прав, read-only, лимиты памяти,
     процессора и процессов, без выхода в сеть), каталог кадров монтируется с хоста;
  2. ждёт HTTP API бара и, если в сценарии есть пакет, ставит его штатным stage → install;
  3. выполняет шаги тем же API, что у настоящего бара: /api/input, /api/apps/launch;
  4. останавливает контейнер и собирает видео, скриншоты и бандл (frames_to_video.py).

Сценарий (JSON):
  {
    "package": "путь/к/app.tar",          # необязательно: пакет приложения для установки
    "intro_ms": 1500,                      # пауза до первого шага
    "steps": [
      {"key": "ok", "pause_ms": 1200},     # up | down | ok | back | start | busy | custom | off | apps | settings
      {"launch": "app.busy.clock", "pause_ms": 3000},
      {"wait_ms": 2000}
    ],
    "tail_ms": 1500,                       # пауза после последнего шага
    "timezone": "Europe/Moscow",           # необязательно
    "max_seconds": 30                      # потолок записи (по умолчанию 30 — лимит каталога)
  }

Только стандартная библиотека, docker и ffmpeg в PATH.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = {"up", "down", "ok", "back", "start", "busy", "custom", "off", "apps", "settings"}


def call(base: str, method: str, path: str, query: dict | None = None, body: bytes | None = None):
    """Один запрос к API бара. Ответ — разобранный JSON или None для пустого тела."""
    url = f"{base}/api/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path} → HTTP {error.code}: {error.read()[:200]!r}") from None
    return json.loads(data) if data.strip() else None


def wait_for_api(base: str, timeout_s: float = 60):
    """API бара поднимается через несколько секунд после старта контейнера."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            call(base, "GET", "version")
            return
        except (SystemExit, urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    raise SystemExit("API бара не поднялся")


def start_container(image: str, name: str, frames_dir: Path, timezone: str | None, max_seconds: int):
    """Изолированный контейнер: порт API — только на 127.0.0.1 хоста, порт выбирает Docker."""
    command = [
        "docker", "run", "-d", "--name", name, "--platform", "linux/amd64",
        "--read-only", "--tmpfs", "/run/fsim:size=1400m",
        "--cap-drop=ALL", "--cap-add=NET_BIND_SERVICE", "--security-opt", "no-new-privileges",
        "--memory=512m", "--cpus=2", "--pids-limit=256",
        "-v", f"{frames_dir}:/run/fsim/frames",
        "-e", "FSIM_RECORD=1", "-e", f"FSIM_RUN_SECONDS={max_seconds + 60}",
        "-p", "127.0.0.1::80", "-p", "127.0.0.1::7001",
    ]
    if timezone:
        command += ["-e", f"TZ={timezone}"]
    subprocess.run([*command, image], check=True, capture_output=True)
    def published(port: str) -> str:
        mapping = subprocess.run(["docker", "port", name, f"{port}/tcp"], check=True, capture_output=True, text=True)
        return mapping.stdout.strip().splitlines()[0].replace("0.0.0.0", "127.0.0.1")

    return published("80"), published("7001")


def mark(control: tuple[str, int], label: str):
    """Метка на таймлайне записи — по тем же часам fsim, что и кадры."""
    with socket.create_connection(control, timeout=5) as connection:
        connection.sendall(f"mark {label}\n".encode())
        if not connection.recv(16).startswith(b"ok"):
            raise SystemExit(f"fsim не принял метку {label}")


def install_package(base: str, package: Path):
    """stage → install: тот же протокол, которым каталог ставит приложение на настоящий бар."""
    staged = call(base, "POST", "apps/stage", body=package.read_bytes())
    call(base, "POST", "apps/install", {"install_key": staged["install_key"]})
    return staged["staged"]["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--image", default="fsim-runtime:dev")
    args = parser.parse_args()

    scenario = json.loads(args.scenario.read_text())
    steps = scenario.get("steps", [])
    for step in steps:
        if "key" in step and step["key"] not in KEYS:
            sys.exit(f"неизвестная клавиша: {step['key']}")
    max_seconds = int(scenario.get("max_seconds", 30))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="fsim-record-"))
    frames_dir = work / "frames"
    frames_dir.mkdir()
    name = f"fsim-record-{uuid.uuid4().hex[:8]}"
    address, control_address = start_container(
        args.image, name, frames_dir, scenario.get("timezone"), max_seconds
    )
    host, port = control_address.rsplit(":", 1)
    control = (host, int(port))
    base = f"http://{address}"
    try:
        wait_for_api(base)
        if scenario.get("package"):
            print("установлено:", install_package(base, Path(scenario["package"])))
        # Окно записи — между метками start и end на таймлайне fsim
        started = time.monotonic()
        mark(control, "start")
        time.sleep(scenario.get("intro_ms", 1500) / 1000)
        for index, step in enumerate(steps, start=1):
            mark(control, f"step-{index}")
            if "key" in step:
                call(base, "POST", "input", {"key": step["key"]})
            elif "launch" in step:
                call(base, "POST", "apps/launch", {"app_id": step["launch"]})
            time.sleep(step.get("pause_ms", step.get("wait_ms", 1000)) / 1000)
        time.sleep(scenario.get("tail_ms", 1500) / 1000)
        mark(control, "end")
        recorded_s = time.monotonic() - started
        if recorded_s > max_seconds:
            sys.exit(f"сценарий длился {recorded_s:.0f} с — больше лимита {max_seconds} с")
    finally:
        subprocess.run(["docker", "stop", "-t", "2", name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    marks = {}
    timeline = []
    for line in (frames_dir / "frames.log").read_text().splitlines():
        stamp, kind, label = line.split()
        if kind == "mark":
            marks[label] = int(stamp)
            timeline.append({"label": label, "ms": int(stamp)})
    subprocess.run(
        [sys.executable, str(HERE / "frames_to_video.py"), str(frames_dir), str(args.out_dir),
         "--from-ms", str(marks["start"]), "--to-ms", str(marks["end"]), "--hold-ms", "1000",
         "--bundle", str(args.out_dir / "bundle.tar.gz")],
        check=True,
    )
    (args.out_dir / "timeline.json").write_text(json.dumps(
        [{**item, "ms": item["ms"] - marks["start"]} for item in timeline if item["ms"] >= marks["start"]]
    ))


if __name__ == "__main__":
    main()
