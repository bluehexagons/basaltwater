"""Bounded Guacamole instruction framing and fixed RDP handshake."""

from __future__ import annotations

import asyncio

MAX_INSTRUCTION = 65536


def instruction(*elements: str) -> str:
    """Lengths count Unicode code points, as required by the wire protocol."""
    return ",".join(f"{len(value)}.{value}" for value in elements) + ";"


class Parser:
    """Incrementally parse instructions with bounds on lengths and elements."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, data: str) -> list[list[str]]:
        self.buffer += data
        if len(self.buffer) > MAX_INSTRUCTION:
            raise ValueError("Guacamole instruction is too large")
        results = []
        position = 0
        elements: list[str] = []
        beginning = 0
        while position < len(self.buffer):
            dot = self.buffer.find(".", position)
            if dot < 0:
                if len(self.buffer) - position > 6:
                    raise ValueError("Invalid Guacamole element length")
                break
            length_text = self.buffer[position:dot]
            if not length_text or len(length_text) > 6 or any(c not in "0123456789" for c in length_text):
                raise ValueError("Invalid Guacamole element length")
            length = int(length_text)
            if length > MAX_INSTRUCTION:
                raise ValueError("Guacamole element is too large")
            end = dot + 1 + length
            if end >= len(self.buffer):
                break
            separator = self.buffer[end]
            if separator not in ",;":
                raise ValueError("Invalid Guacamole separator")
            elements.append(self.buffer[dot + 1:end])
            if len(elements) > 256:
                raise ValueError("Too many Guacamole elements")
            position = end + 1
            if separator == ";":
                results.append(elements)
                elements = []
                beginning = position
        self.buffer = self.buffer[beginning:]
        return results


async def read_instruction(reader: asyncio.StreamReader) -> list[str]:
    """Read exactly one handshake instruction, without consuming frame data."""
    elements = []
    total = 0
    while True:
        prefix = await reader.readuntil(b".")
        if len(prefix) > 7 or not prefix[:-1] or any(c not in b"0123456789" for c in prefix[:-1]):
            raise ValueError("Invalid Guacamole handshake")
        length = int(prefix[:-1])
        total += length + len(prefix) + 1
        if total > MAX_INSTRUCTION or len(elements) >= 256:
            raise ValueError("Guacamole handshake exceeds limits")
        # Handshake fields are ASCII except the submitted Unix password. Read
        # exactly the specified number of Unicode code points, including UTF-8.
        characters = []
        for _ in range(length):
            byte = await reader.readexactly(1)
            lead = byte[0]
            extra = 0 if lead < 128 else 1 if 0xC2 <= lead <= 0xDF else 2 if 0xE0 <= lead <= 0xEF else 3 if 0xF0 <= lead <= 0xF4 else -1
            if extra < 0:
                raise ValueError("Invalid handshake UTF-8")
            characters.append((byte + await reader.readexactly(extra)).decode("utf-8"))
        elements.append("".join(characters))
        separator = await reader.readexactly(1)
        if separator == b";":
            return elements
        if separator != b",":
            raise ValueError("Invalid Guacamole handshake separator")


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    username: str, password: str, certificate: str,
                    geometry: list[int]) -> str:
    """Never accept destinations, connection IDs or protocol options from a browser."""
    writer.write(instruction("select", "rdp").encode())
    await writer.drain()
    args = await read_instruction(reader)
    if len(args) < 2 or args[0] != "args" or args[1] != "VERSION_1_5_0":
        raise ValueError("Unexpected guacd protocol version")
    parameters = {
        "hostname": "127.0.0.1", "port": "3389", "username": username,
        "password": password, "security": "tls", "ignore-cert": "false",
        "cert-fingerprints": "sha256:" + certificate,
        "color-depth": "32", "resize-method": "", "disable-copy": "true",
        "disable-paste": "true", "enable-drive": "false", "enable-printing": "false",
        "enable-audio-input": "false", "disable-audio": "true", "enable-sftp": "false",
    }
    if "cert-fingerprints" not in args[2:]:
        raise ValueError("guacd lacks certificate pinning support")
    writer.write((instruction("size", str(geometry[0]), str(geometry[1]), "96")
                  + instruction("audio") + instruction("video")
                  + instruction("image", "image/png", "image/jpeg", "image/webp")
                  + instruction("connect", args[1], *(parameters.get(name, "") for name in args[2:]))).encode())
    await writer.drain()
    ready = await read_instruction(reader)
    if len(ready) != 2 or ready[0] != "ready" or not ready[1]:
        raise ValueError("XRDP connection was not established")
    return ready[1]


def filter_instruction(elements: list[str], controlled: bool) -> str | None:
    """View-only and transfer restrictions apply even to a modified client."""
    opcode, *args = elements
    counts = {"sync": 1, "nop": 0, "ack": 3, "key": 2, "mouse": 3, "disconnect": 0}
    if opcode not in counts or len(args) != counts[opcode]:
        raise ValueError("Browser instruction is not permitted")
    numeric = {"sync": [(0, 2**53 - 1)], "key": [(0, 0xFFFFFFFF), (0, 1)],
               "mouse": [(0, 16384), (0, 16384), (0, 31)]}
    for value, (minimum, maximum) in zip(args, numeric.get(opcode, ())):
        if not value or len(value) > 16 or any(c not in "0123456789" for c in value) or not minimum <= int(value) <= maximum:
            raise ValueError("Invalid browser input value")
    if opcode == "ack" and (len(args[1]) > 1024 or any(not a.isascii() or not a.isdigit() or len(a) > 5 for a in (args[0], args[2]))):
        raise ValueError("Invalid browser acknowledgement")
    if opcode in {"key", "mouse"} and not controlled:
        return None
    return instruction(*elements)
