"""Offline source-pixel annotations with a separate, unchanged literal crop."""

from __future__ import annotations

import os
from pathlib import Path

from desktop.evidence_media import caption_text, crop, media_command
from desktop.portal_capture import validate_crop
from desktop.qa_artifacts import file_digest, stamp
from lib.atomic_io import read_json_file, write_json_atomic


def point_preview(source: str, rectangle: list[int], point: list[int], caption: str,
                  output: str | None = None) -> dict:
    """Mark one source-PNG pixel; this does not select or validate a live target."""
    validate_crop(rectangle)
    if rectangle is None or rectangle[2] > 1024 or rectangle[3] > 1024:
        raise ValueError("Point preview needs an explicit rectangle at most 1024 by 1024")
    if (not isinstance(point, list) or len(point) != 2 or any(type(n) is not int for n in point)
            or not rectangle[0] <= point[0] < rectangle[0] + rectangle[2]
            or not rectangle[1] <= point[1] < rectangle[1] + rectangle[3]):
        raise ValueError("Point must be two integer source-PNG pixels inside the preview rectangle")
    rendered_caption = caption_text(caption)
    literal = crop(source, rectangle, caption, output)
    folder = Path(literal["artifact"]).parent
    manifest = read_json_file(literal["artifact"], max_bytes=65536)
    x, y = point[0] - rectangle[0], point[1] - rectangle[1]
    # All filter values are bounded integers. Keep the center pixel and a cross
    # visible at image edges; clips affect annotations only, never the literal.
    boxes = []
    for left, top, width, height, color in ((x - 10, y - 1, 21, 3, "red"),
                                          (x - 1, y - 10, 3, 21, "red")):
        right, bottom = min(rectangle[2], left + width), min(rectangle[3], top + height)
        left, top = max(0, left), max(0, top)
        boxes.append(f"drawbox=x={left}:y={top}:w={right-left}:h={bottom-top}:color={color}:t=fill")
    media_command(["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-n", "-max_alloc", "134217728",
                   "-f", "png_pipe", "-i", str(folder / "crop.png"), "-frames:v", "1", "-vf",
                   ",".join(boxes), "-threads", "1", str(folder / "point-preview.png")])
    os.chmod(folder / "point-preview.png", 0o600)
    data = {"schema_version": 1, "kind": "point-preview", "created": stamp(),
            "source": manifest["source"], "source_geometry": manifest["source_geometry"],
            "literal_crop": {"artifact": "manifest.json", "sha256": file_digest(folder / "manifest.json")},
            "rectangle": rectangle, "point": point, "point_space": "source-image",
            "point_in_preview": [x, y],
            "derivative": {"artifact": "point-preview.png", "sha256": file_digest(folder / "point-preview.png")},
            "caption": caption, "review": "unreviewed", "input_sent": False, "input_authority": False,
            "freshness_renewed": False,
            "limit": "Annotated historical pixels only; inspect the marker and unchanged crop. Not target recognition, live capture, viewer-size proof or a usable observation. Add the capture origin only through its saved mapping receipt."}
    artifact, report = folder / "point-preview.json", folder / "point-preview.md"
    write_json_atomic(str(artifact), data, mode=0o600)
    with report.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(f"{rendered_caption}\n\nSource PNG point: {point[0]}, {point[1]}. "
                     "Red cross is an annotation; no input sent or freshness renewed.\n\n"
                     "![Annotated point preview](point-preview.png)\n\n"
                     "![Unchanged literal crop](crop.png)\n\n"
                     "[Point provenance](point-preview.json) · [Literal crop provenance](manifest.json)\n")
    return {"artifact": str(artifact), "report": str(report), "preview": str(folder / "point-preview.png"),
            "literal_crop": literal["artifact"], "point": point, "point_space": "source-image",
            "source_sha256": manifest["source"]["sha256"], "input_sent": False,
            "input_authority": False, "freshness_renewed": False}
