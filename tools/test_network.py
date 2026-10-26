"""Проверка сети стенда (FS-8) на образе рекордера: контейнер с тестовым «внешним» хостом → recorder/selftest_network.py.

    test_network.py [--image fsim-runtime-recorder:dev-current]       Код выхода 0 — всё прошло.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default="fsim-runtime-recorder:dev-current")
    args = parser.parse_args()
    name = f"fsim-network-test-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "run", "-d", "--name", name, "--platform", "linux/amd64", "--read-only",
                    "--tmpfs", "/run/fsim-recorder:size=3000m", "--tmpfs", "/tmp", "--cap-drop=ALL",
                    "--cap-add=NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--memory=2g",
                    "--pids-limit=512", "-e", 'NETPROXY_TEST_HOSTS={"live.test":"127.0.0.1:9099"}', args.image],
                   check=True, capture_output=True)
    try:
        wait = ("import urllib.request,time\n"
                "for _ in range(180):\n"
                "    try:\n        urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=3); break\n"
                "    except Exception: time.sleep(1)\n")
        script = (ROOT / "recorder" / "selftest_network.py").read_text()
        result = subprocess.run(["docker", "exec", "-i", name, "python3", "-"], input=(wait + script).encode())
        if result.returncode != 0:
            print(subprocess.run(["docker", "logs", "--tail", "25", name], capture_output=True, text=True).stdout)
        sys.exit(result.returncode)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    main()
