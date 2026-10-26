"""Самопроверка fsim-stream по контракту FS-7 — запускается внутри контейнера рядом с сервером.

    docker exec <контейнер> python3 /opt/stream/selftest.py      (STREAM_MAX_SESSIONS=2, токен test-token)
Код выхода 0 — всё прошло.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
import tarfile
import time
import zlib
from pathlib import Path

from aiohttp import ClientSession, WSMsgType

BASE = "http://127.0.0.1:8080"
AUTH = {"Authorization": "Bearer test-token"}
SMOKE = Path("/opt/stream/smoke_app/app.fsim.smoke")
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("PASS" if ok else "FAIL", name, detail, flush=True)


def icon_png(size: int = 8) -> bytes:
    rows = b"".join(b"\x00" + b"\xff\xff\xff" * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def smoke_package(root: Path = SMOKE) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"{root.name}/{path.relative_to(root)}", recursive=False)
        icon = icon_png()
        info = tarfile.TarInfo(f"{root.name}/appmeta/icon_front_8x8.png")
        info.size = len(icon)
        archive.addfile(info, io.BytesIO(icon))
    return buffer.getvalue()


class Viewer:
    """WebSocket-зритель: копит последние кадры, считает сообщения."""

    def __init__(self, ws):
        self.ws, self.frames, self.texts, self.updates = ws, {}, [], 0
        self.task = asyncio.create_task(self.pump())

    async def pump(self) -> None:
        async for message in self.ws:
            if message.type == WSMsgType.BINARY:
                display, tick = struct.unpack(">BI", message.data[:5])
                self.frames[display] = zlib.decompress(message.data[5:])
                self.updates += 1
            elif message.type == WSMsgType.TEXT:
                self.texts.append(json.loads(message.data))

    async def wait(self, condition, seconds: float = 20) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            await asyncio.sleep(0.05)
        return False


async def main() -> None:
    async with ClientSession() as http:
        for _ in range(180):  # самопроверка прошивки при старте сервиса идёт до ~минуты в эмуляции
            try:
                async with http.get(f"{BASE}/v1/health") as reply:
                    if reply.status == 200:
                        health = await reply.json()
                        break
            except OSError:
                pass
            await asyncio.sleep(1)
        else:
            check("health", False)
            return
        check("health", health["status"] == "ok" and bool(health["api_semver"]), str(health))

        async with http.post(f"{BASE}/v1/sessions") as reply:
            check("401 без внутреннего токена", reply.status == 401, str(reply.status))

        async with http.post(f"{BASE}/v1/sessions", headers=AUTH, json={"clock": "25:00:00"}) as reply:
            check("400 неверный clock", reply.status == 400, str(reply.status))

        began = time.monotonic()
        async with http.post(f"{BASE}/v1/sessions", headers=AUTH, json={"timezone": "Europe/Moscow", "clock": "12:34:55"}) as reply:
            first = await reply.json()
            check("201 сессия создана", reply.status == 201 and all(k in first for k in ("id", "ws_url", "api_url", "token", "expires_at")),
                  f"{time.monotonic() - began:.1f} с")
        api = first["api_url"]

        # --- WebSocket ---
        ws = await http.ws_connect(f"{first['ws_url']}?token={first['token']}")
        viewer = Viewer(ws)
        got = await viewer.wait(lambda: 0 in viewer.frames and 1 in viewer.frames)
        check("hello и кадры обоих экранов", got and viewer.texts and viewer.texts[0]["type"] == "hello")
        check("размеры кадров: передний 3456 Б, задний 12800 Б",
              len(viewer.frames.get(0, b"")) == 72 * 16 * 3 and len(viewer.frames.get(1, b"")) == 160 * 80)

        before = viewer.frames[0]
        sent_at = time.monotonic()
        await ws.send_str(json.dumps({"cmd": "switch busy"}))
        changed = await viewer.wait(lambda: viewer.frames.get(0) != before)
        check("ввод → кадр меняется", changed, f"{(time.monotonic() - sent_at) * 1000:.0f} мс")
        check("метка положения ручки", any(t.get("type") == "switch" and t["position"] == "busy" for t in viewer.texts))
        await ws.send_str(json.dumps({"cmd": "rm -rf /"}))
        await viewer.wait(lambda: any(t.get("type") == "error" for t in viewer.texts), 5)
        check("плохая команда отвергнута", any(t.get("type") == "error" for t in viewer.texts))
        await ws.send_str(json.dumps({"cmd": "switch apps"}))
        await asyncio.sleep(3)

        # --- api_url: тот же API, что у бара ---
        async with http.get(f"{api}/api/version") as reply:
            version = await reply.json(content_type=None)
            check("api_url: /api/version", reply.status == 200 and "api_semver" in version)
        async with http.get(f"{api}/api/time") as reply:
            stamp = (await reply.json(content_type=None))["timestamp"]
            check("часы сессии: clock 12:34:55 в Moscow", "T12:3" in stamp and stamp.endswith("+03:00"), stamp)
        async with http.get(f"{api}/etc/passwd") as reply:
            check("прокси отдаёт только /api", reply.status == 404, str(reply.status))
        async with http.get(f"{api.rsplit('/', 1)[0]}/wrong-token/api/version") as reply:
            check("403 при чужом токене", reply.status == 403, str(reply.status))

        async with http.post(f"{api}/api/apps/stage", data=smoke_package()) as reply:
            staged = await reply.json(content_type=None)
            check("api_url: apps/stage", reply.status == 200 and "install_key" in staged, str(reply.status))
        async with http.post(f"{api}/api/apps/install", params={"install_key": staged["install_key"]}) as reply:
            check("api_url: apps/install", reply.status == 200, str(reply.status))
        before = viewer.frames[0]
        async with http.post(f"{api}/api/apps/launch", params={"app_id": "app.fsim.smoke"}) as reply:
            check("api_url: apps/launch", reply.status == 200, str(reply.status))
        drawn = await viewer.wait(lambda: viewer.frames.get(0) != before and any(viewer.frames[0]), 15)
        check("кадр приложения пришёл зрителю", drawn)

        # --- лимиты ---
        async with http.post(f"{BASE}/v1/sessions", headers=AUTH) as reply:
            second = await reply.json()
            check("вторая сессия", reply.status == 201)
        async with http.post(f"{BASE}/v1/sessions", headers=AUTH) as reply:
            check("429 + Retry-After, когда мест нет", reply.status == 429 and "Retry-After" in reply.headers, str(reply.status))

        # --- завершение ---
        async with http.delete(f"{BASE}/v1/sessions/{second['id']}", headers=AUTH) as reply:
            check("DELETE сессии", reply.status == 204, str(reply.status))
        async with http.delete(f"{BASE}/v1/sessions/{first['id']}", headers=AUTH) as reply:
            check("DELETE первой сессии", reply.status == 204)
        await viewer.wait(lambda: ws.closed, 5)
        check("WebSocket закрыт сервером с причиной", ws.closed and any(t.get("type") == "closed" for t in viewer.texts))
        async with http.get(f"{api}/api/version") as reply:
            check("после завершения api_url недоступен", reply.status in (403, 404), str(reply.status))
        async with http.get(f"{BASE}/metrics") as reply:
            text = await reply.text()
            check("метрики", "fsim_stream_sessions_started_total 2" in text and "fsim_stream_input_to_frame_seconds_count" in text)

        # --- сеть сессии (FS-8): белый список, моки, лог ---
        async with http.post(f"{BASE}/v1/sessions", headers=AUTH, json={"network": {"allow": ["10.0.0.1"]}}) as reply:
            check("400 неверный network", reply.status == 400, str(reply.status))
        network = {"allow": [], "mocks": [{"host": "mocked.test", "path": "/price", "body": {"usd": 7}},
                                          {"host": "mocked.test", "path": "/post", "method": "POST", "status": 201, "body": "ok"}]}
        async with http.post(f"{BASE}/v1/sessions", headers=AUTH, json={"network": network}) as reply:
            third = await reply.json()
            check("сессия с network", reply.status == 201)
        api3 = third["api_url"]
        async with http.post(f"{api3}/api/apps/stage", data=smoke_package(Path("/opt/stream/net_app/app.fsim.nettest"))) as reply:
            staged = await reply.json(content_type=None)
        async with http.post(f"{api3}/api/apps/install", params={"install_key": staged["install_key"]}) as reply:
            check("сеть: установка приложения", reply.status == 200, str(reply.status))
        async with http.post(f"{api3}/api/apps/launch", params={"app_id": "app.fsim.nettest"}) as reply:
            check("приложение с сетью запущено", reply.status == 200, str(reply.status))
        log = {"entries": []}
        for _ in range(40):
            await asyncio.sleep(1)
            async with http.get(f"{BASE}/v1/sessions/{third['id']}/network", headers=AUTH) as reply:
                log = await reply.json()
            if any(e["host"] == "mocked.test" and e["method"] == "POST" for e in log["entries"]):
                break
        entries = log["entries"]
        if not any(e["source"] == "mock" for e in entries):  # диагностика для CI: что делала прошивка этой сессии
            print("--- диагностика: лог сети:", json.dumps(log)[:400])
            async with http.get(f"{api3}/api/apps/list") as reply:
                print("--- приложения:", [a["id"] for a in (await reply.json(content_type=None))["apps"]])
            for session_dir in sorted(Path("/run/fsim-stream").glob("s-*"), key=lambda d: d.stat().st_mtime)[-1:]:
                text = (session_dir / "fsim.log").read_text(errors="replace").splitlines()
                print("--- fsim.log сессии (хвост):")
                print("\n".join(line[:200] for line in text[-40:] if "WARN]" not in line))
        check("лог сети: моки", any(e["source"] == "mock" and e["host"] == "mocked.test" for e in entries), f"{len(entries)} записей")
        check("лог сети: хост вне списка отклонён", any(e["source"] == "rejected" and e["host"] == "live.test" for e in entries))
        check("лог сети: секреты замаскированы", "SECRET1" not in json.dumps(entries) and "TOPSECRET" not in json.dumps(entries))
        async with http.get(f"{BASE}/v1/sessions/{third['id']}/network") as reply:
            check("лог сети только по внутреннему токену", reply.status == 401, str(reply.status))
        async with http.delete(f"{BASE}/v1/sessions/{third['id']}", headers=AUTH) as reply:
            pass


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} проверок прошло")
sys.exit(0 if results and all(results) else 1)
