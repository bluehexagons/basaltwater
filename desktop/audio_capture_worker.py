"""Exec the owned recorder with an independent alarm surviving parent loss."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.validation import validate_filesystem_path


def execute(seconds: int, token: str, output: str) -> None:
    if type(seconds) is not int or not 1 <= seconds <= 120 or not re.fullmatch(r"basaltwater-audio-[0-9a-f]{24}", token):
        raise ValueError("Invalid recorder budget or owned node name")
    validate_filesystem_path(output)
    path = Path(output)
    if not path.is_absolute() or path.name != "audio.wav" or path.exists() or path.is_symlink():
        raise ValueError("Recorder requires a new absolute audio.wav path")
    properties = {"node.name": token, "node.autoconnect": False, "node.dont-reconnect": True,
                  "stream.dont-remix": True}
    arguments = ["/usr/bin/pw-record", "--target", "0", "--properties", json.dumps(properties),
                 "--rate", "48000", "--channels", "2", "--channel-map", "FL,FR", "--format", "f32",
                 "--container", "wav", "--sample-count", str(seconds * 48000), output]
    # alarm remains armed across exec. SIGALRM's default action terminates even
    # when the controller vanished and the stream is no longer producing data.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(seconds + 15)
    os.execve(arguments[0], arguments, os.environ)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Expected duration, owned node token and output")
    execute(int(sys.argv[1]), sys.argv[2], sys.argv[3])
