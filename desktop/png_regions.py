"""Bounded standard-library PNG decoding for opaque scene-region prototypes."""

from __future__ import annotations

import struct
import zlib

MAX_PIXELS = 4 * 1024 * 1024


def decode(content):
    """Read noninterlaced 8-bit RGB/RGBA PNGs; reject unsupported media."""
    if not isinstance(content, bytes) or not 33 <= len(content) <= 16 * 1024 * 1024 or content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Expected bounded PNG bytes")
    position, compressed, header, ended, idat_ended = 8, bytearray(), None, False, False
    while position < len(content):
        if position + 12 > len(content):
            raise ValueError("Truncated PNG chunk")
        length = struct.unpack(">I", content[position:position + 4])[0]
        kind = content[position + 4:position + 8]
        end = position + 12 + length
        if end > len(content):
            raise ValueError("PNG chunk exceeds input")
        data = content[position + 8:end - 4]
        if zlib.crc32(kind + data) != struct.unpack(">I", content[end - 4:end])[0]:
            raise ValueError("PNG CRC mismatch")
        if header is None:
            if kind != b"IHDR" or length != 13:
                raise ValueError("PNG must begin with IHDR")
            width, height, bits, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if not 0 < width <= 16384 or not 0 < height <= 16384 or width * height > MAX_PIXELS:
                raise ValueError("PNG exceeds 4 megapixels")
            if bits != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError("Only noninterlaced 8-bit RGB/RGBA PNG is supported")
            header = width, height, 3 if color == 2 else 4
        elif kind == b"IDAT":
            if idat_ended:
                raise ValueError("PNG IDAT chunks must be consecutive")
            compressed.extend(data)
        elif kind == b"IEND":
            if length or end != len(content) or not compressed:
                raise ValueError("Invalid PNG end")
            ended = True
            break
        elif kind == b"IHDR" or kind == b"acTL" or kind == b"tRNS" or not kind[0] & 32:
            raise ValueError("Unsupported PNG critical/animation/transparency chunk")
        elif compressed:
            idat_ended = True
        position = end
    if not ended:
        raise ValueError("PNG has no IEND")
    width, height, channels = header
    stride = width * channels
    expected = (stride + 1) * height
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise ValueError("Invalid compressed PNG") from exc
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("PNG decompression size/stream mismatch")
    pixels, prior = bytearray(), bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        if method > 4:
            raise ValueError("Unsupported PNG filter")
        if method:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                above = prior[x]
                diagonal = prior[x - channels] if x >= channels else 0
                if method == 1:
                    prediction = left
                elif method == 2:
                    prediction = above
                elif method == 3:
                    prediction = (left + above) // 2
                else:
                    p = left + above - diagonal
                    distances = abs(p - left), abs(p - above), abs(p - diagonal)
                    prediction = (left, above, diagonal)[distances.index(min(distances))]
                row[x] = (row[x] + prediction) & 255
        if channels == 4:
            if any(alpha != 255 for alpha in row[3::4]):
                raise ValueError("Transparent PNGs are unsupported by the scene prototype")
            for x in range(0, stride, 4):
                pixels.extend(row[x:x + 3])
        else:
            pixels.extend(row)
        prior = row
    return width, height, bytes(pixels)


def region(image, rectangle):
    width, height, pixels = image
    x, y, w, h = rectangle
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError("Region outside decoded PNG")
    return b"".join(pixels[((y + row) * width + x) * 3:((y + row) * width + x + w) * 3] for row in range(h))


def encode(width, height, pixels):
    """Encode literal opaque RGB crops for evidence, without resampling."""
    if type(width) is not int or type(height) is not int or not 0 < width * height <= MAX_PIXELS or width <= 0 or height <= 0 or len(pixels) != width * height * 3:
        raise ValueError("Invalid RGB crop geometry")
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
