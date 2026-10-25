"""Сгенерировать applications.c и список исходников сервисов для fsim из application.fam прошивки.

То же, что делает fbt (appmanifest), но только для сервисов из targets/fsim/services.txt.
.fam разбираются через AST, без выполнения кода.
"""

import ast
import json
import os
import sys
from pathlib import Path

FW, OUT, WANTED = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])


# fsim повторяет бар f22 (U5). Один appid бывает объявлен для разных целей (input — для U5 и SiWG917)
TARGET = "f22"

OPERATORS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b}


def value(node):
    """Литерал, атрибут (FlipperAppType.SERVICE → SERVICE) или арифметика вроде 1024 * 8."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
        return OPERATORS[type(node.op)](value(node.left), value(node.right))
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


apps = {}
def find_fams(root: Path):
    """application.fam во всём дереве; ссылки на каталоги раскрываются (оверлей исходников для WASM)."""
    found = []
    for directory, _, files in os.walk(root, followlinks=True):
        if "application.fam" in files:
            found.append(Path(directory) / "application.fam")
    return sorted(found)


for fam in find_fams(FW / "applications"):
    for node in ast.walk(ast.parse(fam.read_text())):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "App":
            app = {k.arg: value(k.value) for k in node.keywords}
            app["_dir"] = fam.parent
            targets = app.get("targets") or ["all"]
            if "all" in targets or TARGET in targets:
                apps[app["appid"]] = app

# Строка «appid» — сервис прошивки как есть; «appid !replace» — метаданные из .fam, исходники — fsim;
# «appid !exclude=a.c,dir/b.c» — как есть, кроме перечисленных файлов (их API — заглушки fsim).
entries = [
    line.split("#")[0].split()
    for line in WANTED.read_text().splitlines()
    if line.split("#")[0].strip()
]
wanted = [entry[0] for entry in entries]
replaced = {entry[0] for entry in entries if "!replace" in entry[1:]}
excludes = {
    entry[0]: option.split("=", 1)[1].split(",")
    for entry in entries
    for option in entry[1:]
    if option.startswith("!exclude=")
}
missing = [appid for appid in wanted if appid not in apps]
if missing:
    sys.exit(f"unknown appids: {missing}")

chosen = sorted((apps[appid] for appid in wanted), key=lambda app: app.get("order", 0))
services = [app for app in chosen if app["apptype"] == "SERVICE"]
hooks = [app for app in chosen if app["apptype"] == "STARTUP"]
system_apps = [app for app in chosen if app["apptype"] == "SYSTEM"]
user_apps = [app for app in chosen if app["apptype"] == "APP"]

lines = ['#include "applications.h"', "", 'const char* FLIPPER_AUTORUN_APP_NAME = "";', ""]
for app in services:
    lines.append(f"extern int32_t {app['entry_point']}(void* p);")
for app in hooks:
    lines.append(f"extern void {app['entry_point']}(void);")
for app in system_apps + user_apps:
    lines.append(f"extern int32_t {app['entry_point']}(void* p);")
lines += ["", "const FlipperInternalApplication FLIPPER_SERVICES[] = {"]
for app in services:
    lines.append(
        f'    {{.app = {app["entry_point"]}, .name = "{app.get("name", app["appid"])}", '
        f'.appid = "{app["appid"]}", .stack_size = {app.get("stack_size", 2048)}, '
        ".flags = FlipperInternalApplicationFlagDefault},"
    )
lines += ["};", "const size_t FLIPPER_SERVICES_COUNT = COUNT_OF(FLIPPER_SERVICES);", ""]
lines.append("const FlipperInternalOnStartHook FLIPPER_ON_SYSTEM_START[] = {")
for app in hooks:
    lines.append(f"    {{.callback = {app['entry_point']}, .stack_size = {app.get('stack_size', 2048)}}},")
lines += ["};", "const size_t FLIPPER_ON_SYSTEM_START_COUNT = COUNT_OF(FLIPPER_ON_SYSTEM_START);", ""]
lines.append("const FlipperInternalApplication FLIPPER_SYSTEM_APPS[] = {")
for app in system_apps:
    lines.append(
        f'    {{.app = {app["entry_point"]}, .name = "{app.get("name", app["appid"])}", '
        f'.appid = "{app["appid"]}", .stack_size = {app.get("stack_size", 2048)}, '
        ".flags = FlipperInternalApplicationFlagDefault},"
    )
lines += ["};", "const size_t FLIPPER_SYSTEM_APPS_COUNT = COUNT_OF(FLIPPER_SYSTEM_APPS);", ""]
lines.append("const FlipperInternalApplication FLIPPER_APPS[] = {")
for app in user_apps:
    lines.append(
        f'    {{.app = {app["entry_point"]}, .name = "{app.get("name", app["appid"])}", '
        f'.appid = "{app["appid"]}", .stack_size = {app.get("stack_size", 2048)}, '
        ".flags = FlipperInternalApplicationFlagDefault},"
    )
lines += ["};", "const size_t FLIPPER_APPS_COUNT = COUNT_OF(FLIPPER_APPS);", ""]
for name in ("FLIPPER_DEBUG_APPS", "FLIPPER_SETTINGS_APPS"):
    lines += [f"const FlipperInternalApplication {name}[] = {{}};", f"const size_t {name}_COUNT = 0;"]
lines += [
    "const FlipperInternalCommandApplication FLIPPER_CLI_COMMANDS[] = {};",
    "const size_t FLIPPER_CLI_COMMANDS_COUNT = 0;",
    "const FlipperExternalApplication FLIPPER_EXTERNAL_APPS[] = {};",
    "const size_t FLIPPER_EXTERNAL_APPS_COUNT = 0;",
]
OUT.mkdir(parents=True, exist_ok=True)
def write_if_changed(path, text):
    """Не трогать файл без изменений — иначе ninja пересоберёт зависимых."""
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


write_if_changed(OUT / "applications.c", "\n".join(lines) + "\n")

sources, defines = [], []
for app in chosen:
    if app["appid"] in replaced:
        continue
    base = app["_dir"]
    patterns = app.get("sources") or ["*.c"]
    for pattern in patterns:
        excluded = pattern.startswith("!")
        # fbt ищет исходники приложения рекурсивно (GlobRecursive)
        matched = sorted(str(path) for path in base.rglob(pattern.lstrip("!")))
        if excluded:
            sources = [source for source in sources if source not in matched]
        else:
            sources += matched
    for name in excludes.get(app["appid"], []):
        path = str(base / name)
        if path not in sources:
            sys.exit(f"{app['appid']}: !exclude={name} — такого исходника нет")
        sources.remove(path)
    defines += app.get("cdefines", [])
write_if_changed(OUT / "services.json", json.dumps({"sources": sources, "defines": defines}, indent=1))
print(f"services={len(services)} hooks={len(hooks)} system_apps={len(system_apps)} apps={len(user_apps)} sources={len(sources)}")
