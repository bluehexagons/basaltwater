#!/usr/bin/env python3
"""Inspect a capture summary and bounded log excerpts without rerunning its command."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Sequence


def _limit(value: str, maximum: int) -> int:
    try:
        number = int(value)
        if 1 <= number <= maximum:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"select from 1 through {maximum} bytes")


def _regular_file(path: Path):
    if path.is_symlink():
        raise ValueError("Refusing a symlink artifact")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Artifact must be a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _summary(path: Path) -> dict:
    with _regular_file(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Summary exceeds 64 KiB")
    value = json.loads(data.decode("utf-8"))
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("status") not in ("in_progress", "completed", "timeout", "interrupted", "launch_error")):
        raise ValueError("Unsupported capture summary")
    code, duration = value.get("return_code"), value.get("duration_seconds")
    if value["status"] == "in_progress":
        if code is not None or duration is not None:
            raise ValueError("Invalid unfinished capture summary")
    elif (type(code) is not int or not 0 <= code <= 2**32 - 1
          or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        raise ValueError("Invalid capture outcome")
    result = {"schema_version": 1, "summary_path": str(path), "status": value["status"],
              "return_code": code, "duration_seconds": duration}
    for name in ("stdout_bytes", "stderr_bytes"):
        if type(value.get(name)) is not int or value[name] < 0:
            raise ValueError("Invalid captured byte count")
        result[name] = value[name]
    for name in ("error", "cleanup_error"):
        if name in value:
            if not isinstance(value[name], str):
                raise ValueError("Invalid capture error")
            result[name] = value[name][:512]
            if len(value[name]) > 512:
                result[name + "_truncated"] = True
    return result


def _read_stream(path: Path, scan_bytes: int, terms: list[str]) -> tuple[dict, list[str]]:
    with _regular_file(path) as stream:
        size = os.fstat(stream.fileno()).st_size
        start = max(0, size - scan_bytes)
        stream.seek(start)
        data = stream.read(min(size, scan_bytes))
        changed = os.fstat(stream.fileno()).st_size != size or len(data) != size - start
    try:
        text = data.decode("utf-8")
        replaced = False
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        replaced = True
    lines = text.splitlines(keepends=True) if terms else [text]
    if terms:
        lines = [line for line in lines if any(term in line for term in terms)]
    return {"observed_bytes": size, "scan_start_bytes": start, "scan_bytes": len(data),
            "scan_truncated": start > 0, "changed_during_read": changed, "utf8_replaced": replaced,
            "matched_lines": len(lines) if terms else None}, lines


def _excerpt(lines: list[str], budget: int, terms: list[str]) -> tuple[str, bool]:
    remaining = budget
    selected = []
    for line in reversed(lines):
        encoded = line.encode("utf-8")
        if len(encoded) > remaining:
            if terms:
                # Preserve a literal hit even when one matching line is enormous.
                index = min(line.index(term) for term in terms if term in line)
                encoded = line[index:].encode("utf-8")[:remaining]
            else:
                encoded = encoded[-remaining:] if remaining else b""
            selected.append(encoded.decode("utf-8", errors="ignore"))
            break
        selected.append(line)
        remaining -= len(encoded)
    text = "".join(reversed(selected))
    return text, len(text.encode("utf-8")) < sum(len(line.encode("utf-8")) for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="saved summary.json; only adjacent stdout.log/stderr.log are read")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--stream", choices=("stdout", "stderr", "both"),
                           help="request excerpts explicitly; failures default to both, other outcomes to summary only")
    selection.add_argument("--summary-only", action="store_true", help="inspect status without reading either log")
    parser.add_argument("--max-bytes", type=lambda value: _limit(value, 65536), default=4096,
                        help="total UTF-8 excerpt budget across streams, before JSON escaping (default 4096)")
    parser.add_argument("--scan-bytes", type=lambda value: _limit(value, 1048576), default=65536,
                        help="maximum tail window read per stream, including literal searches (default 65536)")
    parser.add_argument("--match", action="append", default=[], help="literal line filter within the tail window (repeatable OR)")
    args = parser.parse_args(argv)
    if args.summary_only and args.match:
        parser.error("--match needs log inspection, not --summary-only")
    if any(not term or len(term.encode("utf-8")) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in term) for term in args.match):
        parser.error("matches require 1–256 UTF-8 bytes without control characters")
    try:
        summary_path = args.summary.expanduser().absolute()
        result = _summary(summary_path)
        selection = None if args.summary_only else args.stream or ("both" if args.match or result["return_code"] not in (None, 0) else None)
        if selection is not None:
            names = ("stderr", "stdout") if selection == "both" else (selection,)
            streams = {}
            for name in names:
                streams[name] = _read_stream(summary_path.parent / (name + ".log"), args.scan_bytes, args.match)
            # Short/empty streams surrender unused budget to the larger stream.
            ordered = sorted(names, key=lambda name: sum(len(line.encode("utf-8")) for line in streams[name][1]))
            remaining = args.max_bytes
            result["streams"] = {}
            for index, name in enumerate(ordered):
                metadata, lines = streams[name]
                text, truncated = _excerpt(lines, remaining // (len(ordered) - index), args.match)
                remaining -= len(text.encode("utf-8"))
                result["streams"][name] = {**metadata, "excerpt_truncated": truncated, "text": text}
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0  # Inspection success, independent of the captured command's exit.
    except (OSError, ValueError, OverflowError) as exc:
        print(json.dumps({"inspection_error": str(exc)[:512]}, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
