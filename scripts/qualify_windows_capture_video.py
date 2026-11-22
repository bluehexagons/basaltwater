"""Opt-in timestamp/pixel qualification of a retained WGC PNG sequence.

Creates a bounded lossless debug clip; does not capture, play or configure devices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import media_windows as native


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def qualify(capture: str, output: str) -> dict:
    native.validate_path(capture, must_exist=True)
    native.validate_path(output)
    source, destination = Path(capture), Path(output)
    manifest = json.loads(native.snapshot(str(source / "manifest.json"), 262144))
    require(manifest.get("kind") == "windows-gpu-window-capture" and
            manifest.get("status") == "complete", "Require a completed WGC capture")
    frames = manifest["frames"]
    require(2 <= len(frames) <= 120, "Require 2..120 retained frames")
    tools = native.discover()
    require(tools["ffmpeg"] and tools["ffprobe"], "FFmpeg/ffprobe are required")
    with native.parent_lock(destination.parent):
        require(not destination.exists(), "Output must be new")
        native.private_directory(destination)
        encoded_bytes, times, dimensions = 0, [], None
        for index, frame in enumerate(frames):
            name = f"frame-{index:04d}.png"
            require(frame["artifact"] == name and frame["sequence"] == index, "Invalid frame identity")
            png = native.snapshot(str(source / name), 16 * 1024 * 1024)
            require(hashlib.sha256(png).hexdigest() == frame["sha256"], "Changed retained frame")
            require(png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR", "Invalid PNG")
            size = struct.unpack(">II", png[16:24])
            require(size == (frame["width"], frame["height"]) and 0 < size[0]*size[1] <= 1920*1080,
                    "Debug video requires fixed frames within 1920x1080 pixels")
            dimensions = dimensions or size
            require(size == dimensions, "Frame dimensions changed")
            encoded_bytes += len(png)
            require(encoded_bytes <= 32*1024*1024, "Video qualification input exceeds 32 MiB")
            (destination / name).write_bytes(png)
            tick = frame["frame_time_100ns"]
            require(type(tick) is int and tick > 0 and (not times or tick > times[-1]),
                    "Frame clock must strictly increase")
            times.append(tick)
        require(times[-1]-times[0] <= 10*10_000_000, "Frame span exceeds ten seconds")
        lines = ["ffconcat version 1.0"]
        for index in range(len(frames)):
            lines += [f"file 'frame-{index:04d}.png'", "option framerate 1000"]
            if index+1 < len(frames):
                lines.append(f"duration {(times[index+1]-times[index])/10_000_000:.7f}")
        (destination / "frames.ffconcat").write_text("\n".join(lines)+"\n", encoding="ascii")
        logs = []

        def run(argv: list[str]) -> str:
            result = native.run_captured(argv, timeout=60, cwd=str(destination))
            logs.append({"exit_code": result.returncode, "stderr": result.stderr,
                         "job_cpu_seconds": result.job_cpu_seconds,
                         "peak_job_commit_bytes": result.peak_job_commit_bytes})
            require(result.returncode == 0, "Debug clip subprocess failed; inspect commands.json")
            return result.stdout

        base = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-threads", "1"]
        inputs = ["-f", "concat", "-safe", "0", "-protocol_whitelist", "file", "-i", "frames.ffconcat"]
        try:
            run(base + inputs + ["-an", "-fps_mode", "passthrough", "-enc_time_base", "1:1000",
                                "-c:v", "ffv1", "-threads", "1", "-pix_fmt", "bgra", "-fs", "16777216", "-n", "debug.mkv"])
            probe = json.loads(run([tools["ffprobe"], "-v", "error", "-select_streams", "v:0",
                                   "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
                                   "-of", "json", "debug.mkv"]))
            observed = [float(frame["best_effort_timestamp_time"]) for frame in probe["frames"]]
            expected = [(tick-times[0])/10_000_000 for tick in times]
            require(len(observed) == len(expected), "Debug video dropped or duplicated frames")
            error = max(abs(a-b) for a, b in zip(observed, expected))
            require(error <= .002, "Debug video PTS differs from WGC intervals by over 2 ms")
            reference = run(base + inputs + ["-an", "-fps_mode", "passthrough", "-pix_fmt", "bgra", "-f", "framemd5", "-"])
            decoded = run(base + ["-i", "debug.mkv", "-an", "-fps_mode", "passthrough", "-pix_fmt", "bgra", "-f", "framemd5", "-"])

            def hashes(text: str) -> list[str]:
                return [line.rsplit(",", 1)[-1].strip() for line in text.splitlines() if line and not line.startswith("#")]

            require(hashes(reference) == hashes(decoded) and len(hashes(decoded)) == len(frames),
                    "Lossless clip pixels differ from retained PNG sequence")
            receipt = {"complete": True, "frames": len(frames), "max_pts_error_seconds": error,
                       "decoded_pixels_equal": True, "frame_span_seconds": expected[-1],
                       "capture_manifest_sha256": hashlib.sha256(native.snapshot(str(source / "manifest.json"), 262144)).hexdigest(),
                       "scope": "WGC frame intervals in a lossless debug clip; no audio, physical scanout or AV-sync assertion"}
            native.write_json_atomic(str(destination / "qualification.json"), receipt)
            return receipt
        finally:
            native.write_json_atomic(str(destination / "commands.json"), {"commands": logs})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(qualify(args.capture, args.output), indent=2))
