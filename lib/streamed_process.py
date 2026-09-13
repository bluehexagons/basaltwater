"""Bounded bidirectional process streaming with shared group cleanup."""

from __future__ import annotations

import codecs
import os
import selectors
import subprocess
import time
from typing import Callable

from lib.remote_utils import (
    CommandTimeoutError, _command_text, _terminate_timed_out_process, _validate_timeout,
)


def run_streamed(
    command: list[str], *, timeout: float, on_output: Callable[[str], None],
    input_data: bytes | None = None, cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Relay UTF-8 output while feeding stdin, without unbounded pipe waits.

    Long lines are emitted in chunks to bound memory. Callbacks must return
    promptly. The deadline includes input delivery and inherited output pipes.
    """
    bound = _validate_timeout(timeout)
    if bound is None:
        raise ValueError("Streaming requires a finite deadline")
    deadline = time.monotonic() + bound
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=0, cwd=cwd, env=env, start_new_session=True,
    )
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    offset = 0
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CommandTimeoutError(_command_text(command), bound)
                for key, event in selector.select(remaining):
                    if event & selectors.EVENT_WRITE:
                        assert input_data is not None
                        try:
                            offset += os.write(key.fd, input_data[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(input_data)
                        if offset == len(input_data):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        pending += decoder.decode(chunk, final=not chunk)
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            on_output(line + "\n")
                        if not chunk or len(pending) >= 65536:
                            if pending:
                                on_output(pending)
                                pending = ""
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandTimeoutError(_command_text(command), bound)
            try:
                return process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise CommandTimeoutError(_command_text(command), bound) from exc
    except BaseException:
        _terminate_timed_out_process(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
