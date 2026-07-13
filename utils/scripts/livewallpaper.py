#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any


VALID_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm"}


class LiveWallpaperError(RuntimeError):
    pass


def xdg_path(environment: str, fallback: Path) -> Path:
    value = os.environ.get(environment, "").strip()
    return Path(value).expanduser() if value else fallback


def default_library_dir() -> Path:
    explicit = os.environ.get(
        "CAELESTIA_LIVE_WALLPAPERS_DIR",
        "",
    ).strip()

    if explicit:
        return Path(explicit).expanduser()

    wallpapers_dir = os.environ.get(
        "CAELESTIA_WALLPAPERS_DIR",
        "",
    ).strip()

    if wallpapers_dir:
        return Path(wallpapers_dir).expanduser() / "Animated"

    pictures = os.environ.get(
        "XDG_PICTURES_DIR",
        "",
    ).strip()

    pictures_dir = Path(pictures).expanduser() if pictures else Path.home() / "Pictures"

    return pictures_dir / "Wallpapers" / "Animated"


def default_cache_dir() -> Path:
    explicit = os.environ.get(
        "CAELESTIA_LIVE_WALLPAPER_CACHE_DIR",
        "",
    ).strip()

    if explicit:
        return Path(explicit).expanduser()

    cache_home = xdg_path(
        "XDG_CACHE_HOME",
        Path.home() / ".cache",
    )

    return cache_home / "caelestia" / "live-wallpapers"


def require_command(command: str) -> str:
    resolved = shutil.which(command)

    if not resolved:
        raise LiveWallpaperError(f"Required command not found: {command}")

    return resolved


def run_command(
    command: list[str],
    *,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=capture_output,
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "").strip()

        if details:
            raise LiveWallpaperError(
                f"Command failed: {' '.join(command)}\n{details}"
            ) from error

        raise LiveWallpaperError(f"Command failed: {' '.join(command)}") from error


def parse_rate(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0

    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def validate_video(path: Path) -> Path:
    resolved = path.expanduser().resolve()

    if not resolved.is_file():
        raise LiveWallpaperError(f"Video file not found: {resolved}")

    if resolved.suffix.lower() not in VALID_VIDEO_EXTENSIONS:
        supported = ", ".join(sorted(VALID_VIDEO_EXTENSIONS))

        raise LiveWallpaperError(
            f"Unsupported video extension: "
            f"{resolved.suffix or '<none>'}. "
            f"Supported extensions: {supported}"
        )

    return resolved


def probe_video(path: Path) -> dict[str, Any]:
    ffprobe = require_command("ffprobe")

    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size,bit_rate,format_name:"
                "stream=index,codec_type,codec_name,profile,"
                "pix_fmt,width,height,r_frame_rate,"
                "avg_frame_rate,bit_rate"
            ),
            "-of",
            "json",
            str(path),
        ]
    )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LiveWallpaperError(
            f"ffprobe returned invalid JSON for: {path}"
        ) from error

    streams = data.get("streams", [])

    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )

    if video_stream is None:
        raise LiveWallpaperError(f"No video stream found in: {path}")

    format_info = data.get("format", {})

    average_fps = parse_rate(video_stream.get("avg_frame_rate"))
    nominal_fps = parse_rate(video_stream.get("r_frame_rate"))

    return {
        "path": str(path),
        "container": format_info.get(
            "format_name",
            "",
        ),
        "duration": float(format_info.get("duration") or 0),
        "size": int(format_info.get("size") or path.stat().st_size),
        "bitRate": int(
            format_info.get("bit_rate") or video_stream.get("bit_rate") or 0
        ),
        "codec": video_stream.get("codec_name", ""),
        "profile": video_stream.get("profile", ""),
        "pixelFormat": video_stream.get("pix_fmt", ""),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": average_fps or nominal_fps,
        "nominalFps": nominal_fps,
        "hasAudio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def file_identity(path: Path) -> str:
    stat = path.stat()

    identity = f"{path.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"

    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def copy_to_library(
    source: Path,
    library_dir: Path,
) -> Path:
    library_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        if source.parent.samefile(library_dir):
            return source
    except FileNotFoundError:
        pass

    destination = library_dir / source.name

    if destination.exists():
        if destination.stat().st_size == source.stat().st_size:
            return destination.resolve()

        suffix = file_identity(source)[:8]
        destination = library_dir / f"{source.stem}-{suffix}{source.suffix.lower()}"

    temporary = destination.with_name(f".{destination.name}.tmp")

    shutil.copy2(source, temporary)
    temporary.replace(destination)

    return destination.resolve()


def extract_poster(
    video: Path,
    output: Path,
) -> None:
    ffmpeg = require_command("ffmpeg")

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".poster-",
        suffix=".jpg",
        dir=output.parent,
    )
    os.close(file_descriptor)

    temporary = Path(temporary_name)

    def extract(position: str) -> None:
        run_command(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                position,
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(temporary),
            ]
        )

    try:
        try:
            extract("0.5")
        except LiveWallpaperError:
            extract("0")

        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(
    output: Path,
    data: dict[str, Any],
) -> None:
    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-",
        suffix=".tmp",
        dir=output.parent,
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_video(
    source: Path,
    *,
    library_dir: Path,
    cache_dir: Path,
    keep_original: bool,
) -> dict[str, Any]:
    source = validate_video(source)

    video = source if keep_original else copy_to_library(source, library_dir)

    metadata = probe_video(video)
    identity = file_identity(video)

    video_cache = cache_dir / identity
    poster = video_cache / "poster.jpg"
    manifest = video_cache / "manifest.json"

    if not poster.exists():
        extract_poster(video, poster)

    manifest_data = {
        "version": 1,
        "id": identity,
        "video": str(video),
        "poster": str(poster),
        "activeProfile": "",
        "metadata": metadata,
    }

    write_json_atomic(
        manifest,
        manifest_data,
    )

    return {
        **manifest_data,
        "manifest": str(manifest),
        "library": str(library_dir),
        "cache": str(video_cache),
    }


def command_doctor(_: argparse.Namespace) -> int:
    result: dict[str, Any] = {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "library": str(default_library_dir()),
        "cache": str(default_cache_dir()),
    }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0 if result["ffmpeg"] and result["ffprobe"] else 1


def command_probe(arguments: argparse.Namespace) -> int:
    video = validate_video(arguments.video)

    print(
        json.dumps(
            probe_video(video),
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def command_prepare(arguments: argparse.Namespace) -> int:
    result = prepare_video(
        arguments.video,
        library_dir=arguments.library.expanduser(),
        cache_dir=arguments.cache.expanduser(),
        keep_original=arguments.keep_original,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="livewallpaper.py",
        description=("Prepare animated wallpapers for Caelestia."),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check runtime dependencies and paths.",
    )
    doctor_parser.set_defaults(handler=command_doctor)

    probe_parser = subparsers.add_parser(
        "probe",
        help="Inspect a video using ffprobe.",
    )
    probe_parser.add_argument(
        "video",
        type=Path,
    )
    probe_parser.set_defaults(handler=command_probe)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help=("Import a video, generate its poster and write its manifest."),
    )
    prepare_parser.add_argument(
        "video",
        type=Path,
    )
    prepare_parser.add_argument(
        "--library",
        type=Path,
        default=default_library_dir(),
    )
    prepare_parser.add_argument(
        "--cache",
        type=Path,
        default=default_cache_dir(),
    )
    prepare_parser.add_argument(
        "--keep-original",
        action="store_true",
        help=(
            "Reference the original file instead of "
            "copying it into the animated wallpaper library."
        ),
    )
    prepare_parser.set_defaults(handler=command_prepare)

    return parser


def main() -> int:
    parser = create_parser()
    arguments = parser.parse_args()

    try:
        return arguments.handler(arguments)
    except LiveWallpaperError as error:
        print(
            f"livewallpaper: {error}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
