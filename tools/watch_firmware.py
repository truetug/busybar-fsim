"""Наблюдатель за прошивкой: новая сборка в CI → fsim из её исходников → смоук → образ.

    watch_firmware.py [--work DIR] [--targets dev,release] [--force] [--dry-run] [--push ПРЕФИКС]

Цели:
  dev       — последняя сборка ветки dev (коммит берётся из имени артефакта CI); не для ревью;
  release   — самая новая версия с API приложений (/api/apps/*, с 1.3.0-rc); стабильная выше -rc при
              равном номере; коммит — по git-тегу версии;
  <имя>     — любая папка каталога сборок (например 1.3.1-rc) — как release.

Для каждой цели:
  1. сверка с состоянием: коммит не изменился — ничего не делаем;
  2. исходники этого коммита с анонимного https GitHub (нужные подмодули, depth 1);
  3. сборка fsim в docker (образ fsim-build) и образ SD из CI-пакетов ТОГО ЖЕ коммита;
  4. образы fsim-runtime, fsim-runtime-recorder и fsim-runtime-stream (<цель>-<коммит>), смоук прошивки
     (smoke_runtime.py), контракт рекордера (test_recorder.py) и сервиса стрима (test_stream.py);
  5. всё прошло — теги <образ>:<цель>-current переезжают на новые образы; у релиза ещё и `stable`
     (его берёт каталог; dev — только ранний сигнал о поломках и отличиях от stable);
     не прошёл — current остаётся на последней рабочей сборке, пишется alerts/<время>-<цель>.md
     и вызывается FSIM_WATCH_NOTIFY (команда, получает путь к файлу), код выхода 2.
     Сломанный коммит повторно не собирается, пока не выйдет новый или не задан --force.

--push ghcr.io/<владелец> (или FSIM_REGISTRY) — после успешного прохода отправить образы
<префикс>/fsim-runtime[-recorder]:<цель>-<коммит>, :<цель>-current и (для release) :stable.
Переменные: FSIM_BUILD_PROXY — прокси для контейнеров сборки; FSIM_WATCH_NOTIFY — команда тревоги.
Состояние и кэш — в --work (по умолчанию ~/.cache/fsim-watch): state.json, src/, build-<цель>/,
reports/, alerts/. Только стандартная библиотека, git и docker.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = "https://update.flipperzero.one/builds/busybar-firmware/"
REPO = "https://github.com/busy-app/busybar-firmware.git"
BUILDER_IMAGE = "fsim-build"
# Подмодули, из которых собирается fsim (остальное — Wi-Fi, Matter, USB, документация — не нужно)
SUBMODULES = [
    "fbt_layers/core_libs", "fbt_layers/freertos", "fbt_layers/fbtng", "lib/lvgl", "lib/jerryscript",
    "lib/mongoose", "lib/mbedtls", "lib/cjson", "lib/microtar", "lib/heatshrink", "lib/zlib", "lib/stb/stb_repo",
]
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY")


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Команда с проверкой кода выхода; stderr попадает в сообщение об ошибке."""
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        tail = (result.stdout + result.stderr)[-1500:]
        raise RuntimeError(f"{' '.join(command[:4])}… → код {result.returncode}\n{tail}")
    return result


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()


def has_apps_api(version: str) -> bool:
    """В теге версии есть http_api/api_apps.c (установка и запуск приложений по HTTP)."""
    url = f"https://raw.githubusercontent.com/busy-app/busybar-firmware/{version}/applications/services/web_server/http_api/api_apps.c"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30):
            return True
    except urllib.error.HTTPError:
        return False


def resolve(target: str) -> dict:
    """(имя папки каталога, git-ссылка, короткий коммит) для цели."""
    if target == "dev":
        names = re.findall(r"busybar-f22-update-[^\"<> ]+?-([0-9a-f]{7,10})\.tgz", fetch_text(f"{INDEX}dev/"))
        if not names:
            raise RuntimeError("в каталоге сборок dev нет пакета обновления")
        return {"build": "dev", "ref": "refs/heads/dev", "commit": names[0]}
    if target == "release":
        # Каталогу нужна прошивка с API установки приложений (/api/apps/*, появился в 1.3.0-rc). Берём самую
        # новую версию, где он есть; при равенстве номера стабильная выше -rc. Стабильная без него (1.2.4)
        # рекордеру не подходит: ни установить, ни запустить приложение.
        versions = re.findall(r'href="(\d+\.\d+\.\d+(?:-rc)?)/"', fetch_text(INDEX))
        if not versions:
            raise RuntimeError("в каталоге сборок нет версий")

        def order(name: str):
            return (*(int(part) for part in name.removesuffix("-rc").split(".")), not name.endswith("-rc"))

        for candidate in sorted(set(versions), key=order, reverse=True):
            if has_apps_api(candidate):
                target = candidate
                break
        else:
            raise RuntimeError("ни в одной версии нет API приложений")
        if target.endswith("-rc"):
            print(f"[release] внимание: стабильной версии с API приложений ещё нет, берём {target}")
    tags = run(["git", "ls-remote", REPO, f"refs/tags/{target}", f"refs/tags/{target}^{{}}"]).stdout.split()
    if not tags:
        raise RuntimeError(f"в репозитории нет тега {target}")
    return {"build": target, "ref": f"refs/tags/{target}", "commit": tags[-2][:8]}  # peeled, если он есть


def sync_sources(work: Path, info: dict) -> None:
    """Исходники нужного коммита в work/src. Репозиторий переиспользуется между запусками."""
    src = work / "src"
    src.mkdir(parents=True, exist_ok=True)
    if not (src / ".git").exists():
        run(["git", "init", "-q", str(src)])
        run(["git", "-C", str(src), "remote", "add", "origin", REPO])
    run(["git", "-C", str(src), "fetch", "-q", "--depth", "200", "origin", info["ref"]])
    full = run(["git", "-C", str(src), "rev-parse", "--verify", f"{info['commit']}^{{commit}}"]).stdout.strip()
    run(["git", "-C", str(src), "checkout", "-q", "-f", full])
    run(["git", "-C", str(src), "submodule", "sync", "-q"])
    run(["git", "-C", str(src), "submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "6",
         "--", *SUBMODULES])


def docker_run(work: Path, name: str, script: str) -> None:
    """Команда в образе fsim-build: fsim — только для чтения, исходники прошивки — только для чтения."""
    env = [arg for var in PROXY_VARS if var in os.environ for arg in ("-e", f"{var}={os.environ[var]}")]
    if os.environ.get("FSIM_BUILD_PROXY"):  # прокси только для контейнеров (host.docker.internal)
        env += ["-e", f"HTTP_PROXY={os.environ['FSIM_BUILD_PROXY']}", "-e", f"HTTPS_PROXY={os.environ['FSIM_BUILD_PROXY']}"]
    run(["docker", "run", "--rm", "--add-host", "host.docker.internal:host-gateway", *env,
         "-v", f"{ROOT}:/fsim:ro", "-v", f"{work / 'src'}:/fw:ro", "-v", f"{work}:/work",
         "-w", "/fsim", BUILDER_IMAGE, "sh", "-c", script])


def build(work: Path, target: str, info: dict) -> tuple[Path, Path]:
    """Бинарник fsim и образ SD, собранные из одного коммита."""
    build_dir = f"/work/build-{target}"
    docker_run(work, target, (
        f"cmake -S . -B {build_dir} -G Ninja -DFW_DIR=/fw -DFSIM_GIT_HASH={info['commit']} "
        f"-DFSIM_GIT_BRANCH={target} >/dev/null && cmake --build {build_dir}"
    ))
    sd = work / f"sd-{target}.img"
    commit_filter = info["commit"] if target == "dev" else ""
    docker_run(work, target, f"sh tools/make_sd.sh {info['build']} /work/sd-{target}.img {commit_filter}")
    return work / f"build-{target}" / "fsim", sd


def firmware_id(target: str, info: dict) -> str:
    """Что отдаёт /v1/health: dev-<коммит> для dev, release-<версия> для релиза."""
    return f"dev-{info['commit']}" if target == "dev" else f"{'release' if target == 'release' else target}-{info['build']}"


def save_openapi(work: Path, target: str, info: dict, state: dict) -> None:
    """openapi.yaml сборки CI; если есть и dev, и stable — diff между ними (что изменится для приложений)."""
    try:
        folder = f"{INDEX}{info['build']}/"
        names = re.findall(r"busybar-f22-openapi-[^\"<> ]+\.yaml", fetch_text(folder))
        if not names:
            return
        path = work / "openapi" / f"{target}-{info['commit']}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fetch_text(folder + names[0]))
        state[target]["openapi"] = str(path)
        dev, release = state.get("dev", {}).get("openapi"), state.get("release", {}).get("openapi")
        if dev and release and Path(dev).exists() and Path(release).exists():
            diff = "".join(difflib.unified_diff(
                Path(release).read_text().splitlines(True), Path(dev).read_text().splitlines(True),
                f"stable ({state['release'].get('build')})", f"dev ({state['dev'].get('commit')})"))
            (work / "reports").mkdir(parents=True, exist_ok=True)
            (work / "reports" / "openapi-dev-vs-stable.diff").write_text(diff or "# без отличий\n")
    except Exception as error:  # noqa: BLE001 — diff удобство, не условие сборки
        print(f"[{target}] openapi не сохранён: {error}", file=sys.stderr)


def alert(work: Path, target: str, info: dict, problem: str) -> Path:
    path = work / "alerts" / f"{time.strftime('%Y%m%d-%H%M%S')}-{target}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# fsim: сборка {target} {info['commit']} не прошла\n\n"
        f"Виртуальный бар **остаётся на последней рабочей сборке** (`fsim-runtime:{target}-current`).\n\n"
        f"Причина:\n\n```\n{problem}\n```\n\n"
        f"Повторить после правки: `tools/watch_firmware.py --targets {target} --force`.\n"
    )
    command = os.environ.get("FSIM_WATCH_NOTIFY")
    if command:
        subprocess.run([*command.split(), str(path)], check=False)
    return path


def publish(prefix: str, target: str, tag: str) -> None:
    """Образы текущей сборки в реестр: тег сборки, <цель>-current и stable (только release)."""
    tags = {"fsim-runtime": tag, "fsim-runtime-recorder": tag, "fsim-runtime-stream": tag}
    suffixes = [tag, f"{target}-current"] + (["stable"] if target != "dev" else [])
    for repository in tags:
        for suffix in dict.fromkeys(suffixes):
            remote = f"{prefix.rstrip('/')}/{repository}:{suffix}"
            run(["docker", "tag", f"{repository}:{tag}", remote])
            run(["docker", "push", remote])
            print(f"[{target}] опубликован {remote}")


def process(work: Path, target: str, state: dict, force: bool, dry_run: bool, push: str | None = None) -> bool:
    info = resolve(target)
    known = state.get(target, {})
    print(f"[{target}] CI: {info['build']} @ {info['commit']}; состояние: {known.get('commit')} {known.get('status')}")
    if dry_run:
        return True
    if known.get("commit") == info["commit"] and not force:
        print(f"[{target}] без изменений")
        return True
    stage = "sync"
    try:
        sync_sources(work, info)
        stage = "build"
        binary, sd = build(work, target, info)
        image = f"fsim-runtime:{target}-{info['commit']}"
        run([str(ROOT / "tools" / "package_runtime.sh"), image], env={
            **os.environ, "FSIM_BINARY": str(binary), "FSIM_SD": str(sd), "FSIM_FIRMWARE": firmware_id(target, info)})
        stage = "recorder"
        tag = f"{target}-{info['commit']}"
        run([str(ROOT / "tools" / "package_recorder.sh"), tag])
        stage = "stream"
        run([str(ROOT / "tools" / "package_stream.sh"), tag])
        stage = "smoke"
        report = work / "reports" / f"{target}-{info['commit']}.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        smoke = subprocess.run([sys.executable, str(ROOT / "tools" / "smoke_runtime.py"), image, "--report", str(report)],
                               capture_output=True, text=True)
        if smoke.returncode != 0:
            raise RuntimeError(f"смоук не прошёл (отчёт {report}):\n{smoke.stdout[-1500:]}")
        stage = "recorder-test"
        contract = subprocess.run([sys.executable, str(ROOT / "tools" / "test_recorder.py"), "--image",
                                   f"fsim-runtime-recorder:{tag}"], capture_output=True, text=True)
        if contract.returncode != 0:
            raise RuntimeError(f"контракт fsim-recorder не прошёл:\n{contract.stdout[-1500:]}")
        stage = "stream-test"
        stream = subprocess.run([sys.executable, str(ROOT / "tools" / "test_stream.py"), "--image",
                                 f"fsim-runtime-stream:{tag}"], capture_output=True, text=True)
        if stream.returncode != 0:
            raise RuntimeError(f"контракт fsim-stream не прошёл:\n{stream.stdout[-1500:]}")
        run(["docker", "tag", image, f"fsim-runtime:{target}-current"])
        run(["docker", "tag", f"fsim-runtime-recorder:{tag}", f"fsim-runtime-recorder:{target}-current"])
        run(["docker", "tag", f"fsim-runtime-stream:{tag}", f"fsim-runtime-stream:{target}-current"])
        if target != "dev":  # каталог берёт только stable: последний стабильный релиз, прошедший смоук
            run(["docker", "tag", image, "fsim-runtime:stable"])
            run(["docker", "tag", f"fsim-runtime-recorder:{tag}", "fsim-runtime-recorder:stable"])
            run(["docker", "tag", f"fsim-runtime-stream:{tag}", "fsim-runtime-stream:stable"])
        state[target] = {"commit": info["commit"], "build": info["build"], "status": "ok", "image": image,
                         "checked_at": time.time()}
        if push:
            stage = "push"
            publish(push, target, tag)
        save_openapi(work, target, info, state)
        print(f"[{target}] OK → fsim-runtime:{target}-current = {image}" + ("; stable обновлён" if target != "dev" else ""))
        return True
    except Exception as error:  # noqa: BLE001 — любая поломка конвейера = тревога, current не трогаем
        # «Сломанным» коммит считаем, только если не собралась или не прошла смоук его сборка;
        # сбой доставки исходников (сеть, GitHub) повторится при следующем запуске
        state[target] = {**known, "status": "failed", "checked_at": time.time(),
                         **({"failed_commit": info["commit"]} if stage in ("build", "recorder", "stream", "smoke", "recorder-test", "stream-test") else {})}
        path = alert(work, target, info, f"этап «{stage}»: {error}")
        print(f"[{target}] ПРОВАЛ на этапе {stage}, тревога: {path}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, default=Path.home() / ".cache" / "fsim-watch")
    parser.add_argument("--targets", default="dev,release")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--push", default=os.environ.get("FSIM_REGISTRY"), help="префикс реестра, например ghcr.io/truetug")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что нашлось в CI")
    args = parser.parse_args()

    args.work.mkdir(parents=True, exist_ok=True)
    state_path = args.work / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    ok = True
    for target in args.targets.split(","):
        # Коммит, который уже ломал сборку, повторно не гоняем — только новый или --force
        failed = state.get(target, {}).get("failed_commit")
        if failed and not args.force and not args.dry_run:
            try:
                if resolve(target)["commit"] == failed:
                    print(f"[{target}] коммит {failed} уже не прошёл — жду новый (или --force)")
                    ok = False
                    continue
            except Exception:  # noqa: BLE001
                pass
        ok = process(args.work, target, state, args.force, args.dry_run, args.push) and ok
        state_path.write_text(json.dumps(state, indent=1))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
