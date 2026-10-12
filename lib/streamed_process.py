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
    command: list[str], *, timeout: float | None, on_output: Callable[[str], None],
    input_data: bytes | None = None, cwd: str | None = None,
    env: dict[str, str] | None = None,
    interactive: bool = False,
    on_error: Callable[[str], None] | None = None,
    discard_stderr: bool = False,
) -> int:
    """Relay UTF-8 output while feeding stdin, without unbounded pipe waits.

    Long lines are emitted in chunks to bound memory. Callbacks must return
    promptly. The deadline includes input delivery and inherited output pipes.
    Interactive children retain the controlling terminal (for SSH passphrase
    prompts via /dev/tty), while stdin remains reserved for the payload. Only
    interactive callers may omit the deadline to allow time for a prompt.
    """
    bound = _validate_timeout(timeout)
    if on_error is not None and discard_stderr:
        raise ValueError("Cannot capture and discard stderr together")
    if bound is None and not interactive:
        raise ValueError("Streaming requires a finite deadline")
    deadline = time.monotonic() + bound if bound is not None else None
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if discard_stderr else subprocess.PIPE if on_error is not None else subprocess.STDOUT,
        bufsize=0, cwd=cwd, env=env, start_new_session=not interactive,
    )
    offset = 0
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            readers = {process.stdout: (codecs.getincrementaldecoder("utf-8")(errors="replace"), "", on_output)}
            if on_error is not None:
                assert process.stderr is not None
                os.set_blocking(process.stderr.fileno(), False)
                selector.register(process.stderr, selectors.EVENT_READ)
                readers[process.stderr] = (codecs.getincrementaldecoder("utf-8")(errors="replace"), "", on_error)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
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
                        decoder, pending, callback = readers[key.fileobj]
                        pending += decoder.decode(chunk, final=not chunk)
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            callback(line + "\n")
                        if not chunk or len(pending) >= 65536:
                            if pending:
                                callback(pending)
                                pending = ""
                        readers[key.fileobj] = (decoder, pending, callback)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise CommandTimeoutError(_command_text(command), bound)
            try:
                return process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise CommandTimeoutError(_command_text(command), bound) from exc
    except BaseException:
        # Interactive SSH shares the caller's process group. Never signal that
        # group on cancellation or callback failure: it includes the caller.
        _terminate_timed_out_process(process, isolated=not interactive)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr if on_error is not None else None):
            if stream is not None:
                stream.close()


class CommandOutputLimitError(subprocess.SubprocessError):
    """A bounded capture exceeded its shared stdout/stderr byte budget."""


def run_captured(
    command: list[str], *, timeout: float, max_bytes: int = 256 * 1024,
    input_data: bytes | None = None, cwd: str | None = None,
    env: dict[str, str] | None = None, capture_stderr: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Capture through the shared streaming supervisor, never unlimited pipes."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Capture byte limit must be a positive integer")
    output: list[str] = []
    errors: list[str] = []
    used = 0
    def append(target: list[str], text: str) -> None:
        nonlocal used
        used += len(text.encode("utf-8"))
        if used > max_bytes:
            raise CommandOutputLimitError(f"Command output exceeded {max_bytes} bytes")
        target.append(text)
    try:
        returncode = run_streamed(
            command, timeout=timeout, on_output=lambda text: append(output, text),
            on_error=(lambda text: append(errors, text)) if capture_stderr else None,
            discard_stderr=not capture_stderr, input_data=input_data, cwd=cwd, env=env,
        )
    except CommandTimeoutError as exc:
        # Preserve the captured-subprocess timeout interface used by callers.
        raise subprocess.TimeoutExpired(command, timeout) from exc
    return subprocess.CompletedProcess(command, returncode, "".join(output), "".join(errors))
