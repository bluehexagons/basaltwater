"""Private bounded SSH exchanges for fixed Headscale service adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import selectors
import signal
import stat
import subprocess
import time

from lib.atomic_io import parse_json
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path
from lib.validators import validate_host

SERVICES = {
    "management": "/opt/basaltwater/common/service_tools/headscale_management.py",
    "client": "/opt/basaltwater/common/service_tools/headscale_client_remote.py",
    "dns": "/opt/basaltwater/common/service_tools/headscale_dns.py",
    "dns-authority": "/opt/basaltwater/common/service_tools/headscale_dns_authority.py",
    "dns-control": "/opt/basaltwater/common/service_tools/headscale_dns_control.py",
}
MAX_BYTES = 1024 * 1024


class TransportError(RuntimeError):
    """An exchange may have taken effect; never replay a mutation automatically."""


@dataclass(frozen=True)
class SSHBinding:
    host: str
    identity_file: str
    known_hosts_file: str
    port: int = 22

    def __post_init__(self):
        if not isinstance(self.host, str) or not validate_host(self.host):
            raise ValueError("Invalid explicit SSH management host")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Invalid SSH management port")
        for path in (self.identity_file, self.known_hosts_file):
            validate_filesystem_path(path)
            if not os.path.isabs(path) or any(part in {".", ".."} for part in path.split("/")):
                raise ValueError("SSH credential paths must be absolute without dot components")

    def to_dict(self) -> dict:
        return asdict(self)


def parse_ssh_binding(value: object) -> SSHBinding:
    if type(value) is not dict or set(value) != {"host", "identity_file", "known_hosts_file", "port"}:
        raise ValueError("Invalid explicit SSH binding")
    return SSHBinding(**value)


def _credential(path: str, *, private: bool) -> int:
    with directory_fd(os.path.dirname(path)) as parent:
        info = os.fstat(parent)
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ValueError("Unsafe SSH credential directory")
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()} or info.st_mode & (0o077 if private else 0o022)
                or not 0 < info.st_size <= MAX_BYTES):
            raise ValueError("Unsafe SSH credential file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def exchange(binding: SSHBinding, service: str, request: dict, *, timeout: int = 90) -> dict:
    """No logging; credentials stay descriptor-pinned and bodies stay on stdin."""
    if service not in SERVICES or type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("Invalid private transport operation")
    payload = json.dumps(request, separators=(",", ":")).encode()
    if len(payload) > 16384:
        raise ValueError("Private request exceeds limit")
    descriptors = []
    output = bytearray()
    process = None
    try:
        descriptors.append(_credential(binding.identity_file, private=True))
        descriptors.append(_credential(binding.known_hosts_file, private=False))
        identity, hosts = (f"/proc/{os.getpid()}/fd/{fd}" for fd in descriptors)
        options = {
            "IdentityFile": "none", "UserKnownHostsFile": hosts,
            "GlobalKnownHostsFile": "/dev/null", "StrictHostKeyChecking": "yes",
            "UpdateHostKeys": "no", "VerifyHostKeyDNS": "no", "CanonicalizeHostname": "no",
            "BatchMode": "yes", "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
            "PreferredAuthentications": "publickey", "IdentitiesOnly": "yes", "IdentityAgent": "none",
            "CertificateFile": "none", "ForwardAgent": "no", "RequestTTY": "no",
            "ClearAllForwardings": "yes", "ProxyCommand": "none", "ProxyJump": "none",
            "ControlMaster": "no", "ControlPath": "none", "ControlPersist": "no",
            "PermitLocalCommand": "no", "ConnectionAttempts": "1", "ConnectTimeout": "10", "LogLevel": "ERROR",
        }
        argv = ["/usr/bin/ssh", "-F", "/dev/null", "-p", str(binding.port), "-i", identity]
        for key, value in options.items():
            argv.extend(["-o", key + "=" + value])
        argv.extend(["-T", "-a", "--", "root@" + binding.host,
                     "/usr/bin/python3 -I " + SERVICES[service]])
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, pass_fds=tuple(descriptors),
                                   env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        deadline, sent, received = time.monotonic() + timeout, 0, 0
        with selectors.DefaultSelector() as selector:
            for stream, events, kind in ((process.stdin, selectors.EVENT_WRITE, "in"),
                                         (process.stdout, selectors.EVENT_READ, "out"),
                                         (process.stderr, selectors.EVENT_READ, "err")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, events, kind)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("Private SSH result unconfirmed; reconcile before retry")
                for key, _ in selector.select(min(1, remaining)):
                    if key.data == "in":
                        try:
                            sent += os.write(key.fd, payload[sent:sent + 4096])
                        except BrokenPipeError:
                            sent = len(payload)
                        if sent == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            received += len(chunk)
                            if received > MAX_BYTES:
                                raise TransportError("Private SSH response exceeds limit; reconcile")
                            if key.data == "out":
                                output.extend(chunk)
            code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        result = parse_json(output.decode())
        if (code or type(result) is not dict or set(result) != {"ok", "result"}
                or result["ok"] is not True or type(result["result"]) is not dict):
            raise TransportError("Private SSH result unconfirmed; reconcile before retry")
        return result["result"]
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise TransportError("Private SSH operation unavailable or unconfirmed; reconcile before retry") from None
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        for fd in descriptors:
            os.close(fd)
