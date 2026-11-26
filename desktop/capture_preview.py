"""A private, bounded derivative of an existing desktop capture."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess

from desktop.evidence_media import media_command
from desktop.qa_artifacts import file_digest, new_bundle, snapshot

MAX_WIDTH = 2048


def validate_width(width: int) -> None:
    if type(width) is not int or not 1 <= width <= MAX_WIDTH:
        raise ValueError(f"Preview width must be from 1 through {MAX_WIDTH} pixels")


def png_geometry(path: Path) -> list[int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Expected a PNG with a standard IHDR header")
    return list(struct.unpack(">II", header[16:24]))


def make_preview(capture: dict, width: int) -> dict:
    validate_width(width)
    folder = new_bundle()
    try:
        source = folder / "source.png"
        original = snapshot(capture["output"], source)
        expected = capture.get("artifact_sha256")
        if expected is not None and original["sha256"] != expected:
            raise ValueError("Capture no longer matches its receipt")
        image_width, image_height = png_geometry(source)
        if (not 0 < image_width <= 16384 or not 0 < image_height <= 16384
                or image_width * image_height > 32 * 1024 * 1024):
            raise ValueError("Source exceeds the 32-megapixel capture limit")
        if [image_width, image_height] != capture.get("image_geometry", capture.get("geometry")):
            raise ValueError("Capture PNG geometry does not match its receipt")
        width = min(width, image_width)
        height = max(1, width * image_height // image_width)
        output = folder / "preview.png"
        media_command(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-max_alloc", "134217728",
                       "-f", "png_pipe", "-i", str(source), "-frames:v", "1", "-vf",
                       f"scale={width}:{height}:flags=neighbor", "-threads", "1", str(output)])
        os.chmod(output, 0o600)
        if png_geometry(output) != [width, height]:
            raise ValueError("Preview PNG geometry does not match the requested size")
        source.unlink()
        return {"output": str(output), "geometry": [width, height], "source_sha256": original["sha256"],
                "artifact_sha256": file_digest(output),
                "mapping": {"image_origin": capture.get("image_origin", [0, 0]),
                            "scale": [image_width / width, image_height / height], "rounding": "floor"}}
    except BaseException:
        shutil.rmtree(folder)
        raise


def with_preview(result: dict, width: int, *, followup: bool = False) -> dict:
    if "error" in result or result.get("ok") is False:
        return result
    capture = result["observation"] if followup else result
    try:
        capture["preview"] = make_preview(capture, width)
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        capture["preview_error"] = str(exc)
    return result
