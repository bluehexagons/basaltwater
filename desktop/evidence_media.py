"""Literal PNG crops, WAV excerpts and reviewed portable evidence bundles."""

from __future__ import annotations

import html
import os
from pathlib import Path
import struct

from desktop.portal_capture import validate_crop
from desktop.qa_artifacts import clock, file_digest, new_bundle, snapshot, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.streamed_process import run_captured
from lib.validation import validate_no_control_characters


def media_command(arguments: list[str], *, timeout: float = 30) -> str:
    result = run_captured(arguments, timeout=timeout, max_bytes=1024 * 1024,
                          env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if result.returncode:
        raise RuntimeError("Media tool failed: " + result.stderr[-1000:])
    return result.stdout


def caption_text(caption: str) -> str:
    if not isinstance(caption, str):
        raise ValueError("Caption/reviewer must be text")
    validate_no_control_characters(caption, "Caption")
    if not 1 <= len(caption) <= 1024:
        raise ValueError("Caption must contain 1–1024 characters")
    return html.escape(caption).replace("[", "&#91;").replace("]", "&#93;")


def crop(source: str, rectangle: list[int], caption: str, output: str | None = None) -> dict:
    rendered_caption = caption_text(caption)
    validate_crop(rectangle)
    folder = new_bundle(output)
    saved = folder / "source.png"
    original = snapshot(source, saved)
    with saved.open("rb") as stream:
        header = stream.read(33)
    if len(header) != 33 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Expected a PNG with a standard IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > 32 * 1024 * 1024:
        raise ValueError("Source exceeds the 32-megapixel limit")
    validate_crop(rectangle, [width, height])
    x, y, w, h = rectangle
    media_command(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-max_alloc", "134217728",
                   "-f", "png_pipe", "-i", str(saved), "-frames:v", "1", "-vf",
                   f"crop={w}:{h}:{x}:{y}:exact=1", "-threads", "1", str(folder / "crop.png")])
    os.chmod(folder / "crop.png", 0o600)
    data = {"schema_version": 1, "kind": "literal-crop", "created": stamp(), "clock": clock(),
            "source": original, "source_geometry": [width, height], "rectangle": rectangle,
            "derivative": {"artifact": "crop.png", "sha256": file_digest(folder / "crop.png")},
            "caption": caption, "review": "unreviewed", "tool": "ffmpeg crop exact=1, one frame"}
    write_json_atomic(str(folder / "manifest.json"), data, mode=0o600)
    with (folder / "report.md").open("x") as report:
        os.fchmod(report.fileno(), 0o600)
        report.write(f"{rendered_caption}\n\n![Literal crop](crop.png)\n\n[Source](source.png) · [Provenance](manifest.json)\n")
    return {"artifact": str(folder / "manifest.json"), "report": str(folder / "report.md"), "review": "unreviewed"}


def excerpt(source: str, start: float, seconds: float, output: str | None = None) -> dict:
    from desktop.audio_analysis import audio_metadata
    if not 0 <= start <= 120 or not 0 < seconds <= 10 or start + seconds > 120:
        raise ValueError("Excerpt must start at 0–120 seconds and last at most 10 seconds")
    folder = new_bundle(output)
    original = snapshot(source, folder / "source.wav")
    metadata = audio_metadata(folder / "source.wav")
    if start + seconds > metadata["duration_seconds"]:
        raise ValueError("Excerpt extends beyond the source audio")
    media_command(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-f", "wav", "-i",
                   str(folder / "source.wav"), "-ss", str(start), "-t", str(seconds), "-map", "0:a:0",
                   "-c:a", "pcm_f32le", str(folder / "excerpt.wav")])
    os.chmod(folder / "excerpt.wav", 0o600)
    data = {"schema_version": 1, "kind": "audio-excerpt", "source": original,
            "interval_seconds": [start, start + seconds], "created": stamp(), "clock": clock(),
            "derivative": {"artifact": "excerpt.wav", "sha256": file_digest(folder / "excerpt.wav")},
            "review": "unreviewed", "tool": "ffmpeg PCM float WAV; no normalization"}
    write_json_atomic(str(folder / "manifest.json"), data, mode=0o600)
    return {"artifact": str(folder / "manifest.json"), "review": "unreviewed"}


def export_reviewed(source: str, output: str, reviewer: str) -> dict:
    """Export known derivative files only after explicit human/operator review."""
    caption_text(reviewer)
    manifest = read_json_file(source, max_bytes=65536)
    kind = manifest.get("kind") if isinstance(manifest, dict) else None
    if not isinstance(kind, str) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Expected a supported derivative manifest")
    names = {"literal-crop": ("source.png", "crop.png", "report.md"),
             "audio-excerpt": ("source.wav", "excerpt.wav")}.get(kind)
    if names is None:
        raise ValueError("Expected a supported derivative manifest")
    folder, origin = new_bundle(output), Path(source).absolute().parent
    for key, name in (("source", names[0]), ("derivative", names[1])):
        entry = manifest.get(key)
        if not isinstance(entry, dict) or entry.get("artifact") != name:
            raise ValueError("Invalid portable media reference")
        copied = snapshot(str(origin / name), folder / name)
        if copied["sha256"] != entry.get("sha256"):
            raise ValueError("Media no longer matches its manifest")
    # Regenerate Markdown from the bounded caption; do not copy arbitrary report files.
    if kind == "literal-crop":
        geometry = manifest.get("source_geometry")
        if (not isinstance(geometry, list) or len(geometry) != 2
                or any(type(value) is not int or not 0 < value <= 16384 for value in geometry)):
            raise ValueError("Invalid crop source geometry")
        validate_crop(manifest.get("rectangle"), geometry)
        if manifest.get("rectangle") is None:
            raise ValueError("Missing crop rectangle")
        with (folder / "report.md").open("x") as report:
            os.fchmod(report.fileno(), 0o600)
            report.write(caption_text(manifest.get("caption", "")) +
                         "\n\n![Literal crop](crop.png)\n\n[Source](source.png) · [Provenance](manifest.json)\n")
    # Only known portable fields cross this boundary. No original absolute paths.
    exported = {key: manifest[key] for key in ("schema_version", "kind", "source", "derivative")}
    for key in ("source_geometry", "rectangle", "interval_seconds", "caption"):
        if key in manifest:
            exported[key] = manifest[key]
    for key in ("source", "derivative"):
        exported[key] = {field: exported[key][field] for field in ("artifact", "sha256")}
    exported.update(review="operator-reviewed", reviewer=reviewer, reviewed_at=stamp()["at"])
    write_json_atomic(str(folder / "manifest.json"), exported, mode=0o600)
    return {"artifact": str(folder / "manifest.json"), "review": "operator-reviewed",
            "included_originals": [names[0]], "included_derivatives": [names[1]],
            "included_files": [*names, "manifest.json"]}
