#!/usr/bin/env python3
"""Run opt-in Windows media fixtures and CPU production in a new private directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.media_cli import inspect
from lib.media_windows import validate_path as validate_filesystem_path, private_directory, run_captured
import _thread
import threading
from unittest.mock import patch


def require(condition: object, message: str) -> None:
    """Keep qualification gates active even under optimized Python."""
    if not condition:
        raise RuntimeError(message)


def qualify(output: str) -> dict:
    validate_filesystem_path(output)
    root = Path(output).absolute()
    private_directory(root)
    from lib.media_windows import discover
    tools = discover()
    require(all(tools.values()), "Install/discover FFmpeg and ffprobe before qualification")
    measurements = []

    def ffmpeg(name: str, args: list[str]) -> Path:
        target = root / name
        started = time.monotonic()
        completed = run_captured([tools["ffmpeg"], "-nostdin", "-v", "error", "-n", "-filter_threads", "1", *args, "-fs", "16777216", str(target)],
                                   timeout=120, cwd=str(root))
        if completed.returncode:
            raise RuntimeError(completed.stderr[-1500:])
        target.chmod(0o600)
        measurements.append({"artifact": name, "elapsed_seconds": time.monotonic() - started,
                             "job_cpu_seconds": completed.job_cpu_seconds,
                             "peak_job_commit_bytes": completed.peak_job_commit_bytes,
                             "bytes": target.stat().st_size})
        return target

    video = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24"]
    audio = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    encode = ["-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]
    source = ffmpeg("音 sample.mp4", video + audio + ["-t", "4"] + encode)
    silent = ffmpeg("silent.wav", ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2"])
    flac = ffmpeg("tone.flac", audio + ["-t", "2", "-c:a", "flac"])
    ogg = ffmpeg("tone.ogg", audio + ["-t", "2", "-c:a", "libvorbis"])
    no_audio = ffmpeg("no-audio.mp4", video + ["-t", "2"] + encode)
    low_rate = ffmpeg("low-rate.mp4", ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=1", "-t", "2"] + encode)
    short_video = ffmpeg("short-video.mp4", ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=2"]
                         + audio + ["-t", "6"] + encode)
    delayed_video = ffmpeg("delayed-video.mp4", ["-itsoffset", "1"] + video + audio + ["-t", "4"] + encode
                           + ["-fps_mode:v", "passthrough"])
    vfr = ffmpeg("vfr.mp4", video + ["-t", "4", "-vf", "select='if(lt(t,2),1,not(mod(n,3)))'", "-fps_mode", "vfr"] + encode)
    offset = ffmpeg("offset.mp4", video + ["-itsoffset", "0.5"] + audio + ["-t", "4"] + encode)
    rotated = ffmpeg("rotated.mp4", ["-display_rotation:v:0", "90", "-i", str(source), "-c", "copy",
                                      "-movflags", "+faststart"])
    unknown = ffmpeg("unknown.webm", video + ["-t", "2", "-c:v", "libvpx-vp9", "-threads", "1", "-live", "1"])
    # Editable production recipe, with linked original retained alongside it.
    recipe = {"source": source.name, "trim_seconds": [1, 3], "gain_db": -3,
              "fade_seconds": .1, "delivery_dimensions": [320, 180], "repeat": 2}
    (root / "project.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    trimmed_audio = ffmpeg("trimmed.wav", ["-i", str(source), "-map", "0:a:0", "-ss", "1", "-t", "2", "-af",
                           "volume=-3dB,afade=t=in:d=0.1,afade=t=out:st=1.9:d=0.1", "-c:a", "pcm_s16le"])
    trimmed = ffmpeg("trimmed.mp4", ["-i", str(source), "-ss", "1", "-t", "2", "-vf", "scale=320:180"] + encode)
    delivery = ffmpeg("delivery.mp4", ["-i", str(trimmed), "-i", str(trimmed), "-filter_complex",
                     "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-filter_complex_threads", "1",
                     "-map", "[v]", "-map", "[a]"] + encode)
    with wave.open(str(trimmed_audio)) as wav:
        require(wav.getnframes() == 96000, "Trimmed audio must contain 96000 frames")
        samples = wav.readframes(wav.getnframes())
        require(abs(int.from_bytes(samples[:2], "little", signed=True)) < 20, "Audio fade-in endpoint failed")
        require(abs(int.from_bytes(samples[-2:], "little", signed=True)) < 20, "Audio fade-out endpoint failed")

    def decoded_frame(path: Path, seconds: float) -> bytes:
        completed = run_captured([tools["ffmpeg"], "-nostdin", "-v", "error", "-threads", "1", "-i", str(path),
                                    "-ss", str(seconds), "-vf", "scale=320:180", "-filter_threads", "1",
                                    "-frames:v", "1", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                                   timeout=30, cwd=str(root), binary=True)
        require(completed.returncode == 0, "Decoded frame command failed")
        require(len(completed.stdout) == 320 * 180 * 3, "Decoded RGB frame size mismatch")
        return completed.stdout

    # Re-encoded boundaries need a decoded comparison, not just duration tags.
    boundary_errors = []
    for left, left_time, right, right_time in ((source, 1, trimmed, 0), (source, 2.95, trimmed, 1.95),
                                              (trimmed, 0, delivery, 2)):
        a, b = decoded_frame(left, left_time), decoded_frame(right, right_time)
        error = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        require(error < 10, f"Decoded boundary error exceeds limit: {error}")
        boundary_errors.append(error)
    malformed = root / "malformed.mp4"
    malformed.write_bytes(b"garbage")
    playlist = root / "playlist.m3u8"
    playlist.write_text("#EXTM3U\nfile:///not-readable\n", encoding="utf-8")
    truncated = root / "truncated.mp4"
    truncated.write_bytes(source.read_bytes()[:source.stat().st_size // 2])
    cases = []
    for fixture in (source, silent, flac, ogg, no_audio, low_rate, short_video, delayed_video,
                    vfr, offset, rotated, unknown, trimmed_audio, trimmed, delivery,
                    malformed, playlist, truncated):
        result = inspect(str(fixture), str(root / (fixture.name + "-analysis")))
        expected = "complete" if fixture not in (malformed, playlist, truncated) else None
        if (expected and result["status"] != expected) or (not expected and result["status"] == "complete"):
            raise RuntimeError(f"Unexpected {fixture.name}: {result['status']} {result['errors']}")
        if fixture == silent:
            require(result["audio"]["measurements"]["overall"]["rms_dbfs"] is None,
                    "Silent fixture has finite RMS")
            require(result["audio"]["measurements"]["silence_intervals"], "Silent fixture has no silence interval")
        if fixture == no_audio:
            require(result["audio"]["status"] == "missing", "No-audio fixture unexpectedly has audio")
        if fixture in (low_rate, short_video, delayed_video):
            first, last = result["video"]["packet_pts_range_seconds"]
            require(all(first - .000002 <= frame["requested_seconds"] <= last for frame in result["frames"]),
                    "Requested video sample is outside the selected packet span")
            require(all(frame["decoded_pts_seconds"] <= last + .000002 for frame in result["frames"]),
                    "Decoded video sample exceeds the selected packet span")
            if fixture == delayed_video:
                require(first >= .99, "Delayed video offset was not preserved")
        if fixture == offset:
            require(float(result["metadata"]["streams"][1]["start_time"]) >= .45,
                    "Audio offset was not preserved")
        if fixture == rotated:
            require(result["metadata"]["streams"][0]["side_data_list"][0]["rotation"] == 90,
                    "Rotation metadata was not preserved")
            image = (root / (fixture.name + "-analysis") / "frame-0.png").read_bytes()
            require(int.from_bytes(image[16:20], "big") < int.from_bytes(image[20:24], "big"),
                    "Decoded frame was not rotated to portrait")
        if fixture == unknown:
            require(result["timing"]["duration_basis"] == "packet span", "Expected packet-derived duration")
        if fixture == vfr:
            probe = run_captured([tools["ffprobe"], "-v", "error", "-select_streams", "v:0", "-show_packets",
                                    "-show_entries", "packet=pts_time", "-of", "json", str(vfr)],
                                   timeout=15, cwd=str(root))
            require(probe.returncode == 0, "VFR probe failed")
            timestamps = sorted(float(p["pts_time"]) for p in json.loads(probe.stdout)["packets"])
            gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
            require(max(gaps) > min(gaps) * 2, "VFR fixture has no variable packet intervals")
            for frame in result["frames"]:
                require(-.001 <= frame["decoded_pts_seconds"] - frame["requested_seconds"] <= .13,
                        "VFR decoded sample exceeds timestamp tolerance")
        if fixture in (trimmed_audio, trimmed, delivery):
            expected_duration = 4 if fixture == delivery else 2
            require(abs(result["timing"]["duration_seconds"] - expected_duration) < .1,
                    f"{fixture.name} duration mismatch")
        cases.append({"fixture": fixture.name, "status": result["status"], "elapsed_seconds": result["elapsed_seconds"]})
    deadline = inspect(str(source), str(root / "deadline"), timeout=.001)
    require(deadline["status"] == "incomplete", "Deadline probe did not fail as expected")
    require(deadline["elapsed_seconds"] < 5, "Deadline probe exceeded cleanup allowance")
    # Instrument process creation to interrupt an owned decoder; real Job
    # containment, pipes and cleanup run unchanged.
    from lib import media_windows as native
    original_popen = native.subprocess.Popen
    owned = []
    timer = None

    def observe_popen(argv, **kwargs):
        nonlocal timer
        process = original_popen(argv, **kwargs)
        owned.append(process)
        if "-i" in argv and "null" in argv and timer is None:
            timer = threading.Timer(.01, _thread.interrupt_main)
            timer.start()
        return process

    cancel_output = root / "cancelled"
    interrupted = False
    try:
        with patch.object(native.subprocess, "Popen", side_effect=observe_popen):
            inspect(str(source), str(cancel_output))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if timer:
            timer.join()
    require(interrupted, "Decoder did not receive cancellation")
    require(json.loads((cancel_output / "manifest.json").read_text())["status"] == "cancelled",
            "Cancellation did not retain a cancelled manifest")
    require(all(p.poll() is not None for p in owned), "Owned decoder survived cancellation")
    from lib import media_cli
    with patch.object(media_cli, "discover", return_value={"ffmpeg": None, "ffprobe": None}):
        missing = inspect(str(source), str(root / "missing-tools"))
    require(missing["status"] == "incomplete" and "Missing tools" in missing["errors"][0],
            "Missing-tool refusal failed")
    report = {"schema_version": 1, "cases": cases, "production": measurements,
              "decoded_boundary_mean_absolute_rgb_errors": boundary_errors,
              "platform": sys.platform, "tools": tools,
              "limits": {"job_commit_mib": 1536, "job_cpu_seconds": 90, "per_command_seconds": 120},
              "total_retained_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
              "deadline_status": deadline["status"]}
    report["cancellation_cleanup_confirmed"] = True
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    (root / "qualification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (root / "qualification.json").chmod(0o600)
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    print(json.dumps(qualify(parser.parse_args().output), indent=2))
