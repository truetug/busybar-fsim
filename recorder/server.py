"""fsim-recorder: HTTP-сервис записи сценария на виртуальном баре (контракт — ТЗ каталога 10.25-9.1).

    GET  /v1/health  → {"status":"ok","firmware":"dev-7afbacbd","api_semver":"27.14.0"}
    POST /v1/record  (multipart: package — tar приложения, scenario — JSON MockbarScenario)
                     → 200 application/gzip: бандл для decode_video_bundle каталога
                       (video.mp4, video.webm, video.json с таймлайном, shot-N.png)

Ошибки (JSON {"error","error_code"}): 400 package_invalid | scenario_invalid, 422 — отказ прошивки при
stage / install / launch (error_code прошивки), 429 + Retry-After — занят, 504 — не уложились во время.

Один прогон за раз; каждый прогон — новый процесс fsim с чистым образом SD; сеть из прошивки закрыта
(соединения не к loopback отклоняются самим fsim). Только стандартная библиотека, ffmpeg в PATH.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, "/opt/recorder")
from frames_to_video import build_video  # noqa: E402

FSIM_BINARY = os.environ.get("FSIM_BINARY", "/opt/fsim/fsim")
FSIM_SD_GZ = os.environ.get("FSIM_SD_GZ", "/opt/fsim/sd.img.gz")
try:  # идентификатор сборки (dev-7afbacbd) кладёт в образ tools/package_runtime.sh
    FIRMWARE = Path("/opt/fsim/FIRMWARE").read_text().strip()
except OSError:
    FIRMWARE = os.environ.get("FSIM_FIRMWARE", "unknown")
RUN_ROOT = Path(os.environ.get("RECORDER_RUN_DIR", "/run/fsim-recorder"))
PORT = int(os.environ.get("RECORDER_PORT", "8080"))
TIMEZONE = os.environ.get("TZ", "UTC")
CONTROL_PORT = 7001
API = "http://127.0.0.1:80/api"
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = MAX_PACKAGE_BYTES + 256 * 1024
COMMANDS = {"ok", "back", "start", "up", "down"}
TAIL_MS = 2000  # как в scenario_capture каталога: возврат и ещё 2 с


class RecorderError(Exception):
    """Ошибка с HTTP-статусом и кодом по контракту."""

    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.headers = status, code, headers or {}


# ---------- проверка входных данных ----------

def parse_scenario(text: str) -> dict:
    """MockbarScenario каталога: границы те же, что в app/domain/scenario.py."""
    def bad(message: str):
        raise RecorderError(400, "scenario_invalid", message)

    try:
        raw = json.loads(text)
    except ValueError:
        bad("scenario is not valid JSON")
    if not isinstance(raw, dict):
        bad("scenario must be an object")

    def integer(name: str, default: int | None, low: int, high: int) -> int:
        value = raw.get(name, default)
        if type(value) is not int or not low <= value <= high:
            bad(f"{name} must be an integer {low}..{high}")
        return value

    scenario = {
        "duration_s": integer("duration_s", None, 10, 30),
        "intro_delay_ms": integer("intro_delay_ms", 3000, 0, 5000),
        "action_pause_ms": integer("action_pause_ms", 2000, 0, 3000),
        "return_command": raw.get("return_command", "back"),
    }
    if scenario["return_command"] != "back":
        bad("return_command must be 'back'")
    actions = raw.get("actions")
    if not isinstance(actions, list) or len(actions) > 12:
        bad("actions must be a list of at most 12 items")
    commands = []
    for action in actions:
        command = action.get("command") if isinstance(action, dict) else None
        if command not in COMMANDS:
            bad(f"unknown action command: {command!r}")
        commands.append(command)
    scenario["commands"] = commands
    return scenario


def parse_package(data: bytes) -> str:
    """id приложения из appmeta/manifest.json; пакет должен быть читаемым tar (в т.ч. сжатым)."""
    def bad(message: str):
        raise RecorderError(400, "package_invalid", message)

    if not data:
        bad("package is empty")
    if len(data) > MAX_PACKAGE_BYTES:
        bad("package is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive:
                if member.isfile() and member.name.endswith("appmeta/manifest.json"):
                    manifest = json.loads(archive.extractfile(member).read(65536))
                    app_id = manifest.get("id") if isinstance(manifest, dict) else None
                    if isinstance(app_id, str) and app_id:
                        return app_id
                    bad("manifest has no app id")
    except (tarfile.TarError, ValueError, OSError):
        bad("package is not a readable tar archive")
    bad("package has no appmeta/manifest.json")


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    if not content_type.startswith("multipart/form-data"):
        raise RecorderError(400, "package_invalid", "multipart/form-data expected")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            parts[name] = part.get_payload(decode=True) or b""
    return parts


# ---------- запуск прошивки ----------

class Fsim:
    """Один процесс fsim с чистым образом SD; закрывается всегда, вместе со своей папкой."""

    def __init__(self, record: bool, run_seconds: int):
        RUN_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="run-", dir=RUN_ROOT))
        self.frames = self.dir / "frames"
        self.frames.mkdir()
        self.record, self.run_seconds, self.process = record, run_seconds, None

    def __enter__(self) -> "Fsim":
        # Чистая прошивка: образ SD разворачивается заново, пустые блоки — дырами (sparse)
        subprocess.run(f"gunzip -c {FSIM_SD_GZ} | dd of={self.dir / 'sd.img'} bs=1M conv=sparse status=none",
                       shell=True, check=True)
        env = {
            "PATH": os.environ["PATH"], "TZ": TIMEZONE, "FSIM_SD_IMAGE": str(self.dir / "sd.img"),
            "FSIM_INPUT": "switch apps", "FSIM_CONTROL_PORT": str(CONTROL_PORT), "FSIM_CONTROL_HOST": "127.0.0.1",
            "FSIM_RUN_SECONDS": str(self.run_seconds),
        }
        if self.record:
            env.update(FSIM_FRAMES=str(self.frames), FSIM_FRAMES_HISTORY="1")
        self.log = open(self.dir / "fsim.log", "wb")
        self.process = subprocess.Popen([FSIM_BINARY], env=env, cwd=self.dir, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        return self

    def __exit__(self, *exc):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.log.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def wait_api(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("fsim exited during boot")
            try:
                self.call("GET", "version")
                return
            except (urllib.error.URLError, ConnectionError, OSError, RecorderError):
                time.sleep(0.4)
        raise RecorderError(504, "timeout", "fsim did not start in time")

    def call(self, method: str, path: str, query: dict | None = None, body: bytes | None = None):
        url = f"{API}/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method), timeout=30) as reply:
                data = reply.read()
        except urllib.error.HTTPError as error:
            detail = error.read()
            try:
                payload = json.loads(detail)
            except ValueError:
                payload = {}
            raise RecorderError(422, str(payload.get("error_code") or "firmware_error"),
                                str(payload.get("error") or f"firmware answered HTTP {error.code}")) from None
        return json.loads(data) if data.strip() else None

    def mark(self, label: str) -> None:
        """Метка на таймлайне записи — по тем же часам, что и кадры."""
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=5) as connection:
            connection.sendall(f"mark {label}\n".encode())
            if not connection.recv(16).startswith(b"ok"):
                raise RuntimeError(f"fsim rejected mark {label}")


def record(package: bytes, app_id: str, scenario: dict) -> tuple[bytes, str]:
    """Весь прогон: чистая прошивка → установка → запуск → окно записи → видео. Возвращает бандл и api_semver."""
    started = time.monotonic()
    deadline = started + scenario["duration_s"] + 75  # загрузка в эмуляции ~10 с, установка, кодирование

    def pause(milliseconds: int) -> None:
        if time.monotonic() + milliseconds / 1000 > deadline:
            raise RecorderError(504, "timeout", "scenario exceeded the time limit")
        time.sleep(milliseconds / 1000)

    with Fsim(record=True, run_seconds=scenario["duration_s"] + 90) as fsim:
        fsim.wait_api(deadline)
        api_semver = fsim.call("GET", "version")["api_semver"]
        staged = fsim.call("POST", "apps/stage", body=package)
        fsim.call("POST", "apps/install", {"install_key": staged["install_key"]})
        fsim.call("POST", "apps/launch", {"app_id": app_id})
        fsim.mark("start")
        window = time.monotonic()
        pause(scenario["intro_delay_ms"])
        for index, command in enumerate(scenario["commands"], start=1):
            fsim.mark(f"step-{index}")
            fsim.call("POST", "input", {"key": command})
            pause(scenario["action_pause_ms"])
        fsim.mark("return")
        fsim.call("POST", "input", {"key": scenario["return_command"]})
        pause(TAIL_MS)
        # Добор до duration_s: видео ровно той длительности, что заказал сценарий
        remaining_ms = round(scenario["duration_s"] * 1000 - (time.monotonic() - window) * 1000)
        if remaining_ms > 0:
            pause(remaining_ms)
        fsim.mark("end")
        marks = {}
        timeline = []
        for line in (fsim.frames / "frames.log").read_text().splitlines():
            stamp, kind, label = line.split()
            if kind == "mark":
                marks[label] = int(stamp)
                timeline.append({"label": label, "ms": int(stamp)})
        begin = marks["start"]
        timeline = [{**item, "ms": item["ms"] - begin} for item in timeline if item["ms"] >= begin]
        out = fsim.dir / "video"
        bundle = fsim.dir / "bundle.tar.gz"
        try:
            build_video(fsim.frames, out, from_ms=begin, to_ms=marks["end"], hold_ms=1000, bundle=bundle,
                        duration_s=scenario["duration_s"], extra={"timeline": timeline, "firmware": FIRMWARE})
        except ValueError as error:
            raise RuntimeError(f"no frames recorded: {error}") from None
        return bundle.read_bytes(), api_semver


# ---------- HTTP ----------

LOCK = threading.Lock()
STATE = {"ready": False, "api_semver": None, "error": None}


def self_check() -> None:
    """Один пробный запуск при старте: образ рабочий, известен api_semver (его отдаёт /v1/health)."""
    with LOCK:
        try:
            with Fsim(record=False, run_seconds=120) as fsim:
                fsim.wait_api(time.monotonic() + 90)
                STATE["api_semver"] = fsim.call("GET", "version")["api_semver"]
            STATE["ready"] = True
        except Exception as error:  # noqa: BLE001 — недоступность образа видна в health
            STATE["error"] = f"{type(error).__name__}: {error}"


class Handler(BaseHTTPRequestHandler):
    server_version = "fsim-recorder"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def reply(self, status: int, body: bytes, content_type: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def error(self, error: RecorderError):
        body = json.dumps({"error": str(error), "error_code": error.code}).encode()
        self.reply(error.status, body, "application/json", error.headers)

    def do_GET(self):
        if self.path != "/v1/health":
            return self.error(RecorderError(404, "not_found", "no such endpoint"))
        if not STATE["ready"]:
            return self.reply(503, json.dumps({"status": "starting", "error": STATE["error"]}).encode(),
                              "application/json")
        body = {"status": "ok", "firmware": FIRMWARE, "api_semver": STATE["api_semver"]}
        self.reply(200, json.dumps(body).encode(), "application/json")

    def do_POST(self):
        if self.path != "/v1/record":
            return self.error(RecorderError(404, "not_found", "no such endpoint"))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise RecorderError(400, "package_invalid", "request body is empty or too large")
            parts = parse_multipart(self.headers.get("Content-Type", ""), self.rfile.read(length))
            scenario = parse_scenario(parts.get("scenario", b"").decode("utf-8", "replace"))
            package = parts.get("package", b"")
            app_id = parse_package(package)
            if not LOCK.acquire(blocking=False):
                raise RecorderError(429, "busy", "another recording is in progress", {"Retry-After": "30"})
            try:
                bundle, api_semver = record(package, app_id, scenario)
            finally:
                LOCK.release()
            self.reply(200, bundle, "application/gzip",
                       {"X-Fsim-Firmware": FIRMWARE, "X-Fsim-Api-Semver": api_semver})
        except RecorderError as error:
            self.error(error)
        except Exception as error:  # noqa: BLE001
            self.error(RecorderError(500, "internal_error", f"{type(error).__name__}: {error}"))


def main() -> None:
    threading.Thread(target=self_check, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
