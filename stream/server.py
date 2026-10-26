"""fsim-stream: виртуальный бар в браузере — стрим кадров и ввода с серверного fsim (FS-7).

Сессия = один зритель = процесс fsim с чистой прошивкой (образ SD разворачивается заново), сеть наружу закрыта.

    POST   /v1/sessions            (Authorization: Bearer <внутренний токен>, тело необязательно:
                                    {"timezone": "Europe/Moscow", "clock": "12:34:55"})
           → 201 {"id","ws_url","api_url","token","expires_at","limits"} | 429 + Retry-After (мест нет)
    DELETE /v1/sessions/{id}       (внутренний токен) — завершить досрочно
    GET    /v1/sessions/{id}/ws    WebSocket, ?token=<токен сессии>
    ANY    /v1/sessions/{id}/bar/{token}/api/...   прокси HTTP API бара сессии (apps/stage, install, launch …)
    GET    /v1/health, GET /metrics

WebSocket. Сервер → клиент:
  * текст JSON: {"type":"hello", ...} при подключении, {"type":"switch","position":"apps"} при смене ручки,
    {"type":"closed","reason":"idle"} перед закрытием;
  * двоичные кадры: байт 0 — экран (0 передний 72×16 rgb24, 1 задний 160×80 L8), байты 1–4 — uint32 BE метка мс
    от старта fsim, дальше кадр, сжатый zlib (deflate). Кадры шлются по изменению; при подключении — последние.
Клиент → сервер: текст JSON {"cmd": "short ok"} (switch / short / long / press / release / wheel, как канал управления).

Лимиты и настройки — переменные окружения STREAM_* (см. Settings).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import struct
import tempfile
import time
import uuid
import zlib
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

FSIM_BINARY = os.environ.get("FSIM_BINARY", "/opt/fsim/fsim")
FSIM_SD_GZ = os.environ.get("FSIM_SD_GZ", "/opt/fsim/sd.img.gz")
try:
    FIRMWARE = Path("/opt/fsim/FIRMWARE").read_text().strip()
except OSError:
    FIRMWARE = os.environ.get("FSIM_FIRMWARE", "unknown")


class Settings:
    """Настройки сервиса из окружения."""

    internal_token = os.environ.get("STREAM_INTERNAL_TOKEN", "")
    max_sessions = int(os.environ.get("STREAM_MAX_SESSIONS", "4"))
    idle_s = int(os.environ.get("STREAM_IDLE_S", "300"))
    max_s = int(os.environ.get("STREAM_MAX_S", "1800"))
    grace_s = int(os.environ.get("STREAM_RECONNECT_GRACE_S", "60"))
    public_url = os.environ.get("STREAM_PUBLIC_URL", "").rstrip("/")
    allowed_origins = [o.strip() for o in os.environ.get("STREAM_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    run_dir = Path(os.environ.get("STREAM_RUN_DIR", "/run/fsim-stream"))
    port = int(os.environ.get("STREAM_LISTEN_PORT", "8080"))
    boot_timeout_s = int(os.environ.get("STREAM_BOOT_TIMEOUT_S", "90"))
    max_upload_bytes = 32 * 1024 * 1024


SETTINGS = Settings()
KEYS = {"up", "down", "left", "right", "ok", "back", "start"}
POSITIONS = {"busy", "custom", "off", "apps", "settings"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length", "access-control-allow-origin",
              "access-control-allow-methods", "access-control-allow-headers"}


# ---------- метрики ----------

class Histogram:
    def __init__(self, name: str, help_text: str, buckets: list[float]):
        self.name, self.help, self.buckets = name, help_text, buckets
        self.counts = [0] * len(buckets)
        self.total, self.sum = 0, 0.0

    def observe(self, value: float) -> None:
        self.total += 1
        self.sum += value
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for bound, count in zip(self.buckets, self.counts):
            lines.append(f'{self.name}_bucket{{le="{bound}"}} {count}')
        lines += [f'{self.name}_bucket{{le="+Inf"}} {self.total}', f"{self.name}_sum {self.sum:.6f}",
                  f"{self.name}_count {self.total}"]
        return lines


class Metrics:
    started = 0
    rejected: dict[str, int] = {}
    ended: dict[str, int] = {}
    frame_bytes = 0
    commands = 0
    start_seconds = Histogram("fsim_stream_session_start_seconds", "От запроса до готового API бара",
                              [2, 4, 6, 8, 10, 15, 20, 30, 60])
    input_to_frame = Histogram("fsim_stream_input_to_frame_seconds", "Ввод → первый новый кадр",
                               [0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2])


def render_metrics(active: int) -> str:
    lines = [
        "# TYPE fsim_stream_sessions_active gauge", f"fsim_stream_sessions_active {active}",
        "# TYPE fsim_stream_sessions_started_total counter", f"fsim_stream_sessions_started_total {Metrics.started}",
        "# TYPE fsim_stream_sessions_rejected_total counter",
        *[f'fsim_stream_sessions_rejected_total{{reason="{k}"}} {v}' for k, v in sorted(Metrics.rejected.items())],
        "# TYPE fsim_stream_sessions_ended_total counter",
        *[f'fsim_stream_sessions_ended_total{{reason="{k}"}} {v}' for k, v in sorted(Metrics.ended.items())],
        "# TYPE fsim_stream_frame_bytes_total counter", f"fsim_stream_frame_bytes_total {Metrics.frame_bytes}",
        "# TYPE fsim_stream_commands_total counter", f"fsim_stream_commands_total {Metrics.commands}",
        *Metrics.start_seconds.render(), *Metrics.input_to_frame.render(),
    ]
    return "\n".join(lines) + "\n"


# ---------- сессия ----------

def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Session:
    """Один процесс fsim и всё, что с ним связано: порты, канал управления, поток кадров, зрители."""

    def __init__(self, timezone: str | None, clock: str | None):
        self.id = uuid.uuid4().hex[:16]
        self.token = secrets.token_urlsafe(24)
        self.timezone, self.clock = timezone, clock
        self.created = time.monotonic()
        self.created_wall = time.time()
        self.last_activity = self.created
        self.disconnected_at: float | None = self.created
        self.http_port, self.control_port, self.stream_port = free_port(), free_port(), free_port()
        self.dir = Path(tempfile.mkdtemp(prefix="s-", dir=SETTINGS.run_dir))
        self.process: asyncio.subprocess.Process | None = None
        self.ws: web.WebSocketResponse | None = None
        self.control_lock = asyncio.Lock()
        self.frames: dict[int, bytes] = {}  # последние кадры экранов — для нового зрителя
        self.position = "apps"
        self.input_at: float | None = None
        self.tasks: list[asyncio.Task] = []
        self.closing = False

    # -- запуск --
    async def start(self) -> None:
        SETTINGS.run_dir.mkdir(parents=True, exist_ok=True)
        unpack = await asyncio.create_subprocess_shell(
            f"gunzip -c {FSIM_SD_GZ} | dd of={self.dir / 'sd.img'} bs=1M conv=sparse status=none")
        if await unpack.wait() != 0:
            raise RuntimeError("cannot unpack the SD image")
        env = {
            "PATH": os.environ["PATH"], "TZ": self.timezone or os.environ.get("TZ", "UTC"),
            "FSIM_SD_IMAGE": str(self.dir / "sd.img"), "FSIM_INPUT": "switch apps",
            "FSIM_HTTP_PORT": str(self.http_port),
            "FSIM_CONTROL_PORT": str(self.control_port), "FSIM_CONTROL_HOST": "127.0.0.1",
            "FSIM_STREAM_PORT": str(self.stream_port), "FSIM_STREAM_HOST": "127.0.0.1",
            "FSIM_RUN_SECONDS": str(SETTINGS.max_s + 120),
        }
        log = open(self.dir / "fsim.log", "wb")
        self.process = await asyncio.create_subprocess_exec(
            FSIM_BINARY, env=env, cwd=self.dir, stdout=log, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        log.close()
        deadline = time.monotonic() + SETTINGS.boot_timeout_s
        async with ClientSession(timeout=ClientTimeout(total=3)) as client:
            while time.monotonic() < deadline:
                if self.process.returncode is not None:
                    raise RuntimeError("fsim exited during boot")
                try:
                    async with client.get(f"http://127.0.0.1:{self.http_port}/api/version") as reply:
                        if reply.status == 200:
                            break
                except Exception:  # noqa: BLE001 — API ещё не поднялся
                    await asyncio.sleep(0.3)
            else:
                raise TimeoutError("fsim did not start in time")
        if self.clock:
            await self.control(f"time set {self.clock}")
        self.tasks.append(asyncio.create_task(self.read_frames()))

    # -- канал управления --
    async def control(self, command: str) -> bool:
        async with self.control_lock:
            reader, writer = await asyncio.open_connection("127.0.0.1", self.control_port)
            try:
                writer.write(f"{command}\n".encode())
                await writer.drain()
                return (await asyncio.wait_for(reader.read(16), 5)).startswith(b"ok")
            finally:
                writer.close()

    async def handle_command(self, text: str) -> bool:
        """Ввод зрителя: только безопасная часть грамматики канала управления."""
        parts = text.split()
        ok = (
            (len(parts) == 2 and parts[0] == "switch" and parts[1] in POSITIONS)
            or (len(parts) == 2 and parts[0] in ("short", "long", "press", "release") and parts[1] in KEYS)
            or (len(parts) == 2 and parts[0] == "wheel" and re.fullmatch(r"-?\d{1,2}", parts[1]) is not None)
        )
        if not ok:
            return False
        self.last_activity = time.monotonic()
        self.input_at = self.last_activity
        Metrics.commands += 1
        if parts[0] == "switch":
            self.position = parts[1]
            await self.send_json({"type": "switch", "position": self.position})
        return await self.control(text)

    # -- кадры --
    async def read_frames(self) -> None:
        """Поток кадров fsim → последние кадры в памяти → текущему зрителю (сжатыми)."""
        reader = None
        for _ in range(100):
            try:
                reader, _writer = await asyncio.open_connection("127.0.0.1", self.stream_port)
                break
            except OSError:
                await asyncio.sleep(0.1)
        if reader is None:
            return
        try:
            while True:
                header = await reader.readexactly(9)
                display, tick, length = struct.unpack(">BII", header)
                frame = await reader.readexactly(length)
                message = struct.pack(">BI", display, tick) + zlib.compress(frame, 1)
                self.frames[display] = message
                if self.input_at is not None:
                    Metrics.input_to_frame.observe(time.monotonic() - self.input_at)
                    self.input_at = None
                await self.send_bytes(message)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def send_bytes(self, data: bytes) -> None:
        if self.ws is not None and not self.ws.closed:
            Metrics.frame_bytes += len(data)
            try:
                await self.ws.send_bytes(data)
            except ConnectionError:
                pass

    async def send_json(self, payload: dict) -> None:
        if self.ws is not None and not self.ws.closed:
            try:
                await self.ws.send_str(json.dumps(payload))
            except ConnectionError:
                pass

    # -- завершение --
    async def stop(self, reason: str) -> None:
        if self.closing:
            return
        self.closing = True
        Metrics.ended[reason] = Metrics.ended.get(reason, 0) + 1
        if self.ws is not None and not self.ws.closed:
            await self.send_json({"type": "closed", "reason": reason})
            await self.ws.close(code=4000 + (1 if reason == "idle" else 2 if reason == "max_lifetime" else 0),
                                message=reason.encode())
        for task in self.tasks:
            task.cancel()
        if self.process and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                await asyncio.wait_for(self.process.wait(), 3)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        shutil.rmtree(self.dir, ignore_errors=True)


# ---------- сервис ----------

class App:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.starting = 0
        self.api_semver: str | None = None
        self.ready = False
        self.error: str | None = None
        self.client: ClientSession | None = None

    def capacity_used(self) -> int:
        return len(self.sessions) + self.starting

    async def reaper(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                reason = None
                if now - session.created > SETTINGS.max_s:
                    reason = "max_lifetime"
                elif now - session.last_activity > SETTINGS.idle_s:
                    reason = "idle"
                elif session.process and session.process.returncode is not None:
                    reason = "crashed"
                elif session.ws is None and now - max(session.disconnected_at or 0, session.last_activity) > SETTINGS.grace_s:
                    reason = "disconnected"
                if reason:
                    self.sessions.pop(session.id, None)
                    await session.stop(reason)

    async def self_check(self) -> None:
        """Пробный запуск при старте: образ рабочий, известен api_semver для /v1/health."""
        self.starting += 1
        session = Session(None, None)
        try:
            await session.start()
            async with self.client.get(f"http://127.0.0.1:{session.http_port}/api/version") as reply:
                self.api_semver = (await reply.json(content_type=None))["api_semver"]
            self.ready = True
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
        finally:
            self.starting -= 1
            Metrics.ended.pop("self_check", None)
            await session.stop("self_check")
            Metrics.ended.pop("self_check", None)


# ---------- HTTP-обработчики ----------

def origin_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin")
    if origin and ("*" in SETTINGS.allowed_origins or origin in SETTINGS.allowed_origins):
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*"}
    return {}


def json_error(status: int, code: str, message: str, headers: dict | None = None) -> web.Response:
    return web.json_response({"error": message, "error_code": code}, status=status, headers=headers)


def internal_auth(request: web.Request) -> bool:
    expected = SETTINGS.internal_token
    given = request.headers.get("Authorization", "")
    return bool(expected) and given.startswith("Bearer ") and hmac.compare_digest(given[7:], expected)


def base_url(request: web.Request) -> str:
    if SETTINGS.public_url:
        return SETTINGS.public_url
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{scheme}://{request.headers.get('X-Forwarded-Host', request.host)}"


async def health(request: web.Request) -> web.Response:
    app: App = request.app["state"]
    if not app.ready:
        return web.json_response({"status": "starting", "error": app.error}, status=503)
    return web.json_response({"status": "ok", "firmware": FIRMWARE, "api_semver": app.api_semver,
                              "sessions": len(app.sessions), "max_sessions": SETTINGS.max_sessions})


async def metrics(request: web.Request) -> web.Response:
    app: App = request.app["state"]
    return web.Response(text=render_metrics(len(app.sessions)), content_type="text/plain")


async def create_session(request: web.Request) -> web.Response:
    app: App = request.app["state"]
    if not internal_auth(request):
        return json_error(401, "unauthorized", "internal token required")
    if not app.ready:
        return json_error(503, "starting", "service is starting", {"Retry-After": "10"})
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        return json_error(400, "session_invalid", "body must be JSON")
    timezone, clock = body.get("timezone"), body.get("clock")
    if timezone is not None and not (isinstance(timezone, str) and re.fullmatch(r"[A-Za-z_+\-/]{1,40}", timezone)):
        return json_error(400, "session_invalid", "invalid timezone")
    if clock is not None and not (isinstance(clock, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d:[0-5]\d", clock)):
        return json_error(400, "session_invalid", "clock must be HH:MM:SS")
    if app.capacity_used() >= SETTINGS.max_sessions:
        Metrics.rejected["capacity"] = Metrics.rejected.get("capacity", 0) + 1
        return json_error(429, "busy", "no free sessions", {"Retry-After": "15"})
    app.starting += 1
    began = time.monotonic()
    session = Session(timezone, clock)
    try:
        await session.start()
    except Exception as error:  # noqa: BLE001
        Metrics.rejected["start_failed"] = Metrics.rejected.get("start_failed", 0) + 1
        await session.stop("start_failed")
        return json_error(500, "start_failed", f"{type(error).__name__}: {error}")
    finally:
        app.starting -= 1
    Metrics.started += 1
    Metrics.start_seconds.observe(time.monotonic() - began)
    app.sessions[session.id] = session
    base = base_url(request)
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(session.created_wall + SETTINGS.max_s))
    return web.json_response({
        "id": session.id, "ws_url": f"{ws_base}/v1/sessions/{session.id}/ws",
        "api_url": f"{base}/v1/sessions/{session.id}/bar/{session.token}", "token": session.token,
        "expires_at": expires, "limits": {"idle_s": SETTINGS.idle_s, "max_s": SETTINGS.max_s},
    }, status=201)


async def delete_session(request: web.Request) -> web.Response:
    app: App = request.app["state"]
    if not internal_auth(request):
        return json_error(401, "unauthorized", "internal token required")
    session = app.sessions.pop(request.match_info["id"], None)
    if session is None:
        return json_error(404, "not_found", "no such session")
    await session.stop("deleted")
    return web.Response(status=204)


def find_session(request: web.Request, token: str | None) -> Session | None:
    app: App = request.app["state"]
    session = app.sessions.get(request.match_info["id"])
    if session and token is not None and hmac.compare_digest(token, session.token):
        return session
    return None


async def websocket(request: web.Request) -> web.StreamResponse:
    session = find_session(request, request.query.get("token", ""))
    if session is None:
        return json_error(403, "forbidden", "bad session or token")
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await ws.prepare(request)
    if session.ws is not None and not session.ws.closed:  # новый зритель заменяет старого
        await session.ws.close(code=4001, message=b"replaced")
    session.ws, session.disconnected_at = ws, None
    session.last_activity = time.monotonic()
    await ws.send_str(json.dumps({
        "type": "hello", "session": session.id, "position": session.position, "firmware": FIRMWARE,
        "front": {"width": 72, "height": 16, "format": "rgb24"}, "back": {"width": 160, "height": 80, "format": "l8"},
        "frame": "u8 display | u32be ms | zlib(raw)",
    }))
    for display in sorted(session.frames):
        await ws.send_bytes(session.frames[display])
    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    command = json.loads(message.data).get("cmd", "")
                except (ValueError, AttributeError):
                    command = ""
                if not await session.handle_command(command if isinstance(command, str) else ""):
                    await ws.send_str(json.dumps({"type": "error", "error": "bad command"}))
    finally:
        if session.ws is ws:
            session.ws, session.disconnected_at = None, time.monotonic()
    return ws


async def proxy(request: web.Request) -> web.StreamResponse:
    app: App = request.app["state"]
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=origin_headers(request))
    session = find_session(request, request.match_info["token"])
    if session is None:
        return json_error(403, "forbidden", "bad session or token", origin_headers(request))
    tail = request.match_info["tail"]
    if not tail.startswith("api/") and tail != "api":
        return json_error(404, "not_found", "only /api is proxied", origin_headers(request))
    session.last_activity = time.monotonic()
    length = request.content_length
    if length is not None and length > SETTINGS.max_upload_bytes:
        return json_error(413, "too_large", "request body is too large", origin_headers(request))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "origin"}
    if length:  # прошивка ждёт Content-Length (загрузка пакета), chunked не понимает
        headers["Content-Length"] = str(length)
    url = f"http://127.0.0.1:{session.http_port}/{tail}"
    try:
        async with app.client.request(request.method, url, params=request.query, headers=headers,
                                      data=request.content if length else None) as upstream:
            response = web.StreamResponse(status=upstream.status, headers={
                **{k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}, **origin_headers(request)})
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
            await response.write_eof()
            return response
    except (OSError, asyncio.TimeoutError) as error:
        return json_error(502, "bar_unavailable", f"{type(error).__name__}", origin_headers(request))


async def on_startup(application: web.Application) -> None:
    state: App = application["state"]
    state.client = ClientSession(timeout=ClientTimeout(total=120))
    application["tasks"] = [asyncio.create_task(state.self_check()), asyncio.create_task(state.reaper())]


async def on_cleanup(application: web.Application) -> None:
    state: App = application["state"]
    for task in application["tasks"]:
        task.cancel()
    for session in list(state.sessions.values()):
        await session.stop("shutdown")
    await state.client.close()


def make_app() -> web.Application:
    application = web.Application(client_max_size=SETTINGS.max_upload_bytes + 1024 * 1024)
    application["state"] = App()
    application.add_routes([
        web.get("/v1/health", health), web.get("/metrics", metrics),
        web.post("/v1/sessions", create_session), web.delete("/v1/sessions/{id}", delete_session),
        web.get("/v1/sessions/{id}/ws", websocket),
        web.route("*", "/v1/sessions/{id}/bar/{token}/{tail:.*}", proxy),
    ])
    application.on_startup.append(on_startup)
    application.on_cleanup.append(on_cleanup)
    return application


if __name__ == "__main__":
    if not SETTINGS.internal_token:
        raise SystemExit("STREAM_INTERNAL_TOKEN is required")
    web.run_app(make_app(), host="0.0.0.0", port=SETTINGS.port, access_log=None)
