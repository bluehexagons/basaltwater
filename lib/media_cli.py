"""Bounded offline media inspection for Debian; no desktop or capture required."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

from desktop.audio_analysis import parse_analysis
from lib.atomic_io import write_json_atomic
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path


class UnsupportedMedia(ValueError):
    """Input is outside the qualified self-contained media contract."""


def snapshot(source: str, max_bytes: int) -> bytes:
    """Take one bounded immutable snapshot, rejecting special files and changes."""
    validate_filesystem_path(source, must_exist=True)
    fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
            raise UnsupportedMedia("Input must be a nonempty regular file within the byte limit")
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after) or len(data) != before.st_size:
        raise ValueError("Input changed during snapshot")
    return data


def demuxer(data: bytes) -> str:
    """Force a small allowlist; never auto-detect playlists or external manifests."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "matroska"
    if data[4:8] == b"ftyp":
        return "mov"
    raise UnsupportedMedia("Supported containers: WAV, FLAC, Ogg, Matroska/WebM, faststart MP4")


def finite(value: object) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise UnsupportedMedia("Unknown or nonfinite timing")
    return number


def inspect(source: str, output: str, *, video: int = 0, audio: int = 0,
            max_mib: int = 128, max_seconds: float = 120, timeout: float = 180) -> dict:
    """Create a new private bundle; failures retain an explicit incomplete receipt."""
    if (not 1 <= max_mib <= 512 or not 0 < max_seconds <= 600
            or not 0 < timeout <= 900 or not math.isfinite(max_seconds)
            or not math.isfinite(timeout) or min(video, audio) < 0):
        raise ValueError("Invalid limits or stream ordinals")
    validate_filesystem_path(output)
    folder = Path(output).absolute()
    folder.mkdir(mode=0o700)  # refuses overwrite, including symlinks
    started = time.monotonic()
    result = {"schema_version": 1, "kind": "file-media-analysis", "status": "incomplete",
              "source": {"name": Path(source).name}, "errors": [], "frames": [],
              "limits": {"input_mib": max_mib, "duration_seconds": max_seconds,
                         "wall_seconds": timeout, "process_address_mib": 1536,
                         "pixels": 1920 * 1080, "output_file_mib": 16},
              "selection": {"video_ordinal": video, "audio_ordinal": audio},
              "limitations": ["Samples do not establish quality, sync, HDR or presentation fidelity.",
                              "No device capture, playback or GPU qualification.",
                              "Pipe input requires streamable self-contained media."]}
    try:
        # One invocation at a time per output parent. The lock is its directory
        # descriptor, so no writable shared lock file or stale marker is needed.
        with _parent_lock(folder.parent):
            if shutil.disk_usage(folder.parent).free < 128 * 1024 * 1024:
                raise OSError("At least 128 MiB free output space required")
            data = snapshot(source, max_mib * 1024 * 1024)
            result["source"].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            container = demuxer(data)
            tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "prlimit")}
            if not all(tools.values()):
                raise RuntimeError("Missing tools: " + ", ".join(k for k, v in tools.items() if not v))

            def command(name: str, args: list[str], *, media: bool = True) -> str:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Total media deadline exceeded")
                argv = [tools["prlimit"], "--as=1610612736", "--fsize=16777216", "--cpu=90", "--",
                        tools[name], "-hide_banner", "-max_alloc", "67108864"]
                if media:
                    argv += ["-protocol_whitelist", "pipe", "-f", container, "-threads", "1"]
                    if container == "mov":
                        argv += ["-enable_drefs", "0", "-use_absolute_path", "0"]
                    argv += ["-i", "pipe:0"]
                completed = run_captured(argv + args, input_data=data if media else None,
                                         timeout=remaining, max_bytes=2 * 1024 * 1024,
                                         cwd=str(folder), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
                if completed.returncode or (name == "ffprobe" and media and completed.stderr.strip()):
                    raise RuntimeError(f"{name} failed: {completed.stderr[-1500:]}")
                return completed.stdout if name == "ffprobe" or not media else completed.stderr

            result["tools"] = {name: command(name, ["-version"], media=False).splitlines()[0]
                               for name in ("ffmpeg", "ffprobe")}
            metadata = json.loads(command("ffprobe", ["-v", "error", "-show_format", "-show_streams",
                                  "-show_packets", "-show_entries", "packet=pts_time,duration_time,stream_index",
                                  "-of", "json"]))
            packets = metadata.pop("packets", [])
            result["metadata"] = metadata
            ends = [finite(p["pts_time"]) + finite(p["duration_time"]) for p in packets
                    if "pts_time" in p and "duration_time" in p]
            starts = [finite(p["pts_time"]) for p in packets if "pts_time" in p]
            duration = (finite(metadata.get("format", {}).get("duration"))
                        if metadata.get("format", {}).get("duration") is not None
                        else max(ends, default=0) - min(starts, default=0))
            result["timing"] = {"duration_seconds": duration,
                                "duration_basis": "container" if "duration" in metadata.get("format", {}) else "packet span",
                                "frame_pts_domain": "FFmpeg input timestamps normalized by input start time"}
            packet_span = max(ends, default=0) - min(starts, default=0)
            if not 0 < duration <= max_seconds or packet_span > max_seconds + .1:
                raise UnsupportedMedia("Duration is unknown or exceeds the configured limit")
            streams = metadata.get("streams", [])
            if not any(s.get("codec_type") in {"audio", "video"} for s in streams):
                raise UnsupportedMedia("No audio or video streams")
            if len(streams) > 16:
                raise UnsupportedMedia("At most 16 streams supported")
            selected = {}
            for kind, ordinal in (("video", video), ("audio", audio)):
                matches = [s for s in streams if s.get("codec_type") == kind]
                if not matches and ordinal == 0:
                    result[kind] = {"status": "missing"}
                    continue
                if ordinal >= len(matches):
                    raise UnsupportedMedia(f"Selected {kind} stream does not exist")
                selected[kind] = matches[ordinal]
                result[kind] = {"status": "pending", "stream_index": matches[ordinal]["index"]}
            for stream in streams:
                if stream.get("codec_type") == "video":
                    if not 0 < int(stream.get("width", 0)) * int(stream.get("height", 0)) <= 1920 * 1080:
                        raise UnsupportedMedia("Video dimensions exceed 1920×1080 pixel budget")
                if stream.get("codec_type") == "audio":
                    if not (1 <= int(stream.get("channels", 0)) <= 8
                            and 8000 <= int(stream.get("sample_rate", 0)) <= 192000):
                        raise UnsupportedMedia("Audio must have 1–8 channels at 8–192 kHz")
            prefix = ["-nostdin", "-nostats", "-xerror", "-n", "-t", str(max_seconds), "-filter_threads", "1"]
            if "video" in selected:
                stream = selected["video"]
                command("ffmpeg", prefix + ["-v", "error", "-map", f"0:{stream['index']}",
                                            "-f", "null", "-"])
                for index, target in enumerate((0, duration / 2, max(0, duration - .25))):
                    name = f"frame-{index}.png"
                    filters = f"select=gte(t\\,{target}),scale=640:360:force_original_aspect_ratio=decrease,showinfo"
                    log = command("ffmpeg", prefix + ["-map", f"0:{stream['index']}", "-vf", filters,
                                  "-frames:v", "1", "-threads", "1", str(folder / name)])
                    times = re.findall(r"\bn:\s*\d+.*?pts_time:([\d.e+-]+)", log)
                    if not times or not (folder / name).is_file():
                        raise RuntimeError("No decoded frame at requested sample")
                    result["frames"].append({"artifact": name, "requested_seconds": target,
                                             "decoded_pts_seconds": finite(times[0]),
                                             "source_sha256": result["source"]["sha256"],
                                             "sha256": hashlib.sha256((folder / name).read_bytes()).hexdigest()})
                result["video"]["status"] = "complete"
                _contact_sheet(folder, result)
            if "audio" in selected:
                filters = "astats=metadata=0:reset=0,ebur128=peak=true:framelog=verbose,silencedetect=n=-50dB:d=0.25"
                log = command("ffmpeg", prefix + ["-map", f"0:{selected['audio']['index']}",
                              "-af", filters, "-f", "null", "-"])
                measurements = parse_analysis(log, duration)
                if ("sample_peak_dbfs" not in measurements["overall"]
                        or "rms_dbfs" not in measurements["overall"]
                        or measurements["integrated_lufs"] is None):
                    raise RuntimeError("Audio measurement summary incomplete")
                result["audio"].update(status="complete", filters=filters, measurements=measurements)
            result["status"] = "complete"
    except UnsupportedMedia as exc:
        result.update(status="unsupported", errors=[str(exc)])
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        result["errors"].append(str(exc))
    except KeyboardInterrupt:
        result.update(status="cancelled", errors=["Interrupted; owned child process group terminated"])
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        for artifact in folder.iterdir():
            artifact.chmod(0o600)
        write_json_atomic(str(folder / "manifest.json"), result, mode=0o600)
    return result


@contextmanager
def _parent_lock(parent: Path):
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _contact_sheet(folder: Path, result: dict) -> None:
    # Embedded PNGs keep the labelled sheet self-contained and passive.
    rows = []
    for index, frame in enumerate(result["frames"]):
        encoded = base64.b64encode((folder / frame["artifact"]).read_bytes()).decode("ascii")
        y = index * 400
        rows.append(f'<text x="0" y="{y + 22}">PTS {frame["decoded_pts_seconds"]:.6f}s</text>'
                    f'<image x="0" y="{y + 30}" width="640" height="360" href="data:image/png;base64,{encoded}"/>')
    digest = result["source"]["sha256"]
    (folder / "contact-sheet.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="1240">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<g font-family="sans-serif" font-size="16">' + "".join(rows)
        + f'<text x="0" y="1225" font-size="9">SHA256 {digest}</text></g></svg>', encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--output", required=True, help="New private bundle under an existing parent")
    parser.add_argument("--video", type=int, default=0, help="Video stream ordinal")
    parser.add_argument("--audio", type=int, default=0, help="Audio stream ordinal")
    parser.add_argument("--max-mib", type=int, default=128)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        result = inspect(**vars(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "incomplete", "errors": [str(exc)]}))
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
