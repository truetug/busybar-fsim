"""Проверка сети стенда (FS-8) внутри контейнера рекордера (NETPROXY_TEST_HOSTS={"live.test":"127.0.0.1:9099"}).

    docker exec -i <контейнер> python3 - < recorder/selftest_network.py        Код выхода 0 — всё прошло.
"""


import asyncio
import http.client
import http.server
import io
import json
import sys
import tarfile
import threading
import uuid
from pathlib import Path

sys.path.insert(0, "/opt/recorder")
from netproxy import PublicOnlyResolver, validate_network  # noqa: E402

APP = Path("/opt/recorder/net_app/app.fsim.nettest")
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("PASS" if ok else "FAIL", name, detail, flush=True)


class Origin(http.server.BaseHTTPRequestHandler):
    """Локальный «внешний» сервер: прокси идёт на него вместо live.test (только в тестовом режиме)."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/ok":
            body = b"live-ok"
        elif self.path == "/big":
            body = b"x" * (2 * 1024 * 1024)
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "http://evil.test/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            body = b"?"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def package() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(APP.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=f"{APP.name}/{path.relative_to(APP)}", recursive=False)
    return buffer.getvalue()


def record(network: dict | None) -> tuple[int, bytes]:
    scenario = {"duration_s": 14, "intro_delay_ms": 1000, "action_pause_ms": 500, "return_command": "back", "actions": []}
    if network is not None:
        scenario["network"] = network
    boundary = uuid.uuid4().hex
    body = b""
    for name, data in (("package", package()), ("scenario", json.dumps(scenario).encode())):
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{name}"\r\n\r\n'.encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=300)
    connection.request("POST", "/v1/record", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    reply = connection.getresponse()
    return reply.status, reply.read()


def main() -> None:
    # --- валидация network ---
    for name, bad in (("IP вместо имени", {"allow": ["10.0.0.1"]}), ("схема в хосте", {"allow": ["https://a.example.com"]}),
                      ("слишком много хостов", {"allow": [f"h{i}.example.com" for i in range(21)]}),
                      ("delay_ms вне диапазона", {"mocks": [{"host": "a.example.com", "path": "/", "delay_ms": 99999}]}),
                      ("тело мока слишком большое", {"mocks": [{"host": "a.example.com", "path": "/", "body": "x" * 70000}]})):
        try:
            validate_network(bad)
            check(f"validate_network отвергает: {name}", False)
        except ValueError:
            check(f"validate_network отвергает: {name}", True)
    check("validate_network принимает *.домен", validate_network({"allow": ["*.example.com"]})["allow"] == ["*.example.com"])
    try:
        asyncio.run(PublicOnlyResolver({}).resolve("localhost", 80))
        check("внутренний адрес отвергается (SSRF)", False)
    except OSError as error:
        check("внутренний адрес отвергается (SSRF)", "private_address" in str(error))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 9099), Origin)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    network = {"allow": ["live.test"], "mocks": [
        {"host": "mocked.test", "path": "/price", "status": 200, "body": {"usd": 42}, "delay_ms": 300},
        {"host": "mocked.test", "path": "/post", "method": "POST", "status": 201, "body": "created"},
    ]}
    status, data = record({"allow": ["10.0.0.1"]})
    check("рекордер: 400 на неверный network", status == 400 and b"scenario_invalid" in data, str(status))
    status, data = record(network)
    check("рекордер: прогон с сетью", status == 200, f"{status}, {len(data)} Б")
    if status != 200:
        print(data[:400])
        return
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        meta = json.loads(archive.extractfile("video.json").read())
    log = meta.get("network_log")
    check("video.json: network_log", isinstance(log, dict) and "entries" in log and "total" in log)
    entries = log["entries"]
    for entry in entries:
        print("   ", entry["t_ms"], entry["method"], entry["url"], entry["source"], entry.get("reason"), entry["status"], entry["resp_bytes"], f"{entry['duration_ms']}ms")

    def find(**conditions):
        return [e for e in entries if all(e.get(k) == v for k, v in conditions.items())]

    live = find(host="live.test", source="network")
    check("живой запрос по https (порт 443)", any(e["url"] == "https://live.test/ok" and e["status"] == 200 for e in live))
    check("живой запрос по http (порт 80)", any(e["url"] == "http://live.test/ok" and e["status"] == 200 for e in live))
    price = find(host="mocked.test", source="mock")
    check("мок отвечает без выхода наружу", any(e["status"] == 200 and e["resp_body"] == '{"usd": 42}' for e in price))
    check("задержка мока (delay_ms 300)", any(e["duration_ms"] >= 280 for e in price), str([e["duration_ms"] for e in price]))
    check("ключ в query замаскирован", any("apikey=***" in e["url"] and "SECRET1" not in e["url"] for e in price))
    post = [e for e in entries if e["method"] == "POST"]
    check("POST: мок по методу, Authorization замаскирован",
          bool(post) and post[0]["source"] == "mock" and post[0]["status"] == 201
          and post[0]["req_headers"].get("Authorization") == "***" and "TOPSECRET" not in json.dumps(entries))
    check("не из списка: DNS отказ с записью", any(e["host"] == "blocked.test" and e["source"] == "rejected" and e["reason"] == "not_allowed" for e in entries))
    big = [e for e in live if e["url"].endswith("/big")]
    check("большой ответ обрезан до 1 МиБ", bool(big) and big[0]["resp_bytes"] == 1024 * 1024 and big[0]["reason"] == "truncated")
    redirect = [e for e in live if e["url"].endswith("/redir")]
    check("редирект отдан приложению, не выполнен", bool(redirect) and redirect[0]["status"] == 302 and not find(host="evil.test"))
    check("лог ≤ 256 КБ", len(json.dumps(log)) <= 256 * 1024, str(len(json.dumps(log))))

    # --- без поля network сети нет ---
    status, data = record(None)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        meta = json.loads(archive.extractfile("video.json").read())
    check("без network: нет network_log, сети нет", status == 200 and "network_log" not in meta)
    server.shutdown()


main()
print(f"\n{sum(results)}/{len(results)} проверок прошло")
sys.exit(0 if results and all(results) else 1)
