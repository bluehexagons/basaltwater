"""Offline bounded WAV analysis; thresholds flag candidates, never diagnose sound."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re

from desktop.evidence_media import media_command
from desktop.qa_artifacts import clock, file_digest, new_bundle, snapshot, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.streamed_process import run_captured


def audio_metadata(path: Path) -> dict:
    raw = media_command(["/usr/bin/ffprobe", "-v", "error", "-f", "wav", "-show_entries",
                         "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)])
    try:
        data = json.loads(raw)
        if len(data["streams"]) != 1:
            raise ValueError("Expected one audio stream")
        stream = data["streams"][0]
        rate, channels, duration = int(stream["sample_rate"]), int(stream["channels"]), float(data["format"]["duration"])
        if not 8000 <= rate <= 192000 or not 1 <= channels <= 8 or not 0 < duration <= 120:
            raise ValueError("WAV must be 0–120 seconds, 8–192 kHz and 1–8 channels")
        return {"sample_rate": rate, "channels": channels, "duration_seconds": duration,
                "codec": stream["codec_name"]}
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("Incomplete WAV metadata") from exc


def number(value: str) -> float | None:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def parse_analysis(text: str, duration: float) -> dict:
    """Parse explicitly scoped FFmpeg summary fields; missing measurements stay null."""
    overall, channels, section, pending = {}, {}, None, None
    intervals = []
    integrated = loudness_range = true_peak = None
    for line in text.splitlines():
        if "[Parsed_astats_" in line:
            if match := re.search(r"Channel:\s*(\d+)", line):
                section = channels.setdefault(match[1], {})
            elif "Overall" in line:
                section = overall
            elif section is not None:
                if match := re.search(r"(Peak level dB|RMS level dB):\s*([-+\w.]+)", line):
                    section[{"Peak level dB": "sample_peak_dbfs", "RMS level dB": "rms_dbfs"}[match[1]]] = number(match[2])
        if match := re.search(r"silence_start:\s*([\d.e+-]+)", line):
            pending = max(0.0, float(match[1]))
        if match := re.search(r"silence_end:\s*([\d.e+-]+)", line):
            end = min(duration, float(match[1]))
            if pending is not None and pending <= end:
                intervals.append({"start_seconds": pending, "end_seconds": end})
            pending = None
        if match := re.match(r"\s*I:\s*([-+\w.]+) LUFS", line):
            integrated = number(match[1])
        if match := re.match(r"\s*LRA:\s*([-+\w.]+) LU", line):
            loudness_range = number(match[1])
        if match := re.match(r"\s*Peak:\s*([-+\w.]+) dBFS", line):
            true_peak = number(match[1])
    if pending is not None and pending <= duration:
        intervals.append({"start_seconds": pending, "end_seconds": duration})
    return {"overall": overall, "channels": channels, "integrated_lufs": integrated,
            "loudness_range_lu": loudness_range, "estimated_true_peak_dbfs": true_peak,
            "silence_intervals": intervals}


def parse_levels(text: str) -> list[dict]:
    rows = []
    current = None
    for line in text.splitlines():
        if match := re.search(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.e+-]+)", line):
            current = {"start_seconds": float(match[1]), "levels": {}}
            rows.append(current)
        elif current is not None and (match := re.search(r"lavfi\.astats\.((?:\d+|Overall)\.(?:Peak_level|RMS_level))=([-+\w.]+)", line)):
            current["levels"][match[1]] = number(match[2])
    return rows


def analyze(source: str, output: str | None = None, *, capture_manifest: str | None = None) -> dict:
    folder = new_bundle(output)
    original = snapshot(source, folder / "source.wav")
    metadata = audio_metadata(folder / "source.wav")
    capture_reference = audio_origin = None
    if capture_manifest is not None:
        capture_reference = snapshot(capture_manifest, folder / "capture.json")
        capture = read_json_file(str(folder / "capture.json"), max_bytes=65536)
        if (not isinstance(capture, dict) or capture.get("schema_version") != 1
                or capture.get("kind") != "application-audio" or capture.get("qualified") is not True
                or capture.get("cleanup_confirmed") is not True or not isinstance(capture.get("audio"), dict)
                or capture["audio"].get("sha256") != original["sha256"]):
            raise ValueError("Capture receipt must qualify these exact WAV bytes with confirmed cleanup")
        audio_origin = {key: capture.get(key) for key in ("clock", "started", "link_ready", "finished", "audio_origin")}
    started = stamp()
    version = media_command(["/usr/bin/ffmpeg", "-version"]).splitlines()[0]
    filters = "astats=metadata=0:reset=0,ebur128=peak=true:framelog=verbose,silencedetect=n=-50dB:d=0.25"
    prefix = ["/usr/bin/ffmpeg", "-nostdin", "-nostats", "-v", "info", "-f", "wav", "-i", str(folder / "source.wav"), "-map", "0:a:0"]
    result = run_captured([*prefix, "-af", filters, "-f", "null", "-"], timeout=45, max_bytes=2 * 1024 * 1024,
                          env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if result.returncode:
        raise RuntimeError("FFmpeg analysis failed: " + result.stderr[-1000:])
    measurements = parse_analysis(result.stderr, metadata["duration_seconds"])
    levels_filter = (f"asetnsamples=n={metadata['sample_rate']}:p=0,"
                     "astats=metadata=1:reset=1:measure_perchannel=Peak_level+RMS_level:"
                     "measure_overall=Peak_level+RMS_level,ametadata=print")
    levels = run_captured([*prefix, "-af", levels_filter, "-f", "null", "-"], timeout=45, max_bytes=2 * 1024 * 1024,
                          env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if levels.returncode:
        raise RuntimeError("FFmpeg interval analysis failed: " + levels.stderr[-1000:])
    windows = parse_levels(levels.stderr)
    peak = measurements["overall"].get("sample_peak_dbfs")
    warnings = (["sample_peak_near_full_scale"] if peak is not None and peak >= -0.01 else [])
    if not measurements["overall"] or measurements["integrated_lufs"] is None or not windows:
        raise RuntimeError("FFmpeg output lacks required measurements; no complete analysis claimed")
    findings = []
    previous_rms = None
    for window in windows:
        start = window["start_seconds"]
        local_peak = window["levels"].get("Overall.Peak_level")
        rms = window["levels"].get("Overall.RMS_level")
        if local_peak is not None and local_peak >= -.01:
            findings.append({"kind": "sample_peak_near_full_scale", "start_seconds": start,
                             "end_seconds": min(start + 1, metadata["duration_seconds"])})
        if rms is not None and previous_rms is not None and abs(rms - previous_rms) >= 12:
            findings.append({"kind": "rms_change_at_least_12_db", "start_seconds": start,
                             "end_seconds": min(start + 1, metadata["duration_seconds"])})
        previous_rms = rms
    findings.extend({"kind": "silence", **interval} for interval in measurements["silence_intervals"])
    excerpts = []
    for index, finding in enumerate(findings[:3]):
        start = max(0, finding["start_seconds"] - .5)
        seconds = min(2, metadata["duration_seconds"] - start)
        name = f"suspect-{index}.wav"
        media_command(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "wav", "-i",
                       str(folder / "source.wav"), "-ss", str(start), "-t", str(seconds),
                       "-map", "0:a:0", "-c:a", "pcm_f32le", str(folder / name)])
        os.chmod(folder / name, 0o600)
        excerpts.append({"artifact": name, "sha256": file_digest(folder / name),
                         "interval_seconds": [start, start + seconds], "finding_index": index})
    data = {"schema_version": 1, "kind": "audio-analysis", "source": original, "format": metadata,
            "clock": clock(), "analysis_started": started, "analysis_finished": stamp(),
            "audio_origin": audio_origin, "capture_reference": capture_reference,
            "tool": version, "filters": filters, "window_filter": levels_filter,
            "measurements": measurements, "one_second_windows": windows, "warnings": warnings,
            "findings": findings, "excerpts": excerpts, "excerpts_omitted": max(0, len(findings) - len(excerpts)),
            "limitations": ["Full-scale peaks are warnings, not proof of distortion.",
                            "Silence threshold is -50 dB for at least 0.25 seconds across the stream.",
                            "Window times are relative to the WAV; analysis time is not capture time.",
                            "Loudness/true peak estimates do not verify audible quality or loop continuity."]}
    write_json_atomic(str(folder / "manifest.json"), data, mode=0o600)
    with (folder / "report.md").open("x") as report:
        os.fchmod(report.fileno(), 0o600)
        report.write("Audio measurements (candidates require listening/application review)\n\n"
                     f"Sample peak: {peak} dBFS; estimated true peak: {measurements['estimated_true_peak_dbfs']} dBFS; "
                     f"integrated loudness: {measurements['integrated_lufs']} LUFS.\n\n"
                     "[Source](source.wav) · [Measurements and provenance](manifest.json)\n\n")
        for excerpt in excerpts:
            report.write(f"- {findings[excerpt['finding_index']]['kind']} at {excerpt['interval_seconds']} seconds: "
                         f"[excerpt]({excerpt['artifact']})\n")
    for name, text in (("analysis.log", result.stderr), ("levels.log", levels.stderr)):
        with (folder / name).open("x") as log:
            os.fchmod(log.fileno(), 0o600)
            log.write(text)
    return {"artifact": str(folder / "manifest.json"), "format": metadata,
            "measurements": measurements, "warnings": warnings}
