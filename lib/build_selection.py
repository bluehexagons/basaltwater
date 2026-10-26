"""Read-only controller selection and explicit managed SSH identity binding."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import os
import stat
from typing import TypedDict

from common.build_host_steps import _public_ed25519_key, parse_build_host_enrollment
from common.build_target_steps import parse_build_target_enrollment
from lib.build_client import _absolute_path, _file_identity, _open_trusted_file
from lib.cache import load_all_setup_commands
from lib.config import SetupConfig
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host, validate_username
from lib.workspace import get_known_hosts_path


MAX_PUBLIC_KEY_BYTES = 16 * 1024


class SSHConnectionKwargs(TypedDict):
    """Connection keywords shared by the builder and target clients."""

    host: str
    username: str
    port: int
    identity_file: str
    known_hosts_file: str


@dataclass(frozen=True)
class SSHEndpoint:
    """Local transport selection; principal is metadata, never a wire field."""

    host: str
    username: str
    identity_file: str
    known_hosts_file: str
    principal: str
    port: int = 22

    def connection_kwargs(self) -> SSHConnectionKwargs:
        """Return client constructor keywords; the caller supplies timeout."""
        return {
            "host": self.host, "username": self.username, "port": self.port,
            "identity_file": self.identity_file,
            "known_hosts_file": self.known_hosts_file,
        }


@dataclass(frozen=True)
class BuilderSelection:
    """One saved configuration and its resolved controller-local endpoint."""

    config: SetupConfig
    endpoint: SSHEndpoint


def _validate_reference(reference: str) -> None:
    if (not isinstance(reference, str) or not 1 <= len(reference) <= 128
            or reference != reference.strip()):
        raise ValueError("Build-on reference must contain 1 to 128 characters without surrounding whitespace")
    validate_no_control_characters(reference, "Build-on reference")


def select_saved_builder(reference: str, configs: Sequence[SetupConfig]) -> SetupConfig:
    """Pure exact, case-insensitive host/name/tag selection across all records.

    Match before checking eligibility: an ineligible record cannot hide an
    ambiguous reference. Repeated matching labels within one record count once.
    """
    _validate_reference(reference)
    needle = reference.casefold()
    matches = [config for config in configs if any(
        needle == label.casefold()
        for label in (config.host, config.friendly_name, *(config.tags or []))
        if label is not None
    )]
    if not matches:
        raise ValueError("No saved setup matches the build-on reference; save an enrolled builder first")
    if len(matches) != 1:
        raise ValueError("Ambiguous build-on reference; use a unique saved host, friendly name, or tag")
    return matches[0]


def _supported_machine(config: SetupConfig) -> None:
    if config.machine_type not in {"vm", "hardware"}:
        raise ValueError("Managed build transport requires an explicitly saved Debian VM or hardware host")


def _local_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty text path")
    validate_filesystem_path(value)
    # Preserve the saved selector: expand ~, but reject relative paths and '..'.
    return _absolute_path(os.path.expanduser(value), label)


def _check_private_identity(path: str) -> None:
    # Metadata only: never parse, hash, or derive a public key from private bytes.
    info = os.stat(path, follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("SSH identity file must be an owner-private, single-link regular file")


def _public_companion(path: str) -> str:
    descriptor, identity = _open_trusted_file(path, "SSH public companion", private=False)
    try:
        if os.fstat(descriptor).st_size > MAX_PUBLIC_KEY_BYTES:
            raise ValueError("SSH public companion exceeds the bounded key-file limit")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_PUBLIC_KEY_BYTES + 1)
        if len(raw) > MAX_PUBLIC_KEY_BYTES:
            raise ValueError("SSH public companion exceeds the bounded key-file limit")
        if (_file_identity(os.fstat(descriptor)) != identity
                or _file_identity(os.stat(path, follow_symlinks=False)) != identity):
            raise ValueError("SSH public companion changed during selection")
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("SSH public companion must be UTF-8") from exc
    # ssh-keygen emits one optional trailing comment and a terminal newline.
    # Require the key type at the beginning: never strip authorized_keys options.
    line = text.removesuffix("\n")
    validate_no_control_characters(line, "SSH public companion")
    fields = line.split(" ", 2)
    if len(fields) < 2:
        raise ValueError("SSH public companion must contain one ssh-ed25519 public key")
    key, _blob = _public_ed25519_key(" ".join(fields[:2]))
    return key


def _endpoint(config: SetupConfig, keys: dict[str, str], prefix: str,
              workspace: str | None) -> SSHEndpoint:
    if not validate_host(config.host):
        raise ValueError("Invalid saved SSH host")
    identity_file = _local_path(
        config.ssh_key if config.ssh_key is not None else "~/.ssh/id_ed25519",
        "SSH identity file",
    )
    _check_private_identity(identity_file)
    public_key = _public_companion(identity_file + ".pub")
    principals = [principal for principal, key in keys.items() if key == public_key]
    if not principals:
        raise ValueError(
            "Selected controller SSH identity is not enrolled; enroll its .pub key "
            "for this service or explicitly update the saved ssh_key to an enrolled identity"
        )
    if len(principals) != 1:
        raise ValueError("Controller SSH identity matches multiple enrollment principals")
    principal = principals[0]
    username = prefix + hashlib.sha256(principal.encode("ascii")).hexdigest()[:12]
    if not validate_username(username):
        raise ValueError("Invalid derived SSH transport username")
    known_hosts_file = _local_path(get_known_hosts_path(workspace), "SSH known-hosts file")
    # Check trust-file safety without reading keys, scanning a host, or creating it.
    descriptor, _identity = _open_trusted_file(known_hosts_file, "SSH known-hosts file", private=False)
    os.close(descriptor)
    return SSHEndpoint(
        host=config.host, username=username, identity_file=identity_file,
        known_hosts_file=known_hosts_file, principal=principal,
    )


def resolve_builder_endpoint(config: SetupConfig, *, workspace: str | None = None) -> SSHEndpoint:
    """Bind an enrolled saved builder to exactly its selected local SSH key."""
    if config.disable_build_host is not False or config.is_build_server is not True:
        raise ValueError("Saved setup must be an enabled managed build server")
    if config.build_host_enrollment is None:
        raise ValueError("Legacy bare build-server setup is not managed; save build-host enrollment first")
    _supported_machine(config)
    enrollment = parse_build_host_enrollment(config.build_host_enrollment)
    return _endpoint(config, enrollment["keys"], "bw-build-", workspace)


def resolve_saved_builder(reference: str, *, workspace: str | None = None) -> BuilderSelection:
    """Load the full workspace inventory, select once, and resolve its endpoint."""
    _validate_reference(reference)
    config = select_saved_builder(reference, load_all_setup_commands(workspace))
    return BuilderSelection(config, resolve_builder_endpoint(config, workspace=workspace))


def resolve_target_endpoint(config: SetupConfig, *, workspace: str | None = None) -> SSHEndpoint:
    """Resolve the typed root-service endpoint from the passed target config."""
    if config.disable_build_target is not False or config.build_target_enrollment is None:
        raise ValueError("Target requires enabled build-target enrollment before controller selection")
    _supported_machine(config)
    enrollment = parse_build_target_enrollment(config.build_target_enrollment)
    return _endpoint(config, enrollment["keys"], "bw-target-", workspace)
