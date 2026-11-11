#!/usr/bin/env python3
"""Opt-in synthetic CPU/GPU media qualification; no capture, playback or driver changes."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import resource
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.atomic_io import write_json_atomic
from lib.media_cli import inspect
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path


def comparison_psnr(log: str) -> float:
    """Require one decoded comparison; positive infinity means identical pixels."""
    values = re.findall(r"\[Parsed_psnr_[^]]+\] PSNR .*?average:([\w.+-]+)", log)
    if len(values) != 1:
        raise RuntimeError("Expected one decoded PSNR summary")
    value = float(values[0])
    if math.isnan(value) or value < 25:
        raise RuntimeError("Decoded sample PSNR below 25 dB")
    return value  # Positive infinity means identical decoded samples.


def backend_commands(backend: str, device: str, source: str) -> dict[str, list[str]]:
    """Require hardware frames for decode/effects so software fallback cannot pass."""
    if backend == "vaapi":
        init = ["-init_hw_device", f"vaapi=gpu:{device}", "-filter_hw_device", "gpu"]
        decode = ["-hwaccel", "vaapi", "-hwaccel_device", "gpu", "-hwaccel_output_format", "vaapi"]
        upload = "format=nv12,hwupload"
        scale = "scale_vaapi=w=320:h=180:format=nv12"
        codec = "h264_vaapi"
    elif backend == "cuda":
        init = ["-init_hw_device", f"cuda=gpu:{device}", "-filter_hw_device", "gpu"]
        decode = ["-hwaccel", "cuda", "-hwaccel_device", "gpu", "-hwaccel_output_format", "cuda"]
        upload = "format=nv12,hwupload"
        scale = "scale_cuda=w=320:h=180:format=nv12"
        codec = "h264_nvenc"
    else:
        raise ValueError("Backend must be vaapi or cuda")
    return {
        "encode": init + ["-i", source, "-vf", upload, "-c:v", codec],
        "decode": init + decode + ["-i", source, "-vf", "hwdownload,format=nv12", "-c:v", "libx264"],
        "scale": init + decode + ["-i", source, "-vf", scale, "-c:v", codec],
    }


def qualify(output: str, backend: str, device: str) -> dict:
    """Retain private commands, logs, receipts and separate per-operation outcomes."""
    validate_filesystem_path(output)
    if backend not in {"vaapi", "cuda"}:
        raise ValueError("Backend must be vaapi or cuda")
    if backend == "vaapi":
        validate_filesystem_path(device, must_exist=True)
    elif not device.isascii() or not device.isdecimal():
        raise ValueError("CUDA device must be a nonnegative ordinal")
    root = Path(output).absolute()
    root.mkdir(mode=0o700)
    report = {"schema_version": 1, "backend": backend, "device": device,
              "status": "incomplete", "operations": [],
              "limitations": ["Synthetic 4-second 720p H.264/AAC workload; single observations.",
                              "GPU frame operations do not qualify editor timeline effects.",
                              "No physical playback, capture, HDR or perceived-quality checks.",
                              "Largest child RSS is cumulative, not aggregate system memory."]}

    def run(name: str, args: list[str]) -> str:
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        command = ["prlimit", "--as=8589934592", "--cpu=120", "--fsize=67108864", "--", *args]
        entry = {"name": name, "argv": command, "returncode": None}
        report["operations"].append(entry)
        try:
            result = run_captured(command, timeout=180, max_bytes=2 * 1024 * 1024,
                                  env={**os.environ, "LC_ALL": "C"}, cwd=str(root))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            entry["error"] = str(exc)
            raise
        finally:
            entry["elapsed_seconds"] = time.monotonic() - started
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        entry.update(returncode=result.returncode,
                     child_cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                     cumulative_largest_child_rss_kib=after.ru_maxrss)
        (root / (name + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {name}.log")
        return result.stdout + result.stderr

    common = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "verbose", "-n", "-threads", "1",
              "-filter_threads", "1"]
    end = ["-map", "0:v:0", "-map", "0:a:0", "-threads", "1", "-c:a", "aac", "-movflags", "+faststart"]
    try:
        report["ffmpeg_version"] = run("version", ["ffmpeg", "-version"]).splitlines()[0]
        source = root / "source.mp4"
        run("source", common + ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                               "-t", "4", "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
                               "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(source)])
        reference = inspect(str(source), str(root / "source-analysis"))
        if reference["status"] != "complete":
            raise RuntimeError("Synthetic source analysis incomplete")
        report["source"] = reference["source"]
        reference_rms = reference["audio"]["measurements"]["overall"]["rms_dbfs"]
        if reference_rms is None or not math.isfinite(reference_rms):
            raise RuntimeError("Synthetic source audio RMS unavailable")
        commands = {"cpu": ["-i", str(source), "-c:v", "libx264", "-preset", "veryfast"]}
        commands.update(backend_commands(backend, device, str(source)))
        outcomes = {}
        for name, args in commands.items():
            target = root / (name + ".mp4")
            try:
                run(name, common + args + end + [str(target)])
                analysis = inspect(str(target), str(root / (name + "-analysis")))
                if analysis["status"] != "complete":
                    raise RuntimeError(f"{name} output analysis incomplete")
                video = next(s for s in analysis["metadata"]["streams"] if s["codec_type"] == "video")
                expected = (320, 180) if name == "scale" else (1280, 720)
                if ((video["width"], video["height"]) != expected
                        or abs(analysis["timing"]["duration_seconds"] - 4) > .1
                        or analysis["audio"]["status"] != "complete"):
                    raise RuntimeError(f"{name} output stream/duration mismatch")
                rms = analysis["audio"]["measurements"]["overall"]["rms_dbfs"]
                if rms is None or not math.isfinite(rms) or abs(rms - reference_rms) > 1:
                    raise RuntimeError(f"{name} audio RMS differs from source by more than 1 dB")
                samples = []
                for seconds in (0, 2, 3.9):
                    # Both paths decode to the same small software format for
                    # comparison; the measured production operation stays separate.
                    log = run(f"{name}-compare-{seconds}", [
                        "ffmpeg", "-nostdin", "-hide_banner", "-v", "info", "-threads", "1",
                        "-ss", str(seconds), "-i", str(source), "-threads", "1",
                        "-ss", str(seconds), "-i", str(target), "-filter_complex_threads", "1",
                        "-filter_complex", "[0:v]scale=320:180,format=yuv420p,setpts=PTS-STARTPTS[ref];"
                        "[1:v]scale=320:180,format=yuv420p,setpts=PTS-STARTPTS[out];[ref][out]psnr",
                        "-an", "-frames:v", "1", "-threads", "1", "-f", "null", "-"])
                    psnr = comparison_psnr(log)
                    samples.append({"seconds": seconds, "psnr_db": psnr if math.isfinite(psnr) else None,
                                    "identical": psnr == math.inf})
                outcomes[name] = {"status": "complete", "bytes": target.stat().st_size,
                                  "decoded_samples": samples, "audio_rms_delta_db": rms - reference_rms}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                outcomes[name] = {"status": "failed", "error": str(exc)}
        report["outcomes"] = outcomes
        report["status"] = "complete" if all(v["status"] == "complete" for v in outcomes.values()) else "incomplete"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    finally:
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(0o600)
        write_json_atomic(str(root / "qualification.json"), report, mode=0o600)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New private directory under an existing parent")
    parser.add_argument("--backend", required=True, choices=("vaapi", "cuda"))
    parser.add_argument("--device", required=True, help="VA-API render node path or CUDA device ordinal")
    args = parser.parse_args()
    result = qualify(args.output, args.backend, args.device)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "complete" else 1)
