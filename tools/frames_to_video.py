"""Кадры fsim (frames.log + PNG) → видео с реальными паузами и скриншоты ключевых экранов.

Результат — папка с video.mp4, video.webm, video.json и shot-0.png … shot-5.png: тот же состав, что
у бандла, который принимает decode_video_bundle каталога (app/infra/bar/video_bundle.py).

    frames_to_video.py <каталог кадров> <выходная папка> [--display front|back] [--from-ms N] [--to-ms N]
                       [--scale N] [--hold-ms N] [--shots N] [--max-fps N] [--max-frames N]
                       [--bundle файл.tar.gz]

Только стандартная библиотека и ffmpeg в PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

MIN_SHOT_MS = 700
DEFAULT_SCALE = {"front": 8, "back": 4}


def read_timeline(frames_dir: Path, display: str, from_ms: int, to_ms: int | None):
    """Кадры одного экрана: [(ms, путь)], по возрастанию времени, в окне [from_ms, to_ms]."""
    frames = []
    for line in (frames_dir / "frames.log").read_text().splitlines():
        stamp, name, number = line.split()
        if name != display:
            continue
        ms = int(stamp)
        if ms < from_ms or (to_ms is not None and ms > to_ms):
            continue
        path = frames_dir / f"{name}_{int(number):06d}.png"
        if path.exists():
            frames.append((ms, path))
    return frames


def thin(frames, hold_ms: int, max_fps: int, max_frames: int):
    """Прореживание: не чаще max_fps и не больше max_frames кадров на всё видео.

    Последний кадр каждой серии (за ним пауза ≥ шага) остаётся всегда: итоговое состояние экрана
    после анимации не теряется, а паузы между сериями сохраняют настоящую длительность.
    """
    window_ms = frames[-1][0] - frames[0][0] + hold_ms
    step = max(1000 / max_fps, window_ms / max_frames)
    kept = [frames[0]]
    for index in range(1, len(frames)):
        ms = frames[index][0]
        next_ms = frames[index + 1][0] if index + 1 < len(frames) else ms + hold_ms
        if ms - kept[-1][0] >= step or next_ms - ms >= step:
            kept.append(frames[index])
    return kept


def pick_shots(frames, hold_ms: int, count: int):
    """Скриншоты: разные по содержимому кадры, которые держались на экране ≥ MIN_SHOT_MS.

    Из подходящих берём равномерно по времени — так в набор попадают и меню, и приложение.
    """
    stable, seen = [], set()
    for index, (ms, path) in enumerate(frames):
        until = frames[index + 1][0] if index + 1 < len(frames) else ms + hold_ms
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if until - ms >= MIN_SHOT_MS and digest not in seen:
            seen.add(digest)
            stable.append(path)
    if len(stable) <= count:
        return stable
    step = (len(stable) - 1) / (count - 1) if count > 1 else 0
    return [stable[round(i * step)] for i in range(count)]


def encode(frames, out_dir: Path, scale: int, hold_ms: int):
    """concat-список с длительностями → mp4 (H.264) и webm (VP9), пиксели без сглаживания."""
    concat = out_dir / "frames.txt"
    lines = []
    for index, (ms, path) in enumerate(frames):
        until = frames[index + 1][0] if index + 1 < len(frames) else ms + hold_ms
        lines.append(f"file '{path.resolve()}'")
        lines.append(f"duration {max(until - ms, 1) / 1000:.3f}")
    # concat-демультиплексор игнорирует длительность последнего файла — повторяем его
    lines.append(f"file '{frames[-1][1].resolve()}'")
    concat.write_text("\n".join(lines) + "\n")
    # yuv420p требует чётных сторон; neighbor — чёткие пиксели LED-матрицы
    vf = f"scale=iw*{scale}:ih*{scale}:flags=neighbor,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p"
    base = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-vf", vf, "-fps_mode", "vfr"]
    subprocess.run([*base, "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart",
                    str(out_dir / "video.mp4")], check=True)
    subprocess.run([*base, "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0",
                    str(out_dir / "video.webm")], check=True)
    concat.unlink()


def build_video(frames_dir: Path, out_dir: Path, display: str = "front", from_ms: int = 0, to_ms: int | None = None,
                scale: int | None = None, hold_ms: int = 1500, shots: int = 6, max_fps: int = 15,
                max_frames: int = 290, bundle: Path | None = None, duration_s: int | None = None,
                extra: dict | None = None) -> dict:
    """Видео, скриншоты и (по желанию) бандл из кадров fsim. Возвращает содержимое video.json.

    duration_s — довести видео до этой длительности: последний кадр держится дольше. В video.json
    пишется именно это значение (каталог сверяет его со сценарием).
    extra — дополнительные ключи video.json (например, таймлайн): декодер каталога лишние ключи
    пропускает, а отдельные файлы в бандле он отвергает.
    """
    frames = read_timeline(frames_dir, display, from_ms, to_ms)
    if not frames:
        raise ValueError("нет кадров в выбранном окне")
    if duration_s is not None:
        hold_ms = max(hold_ms, duration_s * 1000 - (frames[-1][0] - frames[0][0]))
    frames = thin(frames, hold_ms, max_fps, max_frames)
    out_dir.mkdir(parents=True, exist_ok=True)
    encode(frames, out_dir, scale or DEFAULT_SCALE[display], hold_ms)
    picked = pick_shots(frames, hold_ms, min(shots, 6))
    for index, path in enumerate(picked):
        (out_dir / f"shot-{index}.png").write_bytes(path.read_bytes())
    duration_ms = frames[-1][0] - frames[0][0] + hold_ms
    meta = {
        "display": display,
        "duration_s": duration_s if duration_s is not None else round(duration_ms / 1000),
        "frame_count": len(frames),
        "screenshots": [f"shot-{index}.png" for index in range(len(picked))],
        **(extra or {}),
    }
    (out_dir / "video.json").write_text(json.dumps(meta))
    if bundle:
        # tarfile, а не системный tar: macOS tar добавляет ._* и ломает проверку состава бандла
        with tarfile.open(bundle, "w:gz") as archive:
            for name in ["video.mp4", "video.webm", "video.json", *(f"shot-{i}.png" for i in range(len(picked)))]:
                info = archive.gettarinfo(out_dir / name, arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                with open(out_dir / name, "rb") as stream:
                    archive.addfile(info, stream)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--display", choices=["front", "back"], default="front")
    parser.add_argument("--from-ms", type=int, default=0)
    parser.add_argument("--to-ms", type=int)
    parser.add_argument("--scale", type=int)
    parser.add_argument("--hold-ms", type=int, default=1500, help="сколько держать последний кадр")
    parser.add_argument("--shots", type=int, default=6)
    parser.add_argument("--max-fps", type=int, default=15)
    parser.add_argument("--max-frames", type=int, default=290, help="потолок каталога — 300 кадров")
    parser.add_argument("--duration-s", type=int, help="довести видео до этой длительности")
    parser.add_argument("--bundle", type=Path, help="ещё и упаковать результат в tar.gz для каталога")
    args = parser.parse_args()
    try:
        meta = build_video(args.frames_dir, args.out_dir, args.display, args.from_ms, args.to_ms, args.scale,
                           args.hold_ms, args.shots, args.max_fps, args.max_frames, args.bundle, args.duration_s)
    except ValueError as error:
        sys.exit(str(error))
    print(f"{meta['frame_count']} кадров, {meta['duration_s']} с, скриншотов {len(meta['screenshots'])} → {args.out_dir}")


if __name__ == "__main__":
    main()
