"""Capture one bounded RGBA PipeWire frame using the portal's passed descriptor."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import hashlib
import os
import struct
import sys
import time
import zlib


def validate_crop(crop: list | None, geometry: list | None = None) -> None:
    if crop is None:
        return
    if (not isinstance(crop, list) or len(crop) != 4 or any(type(n) is not int for n in crop)
            or min(crop[:2]) < 0 or min(crop[2:]) <= 0 or max(crop) > 16384):
        raise ValueError("Crop must be integer [x, y, width, height] within the selected monitor")
    if geometry is not None and (crop[0] + crop[2] > geometry[0] or crop[1] + crop[3] > geometry[1]):
        raise ValueError("Crop lies outside the selected monitor")


def capture(fd: int, node: int, output_fd: int, next_frame: int = 0, crop: list | None = None) -> dict:
    if next_frame not in (0, 1):
        raise ValueError("next_frame must be 0 or 1")
    validate_crop(crop)
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst, GstVideo

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={fd} path={node} do-timestamp=true ! videoconvert ! "
        "video/x-raw,format=RGBA ! appsink name=frame max-buffers=1 drop=true sync=false"
    )
    try:
        pipeline.set_state(Gst.State.PLAYING)
        sink = pipeline.get_by_name("frame")
        deadline = time.monotonic() + 8
        sample = sink.emit("try-pull-sample", 8 * Gst.SECOND)
        if sample is None:
            raise RuntimeError("No frame from the selected monitor; inspect portal/PipeWire state")
        if next_frame:
            sample = sink.emit("try-pull-sample", int(max(0, deadline - time.monotonic()) * Gst.SECOND))
            if sample is None:
                raise RuntimeError("No subsequent frame from the selected monitor; application/render readiness is unverified")
        received_at = datetime.now(timezone.utc).isoformat()
        received_monotonic_ns = time.monotonic_ns()
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        width, height = info.width, info.height
        if not (0 < width <= 16384 and 0 < height <= 16384 and width * height <= 32 * 1024 * 1024):
            raise ValueError("Selected capture exceeds the 32-megapixel limit")
        validate_crop(crop, [width, height])
        x, y, image_width, image_height = crop if crop is not None else (0, 0, width, height)
        buffer = sample.get_buffer()
        if buffer.get_size() > 160 * 1024 * 1024:
            raise ValueError("Frame exceeds the capture buffer limit")
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError("Cannot read the selected frame")
        try:
            stride, offset = info.stride[0], info.offset[0]
            if stride < width * 4 or len(mapped.data) < offset + stride * (height - 1) + width * 4:
                raise ValueError("Invalid RGBA frame layout")
            compressor = zlib.compressobj()
            digest = hashlib.sha256()
            chunks = []
            for row in range(y, y + image_height):
                start = offset + row * stride + x * 4
                pixels = mapped.data[start:start + image_width * 4]
                digest.update(pixels)
                chunks.append(compressor.compress(b"\0" + pixels))
            chunks.append(compressor.flush())
            compressed = b"".join(chunks)
        finally:
            buffer.unmap(mapped)
        if len(compressed) > 64 * 1024 * 1024:
            raise ValueError("PNG exceeds the 64 MiB artifact limit")

        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

        with os.fdopen(os.dup(output_fd), "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")
            stream.write(chunk(b"IHDR", struct.pack(">IIBBBBB", image_width, image_height, 8, 6, 0, 0, 0)))
            stream.write(chunk(b"IDAT", compressed))
            stream.write(chunk(b"IEND", b""))
        return {"geometry": [width, height], "image_geometry": [image_width, image_height],
                "image_origin": [x, y], "pixel_sha256": digest.hexdigest(),
                "frame_received_at": received_at, "frame_received_monotonic_ns": received_monotonic_ns,
                "frame_pts_ns": None if buffer.pts == Gst.CLOCK_TIME_NONE else buffer.pts,
                "initial_frame_skipped": bool(next_frame)}
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    try:
        if len(sys.argv) not in (4, 5, 9):
            raise ValueError("Expected fd, node, output fd, optional next-frame and crop")
        print(json.dumps(capture(*(int(n) for n in sys.argv[1:4]),
                                 next_frame=int(sys.argv[4]) if len(sys.argv) >= 5 else 0,
                                 crop=[int(n) for n in sys.argv[5:]] or None)))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
