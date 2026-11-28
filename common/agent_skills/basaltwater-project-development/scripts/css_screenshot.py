#!/usr/bin/env python3
"""Make a CSS-sized PNG from a saved, uncropped browser viewport screenshot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile


_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_MAX_BYTES = 32 * 1024 * 1024
_MAX_PIXELS = 16 * 1024 * 1024


def _png_size(header: bytes) -> tuple[int, int]:
    if len(header) != 24 or not header.startswith(_PNG_HEADER):
        raise ValueError("Expected a PNG with a standard IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    if not (1 <= width <= 16384 and 1 <= height <= 16384 and width * height <= _MAX_PIXELS):
        raise ValueError("PNG exceeds the screenshot geometry limit")
    return width, height


def _read_png(path: Path) -> tuple[bytes, tuple[int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
            raise ValueError("Source must be a regular PNG file of at most 32 MiB")
        header = stream.read(24)
        size = _png_size(header)
        body = stream.read(_MAX_BYTES - len(header) + 1)
    if len(header) + len(body) > _MAX_BYTES:
        raise ValueError("Source PNG exceeds 32 MiB")
    return header + body, size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", type=Path, metavar="VIEWPORT.png")
    parser.add_argument("--viewport", type=int, nargs=2, required=True, metavar=("WIDTH", "HEIGHT"),
                        help="Measured CSS viewport dimensions for this capture")
    parser.add_argument("--json", action="store_true", help="Emit one JSON result object (the default)")
    args = parser.parse_args(argv)
    width, height = args.viewport
    result: dict[str, object] = {"schema_version": 1, "ok": False, "stage": "validation", "effect": "none"}
    code = 1
    try:
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            raise ValueError("CSS viewport dimensions must be integers from 1 to 4096")
        source, (source_width, source_height) = _read_png(args.screenshot.expanduser())
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            result.update(issue="dependency_unavailable", error="CSS screenshot conversion requires FFmpeg on PATH")
        else:
            result["stage"] = "convert"
            folder = Path(tempfile.mkdtemp(prefix="basaltwater-css-screenshot-"))
            output = folder / "viewport.png"
            try:
                subprocess.run([
                    ffmpeg, "-nostdin", "-v", "error", "-n", "-max_alloc", "134217728",
                    "-threads", "1", "-f", "png_pipe", "-c:v", "png", "-i", "pipe:0",
                    "-filter_threads", "1", "-vf", f"scale={width}:{height}:flags=area,setsar=1",
                    "-frames:v", "1", "-threads", "1", "-c:v", "png", "-f", "image2", "-update", "1", str(output),
                ], input=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
                with output.open("rb") as stream:
                    try:
                        output_size = _png_size(stream.read(24))
                    except ValueError as exc:
                        raise RuntimeError("FFmpeg did not produce a usable PNG") from exc
                if output_size != (width, height):
                    raise RuntimeError("Converted PNG does not match the requested viewport size")
                if os.name == "posix":
                    output.chmod(0o600)
            except BaseException:
                shutil.rmtree(folder)
                raise
            result = {"schema_version": 1, "ok": True,
                      "source_geometry": [source_width, source_height], "viewport_geometry": [width, height],
                      "image_to_css_scale": [width / source_width, height / source_height],
                      "artifacts": [{"kind": "css_viewport_screenshot", "path": str(output), "media_type": "image/png"}]}
            code = 0
    except ValueError as exc:
        result.update(issue="invalid_input", error=str(exc))
        code = 2
    except subprocess.TimeoutExpired:
        result.update(issue="wait_timeout", error="FFmpeg did not finish converting the screenshot within 30 seconds")
        code = 124
    except subprocess.CalledProcessError as exc:
        result.update(issue="conversion_failed", error="FFmpeg could not convert the source PNG", child_exit_code=exc.returncode)
    except (OSError, RuntimeError) as exc:
        result.update(issue="operation_failed", error=str(exc))
    print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
