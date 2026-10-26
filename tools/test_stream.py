"""Проверка fsim-stream по контракту FS-7: контейнер → stream/selftest.py внутри → отчёт.

    test_stream.py [--image fsim-runtime-stream:dev-current]
Код выхода 0 — все проверки прошли. Только стандартная библиотека и docker.
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
    parser.add_argument("--image", default="fsim-runtime-stream:dev-current")
    args = parser.parse_args()
    name = f"fsim-stream-test-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "run", "-d", "--name", name, "--platform", "linux/amd64", "--read-only",
                    "--tmpfs", "/run/fsim-stream:size=4000m", "--tmpfs", "/tmp", "--cap-drop=ALL",
                    "--cap-add=NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--memory=2g",
                    "--pids-limit=512", "-e", "STREAM_INTERNAL_TOKEN=test-token", "-e", "STREAM_MAX_SESSIONS=2",
                    args.image], check=True, capture_output=True)
    try:
        selftest = (ROOT / "stream" / "selftest.py").read_bytes()
        result = subprocess.run(["docker", "exec", "-i", name, "python3", "-"], input=selftest)
        if result.returncode != 0:
            print(subprocess.run(["docker", "logs", "--tail", "30", name], capture_output=True, text=True).stdout)
        if result.returncode != 0:
            sys.exit(result.returncode)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    # Лимиты времени — отдельный контейнер с уменьшенными порогами
    limits = f"{name}-limits"
    subprocess.run(["docker", "run", "-d", "--name", limits, "--platform", "linux/amd64", "--read-only",
                    "--tmpfs", "/run/fsim-stream:size=4000m", "--tmpfs", "/tmp", "--cap-drop=ALL",
                    "--cap-add=NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--memory=2g",
                    "--pids-limit=512", "-e", "STREAM_INTERNAL_TOKEN=test-token", "-e", "STREAM_MAX_SESSIONS=4",
                    "-e", "STREAM_IDLE_S=12", "-e", "STREAM_RECONNECT_GRACE_S=8", "-e", "STREAM_MAX_S=45",
                    args.image], check=True, capture_output=True)
    try:
        code = subprocess.run(["docker", "exec", "-i", limits, "python3", "-"],
                              input=(ROOT / "stream" / "selftest_limits.py").read_bytes()).returncode
        sys.exit(code)
    finally:
        subprocess.run(["docker", "rm", "-f", limits], capture_output=True)


if __name__ == "__main__":
    main()
