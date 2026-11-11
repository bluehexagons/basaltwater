"""Portable FFmpeg summary parser for the file-media contract.

Kept independent of the Linux desktop capture dependency graph.
"""
from __future__ import annotations

import math
import re


def number(value: str) -> float | None:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def parse_analysis(text: str, duration: float) -> dict:
    """Parse explicitly scoped FFmpeg summaries; preserve missing fields."""
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
