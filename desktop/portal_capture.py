"""Capture one bounded RGBA PipeWire frame using the portal's passed descriptor."""

from __future__ import annotations

import json
import os
import struct
import sys
import zlib


def capture(fd: int, node: int, output_fd: int) -> list[int]:
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
        sample = pipeline.get_by_name("frame").emit("try-pull-sample", 8 * Gst.SECOND)
        if sample is None:
            raise RuntimeError("No frame from the selected monitor; inspect portal/PipeWire state")
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        width, height = info.width, info.height
        if not (0 < width <= 16384 and 0 < height <= 16384 and width * height <= 32 * 1024 * 1024):
            raise ValueError("Selected capture exceeds the 32-megapixel limit")
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
            chunks = []
            for row in range(height):
                start = offset + row * stride
                chunks.append(compressor.compress(b"\0" + mapped.data[start:start + width * 4]))
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
            stream.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)))
            stream.write(chunk(b"IDAT", compressed))
            stream.write(chunk(b"IEND", b""))
        return [width, height]
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    try:
        print(json.dumps({"geometry": capture(*(int(n) for n in sys.argv[1:]))}))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
