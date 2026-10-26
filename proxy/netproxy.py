"""Прокси стенда для сети виртуального бара (FS-8): белый список, подставные ответы, полный лог.

fsim (fsim_net.c) направляет сюда всё, что приложение отправляет в сеть:
  * DNS-запросы → DNS-ответчик (UDP): разрешённым хостам — 127.0.0.2, остальным — NXDOMAIN;
  * 127.0.0.2:443 → HTTP-порт «https» (приложение шлёт обычный HTTP: шифрования в fsim нет);
  * 127.0.0.2:80  → HTTP-порт «http».
Наружу ходит только этот процесс; fsim сам в сеть не ходит.

Что делает с запросом: заготовленный ответ (mock) → он; хост в белом списке → настоящий запрос
(HTTPS для порта 443, HTTP для 80, проверка сертификата, без редиректов, только публичные адреса); иначе — 403 и
запись «rejected». Всё пишется в лог.

    config = validate_network({"allow": ["api.example.com", "*.cdn.example.com"], "mocks": [...]})
    proxy = NetProxy(config); ports = proxy.start(); ...; log = proxy.render_log(); proxy.stop()

Только aiohttp и стандартная библиотека.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import struct
import threading
import time
from urllib.parse import parse_qsl, urlencode

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from aiohttp.abc import AbstractResolver

MAX_ALLOW = 20
MAX_MOCKS = 20
MAX_MOCK_BODY = 64 * 1024
MAX_MOCK_DELAY_MS = 10_000
MAX_REQUESTS = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BODY = 64 * 1024
REQUEST_TIMEOUT_S = 10
LOG_BODY_BYTES = 2048
LOG_TOTAL_BYTES = 256 * 1024
PROXY_ADDRESS = "127.0.0.2"
FIRMWARE_OWN_HOSTS = {"time.busy.app"}  # фон самой прошивки (синхронизация времени): отвечаем NXDOMAIN, в лог не пишем
HOST_RE = re.compile(r"(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
SENSITIVE = ("authorization", "cookie", "key", "token", "secret", "password", "passwd", "signature")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length", "host"}


# ---------- настройки сети из сценария / сессии ----------

def validate_network(raw: object) -> dict:
    """Проверить поле network; вернуть нормализованную копию. ValueError — с текстом для пользователя."""
    if not isinstance(raw, dict):
        raise ValueError("network must be an object")
    allow = raw.get("allow", [])
    mocks = raw.get("mocks", [])
    if not isinstance(allow, list) or len(allow) > MAX_ALLOW:
        raise ValueError(f"network.allow must be a list of at most {MAX_ALLOW} hosts")
    if not isinstance(mocks, list) or len(mocks) > MAX_MOCKS:
        raise ValueError(f"network.mocks must be a list of at most {MAX_MOCKS} items")
    hosts = []
    for host in allow:
        if not isinstance(host, str) or not HOST_RE.fullmatch(host.lower()) or len(host) > 253 or _is_ip(host):
            raise ValueError(f"network.allow: invalid host {host!r} (name or *.name, no IP, port or scheme)")
        hosts.append(host.lower())
    normalized = []
    for mock in mocks:
        if not isinstance(mock, dict):
            raise ValueError("network.mocks items must be objects")
        host, path = mock.get("host"), mock.get("path", "/")
        if not isinstance(host, str) or not HOST_RE.fullmatch(host.lower()) or host.startswith("*") or _is_ip(host):
            raise ValueError(f"network.mocks: invalid host {host!r}")
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 512:
            raise ValueError("network.mocks: path must start with /")
        status = mock.get("status", 200)
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("network.mocks: status must be 100..599")
        method = mock.get("method")
        if method is not None and (not isinstance(method, str) or not re.fullmatch(r"[A-Za-z]{3,10}", method)):
            raise ValueError("network.mocks: invalid method")
        delay = mock.get("delay_ms", 0)
        if type(delay) is not int or not 0 <= delay <= MAX_MOCK_DELAY_MS:
            raise ValueError(f"network.mocks: delay_ms must be 0..{MAX_MOCK_DELAY_MS}")
        headers = mock.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("network.mocks: headers must be an object of strings")
        body = mock.get("body", "")
        if isinstance(body, str):
            content, content_type = body.encode(), "text/plain; charset=utf-8"
        else:
            try:
                content, content_type = json.dumps(body, ensure_ascii=False).encode(), "application/json"
            except (TypeError, ValueError):
                raise ValueError("network.mocks: body must be a string or JSON") from None
        if len(content) > MAX_MOCK_BODY:
            raise ValueError(f"network.mocks: body is larger than {MAX_MOCK_BODY} bytes")
        normalized.append({"host": host.lower(), "path": path, "status": status, "method": method.upper() if method else None,
                           "delay_ms": delay, "headers": headers, "content": content, "content_type": content_type})
    return {"allow": hosts, "mocks": normalized}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def host_allowed(config: dict, host: str) -> bool:
    host = host.lower()
    if any(mock["host"] == host for mock in config["mocks"]):
        return True  # хост мока разрешён сам: ответ подставляется, наружу не идём
    for pattern in config["allow"]:
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]):
            return True
    return False


def match_mock(config: dict, host: str, method: str, path: str) -> dict | None:
    for mock in config["mocks"]:
        if mock["host"] != host or (mock["method"] and mock["method"] != method.upper()):
            continue
        pattern = mock["path"]
        if (pattern.endswith("*") and path.startswith(pattern[:-1])) or path == pattern:
            return mock
    return None


# ---------- лог ----------

def mask_headers(headers) -> dict:
    masked = {}
    for name, value in headers.items():
        masked[name] = "***" if any(word in name.lower() for word in SENSITIVE) else value
    return masked


def mask_url(url: str) -> str:
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    pairs = [(k, "***" if any(word in k.lower() for word in SENSITIVE) else v) for k, v in parse_qsl(query, keep_blank_values=True)]
    return base + "?" + urlencode(pairs, safe="*")


def body_for_log(data: bytes) -> str | None:
    if not data:
        return None
    try:
        text = data[:LOG_BODY_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        return f"[binary {len(data)} bytes]"
    return text + ("…" if len(data) > LOG_BODY_BYTES else "")


# ---------- только публичные адреса ----------

class PublicOnlyResolver(AbstractResolver):
    """SSRF / DNS rebinding: хост из белого списка не должен вести на внутренние адреса."""

    def __init__(self, test_hosts: dict[str, str]):
        self.test_hosts = test_hosts

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        if host in self.test_hosts:  # только для тестов (NETPROXY_TEST_HOSTS): хост → локальный «внешний» сервер
            target_host, _, target_port = self.test_hosts[host].partition(":")
            return [{"hostname": host, "host": target_host, "port": int(target_port or port), "family": socket.AF_INET,
                     "proto": 0, "flags": 0}]
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        result = []
        for family_, _, proto, _, address in infos:
            if family_ in (socket.AF_INET, socket.AF_INET6) and ipaddress.ip_address(address[0]).is_global:
                result.append({"hostname": host, "host": address[0], "port": port, "family": family_, "proto": proto, "flags": 0})
        if not result:
            raise OSError("private_address")
        return result

    async def close(self) -> None:
        return None


class DnsProtocol(asyncio.DatagramProtocol):
    """Минимальный DNS: A-записи разрешённых хостов → 127.0.0.2, остальное — NXDOMAIN / пустой ответ."""

    def __init__(self, proxy: "NetProxy"):
        self.proxy, self.transport = proxy, None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, address) -> None:
        try:
            reply = self.answer(data)
        except (IndexError, struct.error, UnicodeDecodeError):
            return
        if reply:
            self.transport.sendto(reply, address)

    def answer(self, data: bytes) -> bytes | None:
        if len(data) < 17:
            return None
        ident, flags, questions = struct.unpack(">HHH", data[:6])
        if questions != 1:
            return None
        position, labels = 12, []
        while data[position]:
            length = data[position]
            labels.append(data[position + 1:position + 1 + length].decode("ascii"))
            position += 1 + length
        name = ".".join(labels).lower()
        qtype, qclass = struct.unpack(">HH", data[position + 1:position + 5])
        question = data[12:position + 5]
        allowed = host_allowed(self.proxy.config, name)
        if qtype == 1:
            self.proxy.note_dns(name, allowed)
        if not allowed:
            return struct.pack(">HHHHHH", ident, 0x8183, 1, 0, 0, 0) + question  # NXDOMAIN
        if qtype == 1:
            record = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton(PROXY_ADDRESS)
            return struct.pack(">HHHHHH", ident, 0x8180, 1, 1, 0, 0) + question + record
        return struct.pack(">HHHHHH", ident, 0x8180, 1, 0, 0, 0) + question  # AAAA и прочее — пустой ответ


# ---------- прокси ----------

class NetProxy:
    """Прокси в собственном потоке и цикле событий: sync-код рекордера и async-код стрима используют одинаково."""

    def __init__(self, config: dict):
        self.config = config
        self.entries: list[dict] = []
        self.total_requests = 0
        self.total_bytes = 0
        self.t0 = time.monotonic()
        self.test_hosts = json.loads(os.environ.get("NETPROXY_TEST_HOSTS", "{}"))
        self.ports: dict[str, int] = {}
        self.lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: Exception | None = None
        self.dns_seen: set[str] = set()

    # -- жизненный цикл --
    def start(self) -> dict[str, int]:
        self.thread = threading.Thread(target=self._run, daemon=True, name="netproxy")
        self.thread.start()
        self.ready.wait(15)
        if self.error or not self.ports:
            raise RuntimeError(f"network proxy did not start: {self.error}")
        return self.ports

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(5)

    def set_epoch(self) -> None:
        """Отсчёт t_ms в логе — с этого момента (рекордер вызывает его в начале окна записи)."""
        self.t0 = time.monotonic()

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
            self.ready.set()
            self.loop.run_forever()
        except Exception as error:  # noqa: BLE001
            self.error = error
            self.ready.set()
        finally:
            self.loop.close()

    async def _serve(self) -> None:
        transport, _ = await self.loop.create_datagram_endpoint(lambda: DnsProtocol(self), local_addr=("127.0.0.1", 0))
        self.ports["dns"] = transport.get_extra_info("sockname")[1]
        connector = TCPConnector(resolver=PublicOnlyResolver(self.test_hosts), force_close=True)
        self.client = ClientSession(connector=connector, timeout=ClientTimeout(total=REQUEST_TIMEOUT_S), auto_decompress=False)
        for name, scheme in (("https", "https"), ("http", "http")):
            application = web.Application(client_max_size=MAX_REQUEST_BODY + 1024)
            application.router.add_route("*", "/{tail:.*}", self._handler(scheme))
            runner = web.AppRunner(application, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            self.ports[name] = site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    # -- лог --
    def _t_ms(self) -> int:
        return round((time.monotonic() - self.t0) * 1000)

    def note_dns(self, host: str, allowed: bool) -> None:
        if not allowed and host not in self.dns_seen and host not in FIRMWARE_OWN_HOSTS:
            self.dns_seen.add(host)
            self._add({"t_ms": self._t_ms(), "method": "DNS", "url": host, "host": host, "source": "rejected",
                       "reason": "not_allowed", "status": None, "resp_bytes": 0, "duration_ms": 0})

    def _add(self, entry: dict) -> None:
        with self.lock:
            self.entries.append(entry)

    def render_log(self) -> dict:
        """{"entries":[…],"total":N,"truncated":bool}; общий размер ≤ LOG_TOTAL_BYTES (лишние записи отбрасываются)."""
        with self.lock:
            entries = list(self.entries)
        kept, size = [], 2
        for entry in entries:
            piece = len(json.dumps(entry, ensure_ascii=False)) + 1
            if size + piece > LOG_TOTAL_BYTES:
                break
            kept.append(entry)
            size += piece
        return {"entries": kept, "total": len(entries), "truncated": len(kept) < len(entries)}

    # -- обработка запросов --
    def _handler(self, scheme: str):
        async def handle(request: web.Request) -> web.Response:
            return await self._handle(request, scheme)
        return handle

    async def _handle(self, request: web.Request, scheme: str) -> web.Response:
        started = time.monotonic()
        host_header = request.headers.get("Host", "")
        host, _, port = host_header.partition(":")
        host = host.lower()
        path_qs = request.raw_path
        url = mask_url(f"{scheme}://{host}{path_qs}")
        entry = {"t_ms": self._t_ms(), "method": request.method, "url": url, "host": host, "source": "rejected",
                 "reason": None, "status": None, "resp_bytes": 0, "duration_ms": 0,
                 "req_headers": mask_headers(request.headers), "req_body": None, "resp_body": None}
        body = await request.read()
        entry["req_body"] = body_for_log(body)
        if entry["req_body"] is not None and any(w in request.headers.get("Content-Type", "").lower() for w in ("form",)):
            entry["req_body"] = mask_url("?" + (entry["req_body"] or ""))[1:]

        def finish(status: int, payload: bytes, headers: dict, source: str, reason: str | None = None) -> web.Response:
            entry.update(source=source, reason=reason, status=status, resp_bytes=len(payload),
                         duration_ms=round((time.monotonic() - started) * 1000), resp_body=body_for_log(payload))
            self._add(entry)
            return web.Response(status=status, body=payload, headers={**headers, "Connection": "close"})

        if port and port != ("443" if scheme == "https" else "80"):
            return finish(403, b"blocked by fsim network policy: port", {}, "rejected", "port_not_allowed")
        if self.total_requests >= MAX_REQUESTS or self.total_bytes >= MAX_TOTAL_BYTES:
            return finish(429, b"blocked by fsim network policy: limit", {}, "rejected", "limit")
        self.total_requests += 1
        if not host_allowed(self.config, host):
            return finish(403, b"blocked by fsim network policy: host is not allowed", {}, "rejected", "not_allowed")
        mock = match_mock(self.config, host, request.method, request.path)
        if mock:
            if mock["delay_ms"]:
                await asyncio.sleep(mock["delay_ms"] / 1000)
            return finish(mock["status"], mock["content"], {"Content-Type": mock["content_type"], **mock["headers"]}, "mock")
        if not any(host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in self.config["allow"]):
            return finish(404, b"fsim: no mock for this path", {}, "rejected", "no_mock")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        try:
            upstream_scheme = "http" if host in self.test_hosts else scheme  # тестовый «внешний» сервер — без TLS
            async with self.client.request(request.method, f"{upstream_scheme}://{host}{path_qs}", headers=headers,
                                           data=body or None, allow_redirects=False) as upstream:
                payload, cut = b"", False
                async for chunk in upstream.content.iter_chunked(65536):
                    payload += chunk
                    if len(payload) > MAX_RESPONSE_BYTES:
                        payload, cut = payload[:MAX_RESPONSE_BYTES], True
                        break
                self.total_bytes += len(payload)
                reply_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
                return finish(upstream.status, payload, reply_headers, "network", "truncated" if cut else None)
        except asyncio.TimeoutError:
            return finish(504, b"fsim: upstream timeout", {}, "network", "timeout")
        except OSError as error:
            reason = "private_address" if "private_address" in str(error) else "unreachable"
            return finish(403 if reason == "private_address" else 502, f"fsim: {reason}".encode(), {}, "rejected" if reason == "private_address" else "network", reason)
        except Exception as error:  # noqa: BLE001
            return finish(502, f"fsim: {type(error).__name__}".encode(), {}, "network", "error")
