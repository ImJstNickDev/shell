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
import time
from pathlib import Path
from typing import Any


VALID_DECODERS = {
    "auto",
    "vaapi",
    "nvdec",
    "software",
}

VALID_FIT_MODES = {
    "cover",
    "contain",
}

VALID_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".webm",
}

VAAPI_ELEMENTS = (
    "vah264dec",
    "vah265dec",
    "vaav1dec",
    "vavp8dec",
    "vavp9dec",
    "vampeg2dec",
    "vajpegdec",
)

NVDEC_ELEMENTS = (
    "nvh264dec",
    "nvh265dec",
    "nvav1dec",
    "nvmpeg2videodec",
    "nvjpegdec",
)

SOFTWARE_ELEMENTS = (
    "avdec_h264",
    "avdec_h265",
    "avdec_av1",
    "avdec_vp8",
    "avdec_vp9",
    "avdec_mpeg2video",
)


class ControllerError(RuntimeError):
    pass


SCRIPT_PATH = Path(__file__).resolve()
SHELL_ROOT = SCRIPT_PATH.parents[2]

RENDERER_DIR = Path(
    os.environ.get(
        "CAELESTIA_LIVE_WALLPAPER_RENDERER_DIR",
        SHELL_ROOT / "livewallpaper-shell",
    )
).expanduser()

STATE_HOME = Path(
    os.environ.get(
        "XDG_STATE_HOME",
        Path.home() / ".local" / "state",
    )
).expanduser()

STATE_DIR = Path(
    os.environ.get(
        "CAELESTIA_LIVE_WALLPAPER_STATE_DIR",
        STATE_HOME / "caelestia" / "live-wallpaper",
    )
).expanduser()

STATE_FILE = Path(
    os.environ.get(
        "CAELESTIA_LIVE_WALLPAPER_STATE_FILE",
        STATE_DIR / "renderer.json",
    )
).expanduser()

CAPABILITIES_FILE = Path(
    os.environ.get(
        "CAELESTIA_LIVE_WALLPAPER_CAPABILITIES_FILE",
        STATE_DIR / "capabilities.json",
    )
).expanduser()

CAPABILITIES_CACHE_VERSION = 1

IPC_TARGET = "liveWallpaperRenderer"

MAIN_SHELL_CONFIG = os.environ.get(
    "CAELESTIA_LIVE_WALLPAPER_MAIN_CONFIG",
    "caelestia",
)
MAIN_IPC_TARGET = "liveWallpaper"

RENDERER_IPC_TIMEOUT = 4.0
RENDERER_READY_TIMEOUT = 15.0
RENDERER_POLL_INTERVAL = 0.05

_CAPABILITIES: dict[str, Any] | None = None


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


def require_command(command: str) -> str:
    resolved = shutil.which(command)

    if not resolved:
        raise ControllerError(f"Required command not found: {command}")

    return resolved


def run_command(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=environment,
    )

    if check and result.returncode != 0:
        details = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit status {result.returncode}"
        )

        raise ControllerError(f"Command failed: {' '.join(command)}\n{details}")

    return result


def normalize_decoder(value: str | None) -> str:
    clean = str(value or "auto").strip().lower()

    aliases = {
        "intel-vaapi": "vaapi",
        "nvidia-nvdec": "nvdec",
    }

    clean = aliases.get(clean, clean)

    if clean not in VALID_DECODERS:
        raise ControllerError(f"Invalid decoder mode: {value}")

    return clean


def normalize_fit(value: str | None) -> str:
    clean = str(value or "cover").strip().lower()

    if clean not in VALID_FIT_MODES:
        raise ControllerError(f"Invalid fit mode: {value}")

    return clean


def validate_video(path: Path) -> Path:
    resolved = path.expanduser().resolve()

    if not resolved.is_file():
        raise ControllerError(f"Video file not found: {resolved}")

    if resolved.suffix.lower() not in VALID_VIDEO_EXTENSIONS:
        raise ControllerError(
            f"Unsupported video extension: {resolved.suffix or '<none>'}"
        )

    return resolved


def atomic_write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".json",
        dir=path.parent,
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(
            descriptor,
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

        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):
        return {}


def read_state() -> dict[str, Any]:
    return read_json_file(STATE_FILE)


def write_state(data: dict[str, Any]) -> None:
    atomic_write_json(STATE_FILE, data)


def path_signature(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None

    try:
        stat = path.stat()
    except OSError:
        return {
            "path": str(path),
            "missing": True,
        }

    return {
        "path": str(path),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "inode": stat.st_ino,
    }


def plugin_directories() -> list[Path]:
    directories: list[Path] = []

    custom_paths = os.environ.get("GST_PLUGIN_PATH", "")

    for raw_path in custom_paths.split(os.pathsep):
        if raw_path:
            directories.append(Path(raw_path).expanduser())

    directories.extend(
        [
            Path("/usr/lib/gstreamer-1.0"),
            Path("/usr/lib64/gstreamer-1.0"),
            Path("/usr/local/lib/gstreamer-1.0"),
            Path("/usr/local/lib64/gstreamer-1.0"),
        ]
    )

    unique: list[Path] = []
    seen: set[str] = set()

    for directory in directories:
        key = str(directory)

        if key in seen:
            continue

        seen.add(key)

        if directory.is_dir():
            unique.append(directory)

    return unique


def directory_signature(directory: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []

    try:
        children = sorted(
            directory.iterdir(),
            key=lambda path: path.name,
        )
    except OSError:
        children = []

    for child in children:
        if not child.is_file():
            continue

        try:
            stat = child.stat()
        except OSError:
            continue

        entries.append(
            {
                "name": child.name,
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )

    return {
        "path": str(directory),
        "entries": entries,
    }


def dri_signature() -> list[dict[str, Any]]:
    dri_path = Path("/dev/dri")

    if not dri_path.is_dir():
        return []

    devices: list[dict[str, Any]] = []

    for device in sorted(dri_path.glob("renderD*")):
        try:
            stat = device.stat()
        except OSError:
            continue

        devices.append(
            {
                "path": str(device),
                "rdev": stat.st_rdev,
                "mode": stat.st_mode,
            }
        )

    return devices


def capabilities_fingerprint() -> str:
    gst_inspect = shutil.which("gst-inspect-1.0")
    vainfo = shutil.which("vainfo")

    payload = {
        "version": CAPABILITIES_CACHE_VERSION,
        "gstInspect": path_signature(
            Path(gst_inspect) if gst_inspect else None
        ),
        "vainfo": path_signature(
            Path(vainfo) if vainfo else None
        ),
        "pluginDirectories": [
            directory_signature(directory)
            for directory in plugin_directories()
        ],
        "driDevices": dri_signature(),
        "gstPluginPath": os.environ.get("GST_PLUGIN_PATH", ""),
    }

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def has_gstreamer_element(
    inspector: str,
    element: str,
) -> bool:
    result = run_command(
        [inspector, element],
        check=False,
    )

    return result.returncode == 0


def discover_elements(
    inspector: str | None,
    elements: tuple[str, ...],
) -> list[str]:
    if not inspector:
        return []

    return [
        element
        for element in elements
        if has_gstreamer_element(inspector, element)
    ]


def discover_vaapi_device(
    vainfo: str | None,
) -> str | None:
    if not vainfo:
        return None

    dri_path = Path("/dev/dri")

    if not dri_path.is_dir():
        return None

    for device in sorted(dri_path.glob("renderD*")):
        result = run_command(
            [
                vainfo,
                "--display",
                "drm",
                "--device",
                str(device),
            ],
            check=False,
        )

        if result.returncode == 0:
            return str(device)

    return None


def discover_capabilities(
    fingerprint: str,
) -> dict[str, Any]:
    inspector = shutil.which("gst-inspect-1.0")
    vainfo = shutil.which("vainfo")

    return {
        "version": CAPABILITIES_CACHE_VERSION,
        "fingerprint": fingerprint,
        "generatedAt": int(time.time()),
        "gstInspect": inspector,
        "vainfo": vainfo,
        "elements": {
            "vaapi": discover_elements(
                inspector,
                VAAPI_ELEMENTS,
            ),
            "nvdec": discover_elements(
                inspector,
                NVDEC_ELEMENTS,
            ),
            "software": discover_elements(
                inspector,
                SOFTWARE_ELEMENTS,
            ),
        },
        "vaapiDevice": discover_vaapi_device(vainfo),
    }


def get_capabilities(
    *,
    refresh: bool = False,
) -> tuple[dict[str, Any], bool]:
    global _CAPABILITIES

    fingerprint = capabilities_fingerprint()

    if (
        not refresh
        and _CAPABILITIES is not None
        and _CAPABILITIES.get("fingerprint") == fingerprint
    ):
        return _CAPABILITIES, True

    if not refresh:
        cached = read_json_file(CAPABILITIES_FILE)

        if (
            cached.get("version") == CAPABILITIES_CACHE_VERSION
            and cached.get("fingerprint") == fingerprint
        ):
            _CAPABILITIES = cached
            return cached, True

    capabilities = discover_capabilities(fingerprint)
    atomic_write_json(CAPABILITIES_FILE, capabilities)

    _CAPABILITIES = capabilities
    return capabilities, False


def available_elements(
    capabilities: dict[str, Any],
    kind: str,
) -> list[str]:
    elements = capabilities.get("elements", {}).get(kind, [])

    if not isinstance(elements, list):
        return []

    return [
        str(element)
        for element in elements
        if isinstance(element, str)
    ]


def find_vaapi_device(
    capabilities: dict[str, Any],
) -> str | None:
    device = capabilities.get("vaapiDevice")
    return str(device) if device else None


def vaapi_available(
    capabilities: dict[str, Any],
) -> bool:
    return bool(
        available_elements(capabilities, "vaapi")
        and find_vaapi_device(capabilities)
    )


def nvdec_available(
    capabilities: dict[str, Any],
) -> bool:
    return bool(
        available_elements(capabilities, "nvdec")
    )


def software_available(
    capabilities: dict[str, Any],
) -> bool:
    return bool(
        available_elements(capabilities, "software")
    )


def resolve_decoder(
    requested: str,
    capabilities: dict[str, Any],
) -> str:
    requested = normalize_decoder(requested)

    if requested == "auto":
        if vaapi_available(capabilities):
            return "vaapi"

        if nvdec_available(capabilities):
            return "nvdec"

        if software_available(capabilities):
            return "software"

        raise ControllerError("No usable GStreamer video decoder was found")

    if requested == "vaapi" and not vaapi_available(capabilities):
        raise ControllerError("VA-API was requested but is not available")

    if requested == "nvdec" and not nvdec_available(capabilities):
        raise ControllerError("NVDEC was requested but is not available")

    if requested == "software" and not software_available(capabilities):
        raise ControllerError(
            "Software decoding was requested but no supported decoder was found"
        )

    return requested


def build_feature_ranks(
    decoder: str,
    capabilities: dict[str, Any],
) -> str:
    entries: list[str] = []

    def add(
        kind: str,
        rank: str,
    ) -> None:
        for element in available_elements(capabilities, kind):
            entries.append(f"{element}:{rank}")

    if decoder == "vaapi":
        add("vaapi", "MAX")
        add("nvdec", "NONE")

    elif decoder == "nvdec":
        add("nvdec", "MAX")
        add("vaapi", "NONE")

    elif decoder == "software":
        add("vaapi", "NONE")
        add("nvdec", "NONE")
        add("software", "MAX")

    return ",".join(entries)


def ipc_command(
    function: str,
    *arguments: str,
) -> list[str]:
    quickshell = require_command("qs")

    return [
        quickshell,
        "ipc",
        "-p",
        str(RENDERER_DIR),
        "call",
        IPC_TARGET,
        function,
        *arguments,
    ]


def ipc_call(
    function: str,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ipc_command(function, *arguments),
        check=check,
    )


def main_ipc_command(
    function: str,
    *arguments: str,
) -> list[str]:
    quickshell = require_command("qs")

    return [
        quickshell,
        "ipc",
        "-c",
        MAIN_SHELL_CONFIG,
        "call",
        MAIN_IPC_TARGET,
        function,
        *arguments,
    ]


def main_ipc_call(
    function: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """Best-effort IPC call to the main Caelestia shell."""
    return run_command(
        main_ipc_command(function, *arguments),
        check=False,
    )


def sync_main_started(
    state: dict[str, Any],
) -> bool:
    """Adopt a ready renderer in the main shell, when it is available."""
    if state.get("testMode") is True:
        return False

    result = main_ipc_call(
        "syncStarted",
        str(state.get("video") or ""),
        str(state.get("requestedDecoder") or "auto"),
        str(state.get("resolvedDecoder") or ""),
        str(state.get("fitMode") or "cover"),
        str(state.get("vaapiDevice") or ""),
    )

    return result.returncode == 0


def sync_main_stopped() -> bool:
    """Clear the main shell state after an external clear or stop."""
    result = main_ipc_call("syncStopped")
    return result.returncode == 0


def sync_main_paused(paused: bool) -> bool:
    """Mirror an externally requested pause state in the main shell."""
    result = main_ipc_call(
        "syncPaused",
        "true" if paused else "false",
    )

    return result.returncode == 0


def parse_json_output(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            return data

    return None


def renderer_status() -> dict[str, Any] | None:
    result = ipc_call(
        "status",
        check=False,
    )

    if result.returncode != 0:
        return None

    return parse_json_output(result.stdout)


def wait_for_renderer(
    *,
    running: bool,
    timeout: float = RENDERER_IPC_TIMEOUT,
) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        is_running = renderer_status() is not None

        if is_running == running:
            return True

        time.sleep(RENDERER_POLL_INTERVAL)

    return False


def wait_for_renderer_ready(
    timeout: float = RENDERER_READY_TIMEOUT,
) -> dict[str, Any] | None:
    """
    Wait until every renderer window has presented its first valid frame.

    IPC availability only means that the Quickshell process exists. The
    wallpaper remains static until the renderer explicitly reports ready=true.
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        status = renderer_status()

        if (
            status is not None
            and status.get("active") is True
            and status.get("ready") is True
        ):
            return status

        time.sleep(RENDERER_POLL_INTERVAL)

    return None


def stop_renderer() -> bool:
    if renderer_status() is None:
        return False

    ipc_call(
        "quit",
        check=False,
    )

    if not wait_for_renderer(running=False):
        raise ControllerError("The renderer did not stop correctly")

    return True


def launch_renderer(
    video: Path,
    *,
    fit_mode: str,
    requested_decoder: str,
    resolved_decoder: str,
    test_mode: bool,
    feature_ranks: str,
) -> None:
    if not (RENDERER_DIR / "shell.qml").is_file():
        raise ControllerError(f"Renderer shell not found: {RENDERER_DIR}")

    quickshell = require_command("qs")
    require_command("gst-inspect-1.0")

    environment = os.environ.copy()

    environment["QT_MEDIA_BACKEND"] = "gstreamer"
    environment["GST_PLUGIN_FEATURE_RANK"] = feature_ranks

    environment["CAELESTIA_LIVE_WALLPAPER_VIDEO"] = str(video)
    environment["CAELESTIA_LIVE_WALLPAPER_FIT"] = fit_mode
    environment["CAELESTIA_LIVE_WALLPAPER_DECODER"] = requested_decoder
    environment["CAELESTIA_LIVE_WALLPAPER_RESOLVED_DECODER"] = resolved_decoder
    environment["CAELESTIA_LIVE_WALLPAPER_TEST"] = "1" if test_mode else "0"

    result = run_command(
        [
            quickshell,
            "-n",
            "-d",
            "-p",
            str(RENDERER_DIR),
        ],
        environment=environment,
        check=False,
    )

    if result.returncode != 0:
        details = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit status {result.returncode}"
        )

        raise ControllerError(f"Could not start the renderer:\n{details}")

    if not wait_for_renderer(running=True):
        raise ControllerError(
            "The renderer started but its IPC target did not become available"
        )


def apply_video(
    video: Path,
    *,
    fit_mode: str,
    requested_decoder: str,
    test_mode: bool,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    timings: dict[str, float] = {}

    phase_started = time.perf_counter()

    video = validate_video(video)
    fit_mode = normalize_fit(fit_mode)
    requested_decoder = normalize_decoder(requested_decoder)

    timings["validation"] = elapsed_ms(phase_started)

    phase_started = time.perf_counter()

    capabilities, cache_hit = get_capabilities()
    resolved_decoder = resolve_decoder(
        requested_decoder,
        capabilities,
    )
    feature_ranks = build_feature_ranks(
        resolved_decoder,
        capabilities,
    )

    timings["capabilityDiscovery"] = elapsed_ms(phase_started)

    phase_started = time.perf_counter()

    previous_state = read_state()
    status = renderer_status()

    timings["rendererProbe"] = elapsed_ms(phase_started)

    can_reuse_renderer = (
        status is not None
        and previous_state.get("resolvedDecoder") == resolved_decoder
        and previous_state.get("testMode") == test_mode
    )

    timings["rendererSet"] = 0.0
    timings["rendererStop"] = 0.0
    timings["rendererLaunch"] = 0.0

    if can_reuse_renderer:
        phase_started = time.perf_counter()

        result = ipc_call(
            "set",
            str(video),
            fit_mode,
            check=False,
        )

        timings["rendererSet"] = elapsed_ms(phase_started)

        if result.returncode != 0:
            can_reuse_renderer = False

    if not can_reuse_renderer:
        phase_started = time.perf_counter()

        stop_renderer()

        timings["rendererStop"] = elapsed_ms(phase_started)

        phase_started = time.perf_counter()

        launch_renderer(
            video,
            fit_mode=fit_mode,
            requested_decoder=requested_decoder,
            resolved_decoder=resolved_decoder,
            test_mode=test_mode,
            feature_ranks=feature_ranks,
        )

        timings["rendererLaunch"] = elapsed_ms(phase_started)

    phase_started = time.perf_counter()

    ready_status = wait_for_renderer_ready()

    timings["rendererReady"] = elapsed_ms(phase_started)

    if ready_status is None:
        last_status = renderer_status()

        try:
            stop_renderer()
        except ControllerError as error:
            stop_error = str(error)
        else:
            stop_error = ""

        details = (
            json.dumps(
                last_status,
                ensure_ascii=False,
            )
            if last_status is not None
            else "renderer IPC unavailable"
        )

        if stop_error:
            details += f"; cleanup failed: {stop_error}"

        raise ControllerError(
            "The renderer did not present its first video frame "
            f"within {RENDERER_READY_TIMEOUT:.1f} seconds "
            f"(last status: {details})"
        )

    state = {
        "video": str(video),
        "fitMode": fit_mode,
        "requestedDecoder": requested_decoder,
        "resolvedDecoder": resolved_decoder,
        "testMode": test_mode,
        "vaapiDevice": (
            find_vaapi_device(capabilities)
            if resolved_decoder == "vaapi"
            else None
        ),
    }

    phase_started = time.perf_counter()

    write_state(state)
    main_shell_synced = sync_main_started(state)

    timings["stateAndMainSync"] = elapsed_ms(phase_started)
    timings["total"] = elapsed_ms(total_started)

    return {
        "running": True,
        "ready": True,
        "reusedRenderer": can_reuse_renderer,
        "mainShellSynced": main_shell_synced,
        "capabilitiesCacheHit": cache_hit,
        "timingsMs": timings,
        **state,
        "renderer": ready_status,
    }


def command_doctor(
    arguments: argparse.Namespace,
) -> int:
    started = time.perf_counter()

    capabilities, cache_hit = get_capabilities(
        refresh=arguments.refresh,
    )

    result = {
        "qs": shutil.which("qs"),
        "gstInspect": capabilities.get("gstInspect"),
        "vainfo": capabilities.get("vainfo"),
        "rendererDirectory": str(RENDERER_DIR),
        "rendererExists": (RENDERER_DIR / "shell.qml").is_file(),
        "capabilitiesCache": {
            "file": str(CAPABILITIES_FILE),
            "hit": cache_hit,
            "refreshed": arguments.refresh,
            "generatedAt": capabilities.get("generatedAt"),
            "lookupMs": elapsed_ms(started),
        },
        "vaapi": {
            "available": vaapi_available(capabilities),
            "device": find_vaapi_device(capabilities),
            "elements": available_elements(capabilities, "vaapi"),
        },
        "nvdec": {
            "available": nvdec_available(capabilities),
            "elements": available_elements(capabilities, "nvdec"),
        },
        "software": {
            "available": software_available(capabilities),
            "elements": available_elements(capabilities, "software"),
        },
    }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    required_ok = bool(
        result["qs"]
        and result["gstInspect"]
        and result["rendererExists"]
    )

    return 0 if required_ok else 1


def command_set(
    arguments: argparse.Namespace,
) -> int:
    previous_state = read_state()

    decoder = arguments.decoder or previous_state.get(
        "requestedDecoder",
        "auto",
    )

    fit_mode = arguments.fit or previous_state.get(
        "fitMode",
        "cover",
    )

    result = apply_video(
        arguments.video,
        fit_mode=fit_mode,
        requested_decoder=decoder,
        test_mode=arguments.test_mode,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def command_status(
    _: argparse.Namespace,
) -> int:
    renderer = renderer_status()

    result = {
        "running": renderer is not None,
        "state": read_state(),
        "renderer": renderer,
    }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def command_ipc(
    function: str,
) -> int:
    if renderer_status() is None:
        raise ControllerError("The live wallpaper renderer is not running")

    result = ipc_call(function)

    if result.stdout.strip():
        print(result.stdout.strip())

    return 0


def command_pause(
    _: argparse.Namespace,
) -> int:
    result = command_ipc("pause")
    sync_main_paused(True)
    return result


def command_resume(
    _: argparse.Namespace,
) -> int:
    result = command_ipc("resume")
    sync_main_paused(False)
    return result


def command_toggle(
    _: argparse.Namespace,
) -> int:
    result = command_ipc("togglePause")
    status = renderer_status()

    if status is not None:
        sync_main_paused(status.get("paused") is True)

    return result


def command_clear(
    _: argparse.Namespace,
) -> int:
    result = command_ipc("clear")
    sync_main_stopped()
    return result


def command_stop(
    _: argparse.Namespace,
) -> int:
    stopped = stop_renderer()
    main_shell_synced = sync_main_stopped()

    print(
        json.dumps(
            {
                "stopped": stopped,
                "mainShellSynced": main_shell_synced,
            }
        )
    )

    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="livewallpaperctl.py",
        description=(
            "Control the isolated Caelestia live wallpaper renderer."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect and cache available video decoders.",
    )
    doctor_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore and rebuild the decoder capability cache.",
    )
    doctor_parser.set_defaults(handler=command_doctor)

    set_parser = subparsers.add_parser(
        "set",
        help="Start the renderer or change the current video.",
    )
    set_parser.add_argument(
        "video",
        type=Path,
    )
    set_parser.add_argument(
        "--decoder",
        choices=sorted(VALID_DECODERS),
    )
    set_parser.add_argument(
        "--fit",
        choices=sorted(VALID_FIT_MODES),
    )
    set_parser.add_argument(
        "--test",
        dest="test_mode",
        action="store_true",
        help=(
            "Render on the Bottom layer for testing above "
            "Caelestia's static wallpaper."
        ),
    )
    set_parser.set_defaults(handler=command_set)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(handler=command_status)

    pause_parser = subparsers.add_parser("pause")
    pause_parser.set_defaults(handler=command_pause)

    resume_parser = subparsers.add_parser("resume")
    resume_parser.set_defaults(handler=command_resume)

    toggle_parser = subparsers.add_parser("toggle")
    toggle_parser.set_defaults(handler=command_toggle)

    clear_parser = subparsers.add_parser("clear")
    clear_parser.set_defaults(handler=command_clear)

    stop_parser = subparsers.add_parser("stop")
    stop_parser.set_defaults(handler=command_stop)

    return parser


def main() -> int:
    parser = create_parser()
    arguments = parser.parse_args()

    try:
        return arguments.handler(arguments)
    except ControllerError as error:
        print(
            f"livewallpaperctl: {error}",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
