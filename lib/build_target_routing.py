"""Root-owned serving policy shared by artifact activation and setup replay.

The controller installs this through administrative setup. It is never part of
a build request, artifact, or authenticated deployment projection.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import stat
from typing import TYPE_CHECKING

from lib.atomic_io import write_bytes_atomic
from lib.build_client import _file_identity, _open_private_directory
from lib.build_protocol import decode_document, encode_document
from lib.validation import validate_ssl_email

if TYPE_CHECKING:
    from lib.config import SetupConfig


_POLICY = "routing.json"
_WITNESS = ".routing-initialized"
_INITIALIZED = b"basaltwater-target-routing-v1\n"
_MAX_BYTES = 4096


@dataclass(frozen=True)
class TargetRouting:
    cloudflare: bool = False
    ssl: bool = False
    ssl_email: str | None = None

    def document(self) -> dict:
        return {"schema": 1, "cloudflare": self.cloudflare,
                "ssl": self.ssl, "ssl_email": self.ssl_email}


def routing_for_config(config: SetupConfig) -> TargetRouting:
    """Select only administrator-owned serving settings from a setup profile."""
    for value in (config.enable_cloudflare, config.enable_ssl):
        if value is not None and type(value) is not bool:
            raise ValueError("Artifact routing options must be booleans")
    validate_ssl_email(config.ssl_email)
    return TargetRouting(bool(config.enable_cloudflare), bool(config.enable_ssl), config.ssl_email)


def _read(directory: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=directory)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > _MAX_BYTES):
            raise ValueError("Unsafe retained target routing policy")
        with os.fdopen(os.dup(descriptor), "rb") as source:
            raw = source.read(_MAX_BYTES + 1)
        if (len(raw) != before.st_size or len(raw) > _MAX_BYTES
                or _file_identity(before) != _file_identity(os.fstat(descriptor))
                or _file_identity(before) != _file_identity(os.stat(name, dir_fd=directory,
                                                                   follow_symlinks=False))):
            raise ValueError("Retained target routing changed while reading")
        return raw
    finally:
        os.close(descriptor)


def load_target_routing(state_root: str) -> TargetRouting:
    """Read protected routing; a missing initialized policy always refuses."""
    directory = _open_private_directory(state_root)
    try:
        witness = _read(directory, _WITNESS)
        raw = _read(directory, _POLICY)
    finally:
        os.close(directory)
    if witness not in {None, _INITIALIZED}:
        raise ValueError("Invalid retained target routing witness")
    if raw is None:
        if witness is not None:
            raise ValueError("Retained target routing policy is missing; reconcile from the controller")
        return TargetRouting()
    value = decode_document(raw, maximum=_MAX_BYTES)
    if (not isinstance(value, dict) or set(value) != {"schema", "cloudflare", "ssl", "ssl_email"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or any(type(value[key]) is not bool for key in ("cloudflare", "ssl"))
            or value["ssl_email"] is not None and not isinstance(value["ssl_email"], str)):
        raise ValueError("Invalid retained target routing policy")
    validate_ssl_email(value["ssl_email"])
    return TargetRouting(value["cloudflare"], value["ssl"], value["ssl_email"])


def save_target_routing(state_root: str, config: SetupConfig) -> None:
    """Persist setup-owned policy under the caller's exclusive target lease."""
    routing = routing_for_config(config)
    # Validate existing files and their ancestry before an atomic replacement.
    load_target_routing(state_root)
    write_bytes_atomic(os.path.join(state_root, _POLICY),
                       encode_document(routing.document(), maximum=_MAX_BYTES), mode=0o600)
    write_bytes_atomic(os.path.join(state_root, _WITNESS), _INITIALIZED, mode=0o600)
