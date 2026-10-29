"""Ephemeral controller-only HTTPS credentials for source preparation."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import os
import pwd
import selectors
import signal
import subprocess
import tempfile
import time
from typing import Iterator
from urllib.parse import urlsplit

from lib.build_source import CommandRunner
from lib.git_credentials import normalize_git_https_origin
from lib.validation import validate_filesystem_path


MAX_CREDENTIAL_BYTES = 16384


def _capture(command, *, cwd, env, input_data, timeout, max_output_bytes,
             file_size_limit=None):
    """Capture helper output in bounded memory, never a spool or diagnostic."""
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True, umask=0o077,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        pending = memoryview(input_data)
        output = bytearray()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Controller credential helper timed out")
                for key, _events in selector.select(remaining):
                    if key.fileobj is process.stdin:
                        pending = pending[os.write(process.stdin.fileno(), pending[:4096]):]
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(),
                                        min(4096, max_output_bytes + 1 - len(output)))
                        if not chunk:
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > max_output_bytes:
                            raise ValueError("Controller credential output exceeds its limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                raise ValueError("Controller credential helper failed")
        return bytes(output)
    finally:
        # Helpers can spawn children; stop the entire isolated session as well.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


@contextmanager
def controller_https_credentials(
    url: str, directory: str, *, timeout: float, runner: CommandRunner | None = None,
) -> Iterator[str]:
    """Yield a private Git global file for this URL, then always remove it.

    Only trusted controller global helpers are consulted, from a neutral cwd.
    Neither credentials nor the controller environment reach the clone runner.
    """
    parsed = urlsplit(url)
    origin = normalize_git_https_origin(f"{parsed.scheme}://{parsed.netloc}")
    if (len(url) > 4096 or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not parsed.path.startswith("/")
            or parsed.path == "/" or any(ord(c) < 32 or ord(c) == 127 for c in url)):
        raise ValueError("Controller credentials require a credential-free HTTPS repository URL")
    validate_filesystem_path(directory, must_exist=True)
    home = pwd.getpwuid(os.geteuid()).pw_dir
    environment = {
        "PATH": os.defpath, "HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "LANG": "C", "LC_ALL": "C", "TMPDIR": directory,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false", "GCM_INTERACTIVE": "never",
        "GH_PROMPT_DISABLED": "1",
        "GIT_CEILING_DIRECTORIES": directory,
    }
    # gh's configured helper may need a custom config directory or token. These
    # are intentionally available only to credential fill, never clone/snapshot.
    for name in ("GH_CONFIG_DIR", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
                 "GITHUB_ENTERPRISE_TOKEN"):
        if name in os.environ:
            environment[name] = os.environ[name]
    host = urlsplit(origin).netloc
    path = parsed.path[1:]
    request = f"protocol=https\nhost={host}\npath={path}\n\n".encode()
    try:
        with tempfile.TemporaryDirectory(prefix=".credential-helper-", dir=directory) as neutral:
            raw = (runner or _capture)(
                ["git", "-c", "credential.interactive=false", "-c", "credential.useHttpPath=true",
                 "-c", "core.askPass=/bin/false", "credential", "fill"],
                cwd=neutral, env=environment, input_data=request,
                timeout=min(timeout, 30), max_output_bytes=MAX_CREDENTIAL_BYTES,
                file_size_limit=MAX_CREDENTIAL_BYTES,
            )
        if not isinstance(raw, bytes) or len(raw) > MAX_CREDENTIAL_BYTES:
            raise ValueError("Invalid credential output")
        fields = {}
        for line in raw.decode("utf-8").splitlines():
            if not line:
                continue
            key, value = line.split("=", 1)
            if key in fields or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Invalid credential output")
            fields[key] = value
        if (fields.get("protocol") != "https" or fields.get("host") != host
                or fields.get("path") != path or not fields.get("username")
                or not fields.get("password") or ":" in fields["username"]):
            raise ValueError("Invalid credential scope or fields")
        encoded = base64.b64encode(
            (fields["username"] + ":" + fields["password"]).encode()).decode("ascii")
    except Exception:
        raise ValueError("Controller HTTPS credential helper unavailable or returned invalid credentials") from None
    # Git config subsection quoting is distinct from credential protocol framing.
    scoped_url = (origin + parsed.path).replace("\\", "\\\\").replace('"', '\\"')
    descriptor, filename = tempfile.mkstemp(prefix=".clone-credentials-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f'[http "{scoped_url}"]\n\textraHeader = Authorization: Basic {encoded}\n')
        yield filename
    finally:
        os.unlink(filename)
