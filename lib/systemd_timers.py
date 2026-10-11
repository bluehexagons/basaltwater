"""Read-only formatting of systemd calendar and monotonic timer deadlines."""

from __future__ import annotations

import math
import re
import time


def format_timer_next_trigger(timer: dict[str, str]) -> str:
    """Describe both deadlines without assuming the calendar run comes first."""
    if timer.get("ActiveState") != "active":
        return "Timer is not active"
    realtime = timer.get("NextElapseUSecRealtime")
    has_realtime = bool(realtime and realtime not in {"n/a", "0", "infinity", "[not set]"})
    fallback = str(realtime) if has_realtime else "Next trigger not reported"
    # systemctl formats monotonic deadlines as boot-relative time spans.
    raw = timer.get("NextElapseUSecMonotonic", "")
    factors = {"w": 604800, "d": 86400, "h": 3600, "min": 60, "s": 1, "ms": .001, "us": .000001}
    parts = raw.split()
    seconds = 0.0
    for part in parts:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(min|ms|us|w|d|h|s)", part)
        if not match:
            return fallback
        seconds += float(match[1]) * factors[match[2]]
    if not parts or seconds <= 0 or not math.isfinite(seconds):
        return fallback
    remaining = seconds - time.monotonic()
    if remaining <= 0:
        return "Due; waiting for timer dispatch"
    minutes = max(1, round(remaining / 60))
    interval = f"In about {minutes} minute{'s' if minutes != 1 else ''}"
    if has_realtime:
        return f"{realtime} or {interval.lower()}, whichever is earlier"
    return interval
