"""Проверка лимитов времени fsim-stream с уменьшенными порогами (STREAM_IDLE_S=12, RECONNECT_GRACE_S=8, MAX_S=45)."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from aiohttp import ClientSession, WSMsgType

BASE = "http://127.0.0.1:8080"
AUTH = {"Authorization": "Bearer test-token"}
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("PASS" if ok else "FAIL", name, detail, flush=True)


async def main() -> None:
    async with ClientSession() as http:
        for _ in range(180):
            try:
                async with http.get(f"{BASE}/v1/health") as reply:
                    if reply.status == 200:
                        break
            except OSError:
                pass
            await asyncio.sleep(1)

        async def create() -> dict:
            async with http.post(f"{BASE}/v1/sessions", headers=AUTH) as reply:
                return await reply.json()

        # A: зритель не подключился — сессия закрывается после времени на подключение
        lonely = await create()
        # B: зритель подключился, но ничего не делает — закрытие по бездействию
        idle = await create()
        ws = await http.ws_connect(f"{idle['ws_url']}?token={idle['token']}")
        reasons: list[str] = []

        async def listen() -> None:
            async for message in ws:
                if message.type == WSMsgType.TEXT and json.loads(message.data).get("type") == "closed":
                    reasons.append(json.loads(message.data)["reason"])

        task = asyncio.create_task(listen())
        started = time.monotonic()
        while time.monotonic() - started < 30 and not reasons:
            await asyncio.sleep(0.5)
        check("бездействие закрывает сессию с причиной idle", reasons == ["idle"], f"{time.monotonic() - started:.0f} с")
        task.cancel()
        async with http.get(f"{lonely['api_url']}/api/version") as reply:
            check("без зрителя сессия закрыта (disconnected)", reply.status == 403, str(reply.status))
        # C: активная сессия живёт до max_lifetime
        busy = await create()
        ws = await http.ws_connect(f"{busy['ws_url']}?token={busy['token']}")

        async def drain() -> None:  # без чтения закрытие со стороны сервера не заметить
            async for _ in ws:
                pass

        drainer = asyncio.create_task(drain())
        started = time.monotonic()
        closed = None
        while time.monotonic() - started < 60 and closed is None:
            try:
                await ws.send_str(json.dumps({"cmd": "wheel 1"}))  # активность: idle не сработает
            except ConnectionError:
                pass
            await asyncio.sleep(2)
            if ws.closed:
                closed = time.monotonic() - started
        drainer.cancel()
        check("срок жизни ограничен (max_lifetime)", closed is not None, f"{closed or 0:.0f} с")
        async with http.get(f"{BASE}/metrics") as reply:
            text = await reply.text()
            for reason in ("idle", "disconnected", "max_lifetime"):
                check(f"метрика завершений {reason}", f'fsim_stream_sessions_ended_total{{reason="{reason}"}}' in text)


asyncio.run(main())
print(f"\n{sum(results)}/{len(results)} проверок прошло")
sys.exit(0 if results and all(results) else 1)
