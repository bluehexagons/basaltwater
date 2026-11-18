#!/usr/bin/env python3

from __future__ import annotations

from argparse import ArgumentTypeError
import copy
import json
import os
import shlex
import stat
import subprocess

from lib.atomic_io import write_text_atomic
from lib.concurrency import resource_lock
from lib.state_read import StateReadError
from lib.config import SetupConfig
from lib.git_credentials import (
    MAX_GIT_CA_BUNDLE_BYTES,
    encode_git_ca_pem,
    normalize_git_https_origin,
    parse_git_ca_ssh_source,
    validate_git_ca_pem,
)
from lib.ssh_utils import (
    build_ssh_command,
    shell_join,
    ssh_batch_mode,
    ssh_process_timeout,
)
from lib.validation import validate_no_control_characters
from lib.validators import validate_host
from lib.workspace import (
    ensure_workspace_dir,
    get_credentials_path,
    get_known_hosts_path,
)


CREDENTIALS_VERSION = 2
MAX_CREDENTIAL_STORE_BYTES = 1024 * 1024
CREDENTIAL_SERVICES = ("git", "gogs", "smb", "samba", "syncthing", "antistatic", "github")
CredentialKey = tuple[str, str, str]


def _normalize_credential_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("Credential username must not be empty")
    if ":" in normalized:
        raise ValueError("Credential username must not contain ':'")
    if "," in normalized:
        raise ValueError("Credential username must not contain ','")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("Credential username must not contain control characters")
    return normalized


def _checked_password(key: CredentialKey, password: str) -> str:
    # An inline SMB password has always been passed to mount.cifs verbatim.
    value = password if key[0] == "smb" else password.strip()
    if not value:
        raise ValueError("Credential password must not be empty")
    return value


def _credential_key(service: str, destination: str | None, username: str) -> CredentialKey:
    username = _normalize_credential_username(username)
    if service == "legacy":
        if destination is not None:
            raise ValueError("Legacy credentials do not have a destination")
        return service, "", username
    if service not in CREDENTIAL_SERVICES or not destination:
        raise ValueError("A scoped credential requires a service and destination")
    if service == "git":
        destination = normalize_git_https_origin(destination)
    elif service == "smb":
        host, separator, share = destination.removeprefix("//").partition("/")
        if not destination.startswith("//") or not separator or not validate_host(host):
            raise ValueError("SMB credential destination must be //HOST/SHARE")
        if not share or share in {".", ".."} or any(
            char in "/\\" or char.isspace() for char in share
        ):
            raise ValueError("SMB credential destination requires a valid share name")
        validate_no_control_characters(share, "SMB credential share")
        destination = f"//{host.lower()}/{share}"
    else:
        if not validate_host(destination) and destination != "::1":
            raise ValueError("Credential destination must be a setup target hostname or IP")
        destination = destination.lower()
    return service, destination, username


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate credential store key")
        result[key] = value
    return result


def _decode_workspace_credentials(payload: object) -> dict[CredentialKey, str]:
    if not isinstance(payload, dict):
        raise ValueError("Credential store must contain a JSON object")
    version = payload.get("version")
    if type(version) is not int or version not in (1, CREDENTIALS_VERSION):
        raise ValueError("Unsupported credential store version")
    legacy = payload.get("credentials")
    scoped = payload.get("scoped_credentials") if version == CREDENTIALS_VERSION else []
    if not isinstance(legacy, dict) or not isinstance(scoped, list):
        raise ValueError("Invalid credential store sections")

    credentials: dict[CredentialKey, str] = {}
    for username, entry in legacy.items():
        key = _credential_key("legacy", None, username)
        if key[2] != username or not isinstance(entry, dict):
            raise ValueError("Invalid legacy credential")
        password = entry.get("password")
        if not isinstance(password, str) or not password.strip():
            raise ValueError("Invalid legacy password")
        credentials[key] = password

    for entry in scoped:
        if not isinstance(entry, list) or len(entry) != 4 or not all(
            isinstance(value, str) for value in entry
        ):
            raise ValueError("Invalid scoped credential")
        service, destination, username, password = entry
        key = _credential_key(service, destination, username)
        if key != (service, destination, username) or key in credentials:
            raise ValueError("Invalid or duplicate scoped credential")
        _checked_password(key, password)
        credentials[key] = password
    return credentials


def _load_workspace_credentials_unlocked(workspace: str | None = None) -> dict[CredentialKey, str]:
    ensure_workspace_dir(workspace)
    credentials_path = get_credentials_path(workspace)
    try:
        descriptor = os.open(credentials_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise StateReadError(credentials_path, "unsafe or unreadable credential store") from exc

    try:
        with os.fdopen(descriptor, "rb") as file_obj:
            info = os.fstat(file_obj.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid()):
                raise ValueError("credential store must be an owned regular file with one link")
            content = file_obj.read(MAX_CREDENTIAL_STORE_BYTES + 1)
            if len(content) > MAX_CREDENTIAL_STORE_BYTES:
                raise ValueError("credential store exceeds 1 MiB")
            payload = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_json_object)
            credentials = _decode_workspace_credentials(payload)
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(file_obj.fileno(), 0o600)
    except (OSError, ValueError, RecursionError) as exc:
        raise StateReadError(credentials_path, "unsafe, unreadable, or malformed credential store") from exc

    return credentials


def load_workspace_credentials(workspace: str | None = None) -> dict[CredentialKey, str]:
    """Load saved workspace credentials."""
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        return _load_workspace_credentials_unlocked(workspace)


def _save_workspace_credentials_unlocked(
    credentials: dict[CredentialKey, str], workspace: str | None = None
) -> None:
    ensure_workspace_dir(workspace)
    credentials_path = get_credentials_path(workspace)
    payload = {
        "version": CREDENTIALS_VERSION,
        "credentials": {
            username: {"password": password}
            for (service, _, username), password in credentials.items()
            if service == "legacy"
        },
        "scoped_credentials": [
            [*key, password]
            for key, password in sorted(credentials.items())
            if key[0] != "legacy"
        ],
    }
    content = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if len(content) > MAX_CREDENTIAL_STORE_BYTES:
        raise ValueError("Credential store would exceed 1 MiB")
    write_text_atomic(credentials_path, content, mode=0o600)


def set_workspace_credential(
    username: str, password: str, workspace: str | None = None, *,
    service: str = "legacy", destination: str | None = None,
) -> None:
    """Add or replace a workspace credential."""
    key = _credential_key(service, destination, username)
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        credentials[key] = _checked_password(key, password)
        _save_workspace_credentials_unlocked(credentials, workspace)


def remove_workspace_credential(
    username: str, workspace: str | None = None, *,
    service: str = "legacy", destination: str | None = None,
) -> bool:
    """Remove a saved workspace credential."""
    key = _credential_key(service, destination, username)
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        removed = credentials.pop(key, None) is not None
        if removed:
            _save_workspace_credentials_unlocked(credentials, workspace)
        return removed


def list_workspace_credentials(workspace: str | None = None) -> list[CredentialKey]:
    """Return saved identities without exposing their secrets."""
    return sorted(load_workspace_credentials(workspace))


def migrate_workspace_credential(
    username: str, service: str, destination: str, workspace: str | None = None,
) -> None:
    """Copy one legacy entry into an explicitly chosen scope."""
    key = _credential_key(service, destination, username)
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        password = credentials.get(("legacy", "", key[2]))
        if password is None:
            raise ValueError(f"No legacy credential for {key[2]}")
        if key in credentials:
            if credentials[key] != password:
                raise ValueError("Scoped credential already exists. Use credentials set to replace it")
            return
        credentials[key] = password
        _save_workspace_credentials_unlocked(credentials, workspace)


def store_cli_credentials(config: SetupConfig, workspace: str | None = None) -> None:
    """Persist credentials supplied on the command line."""
    _, supplied = _collect_credential_inputs(config)
    if not supplied:
        return
    path = get_credentials_path(workspace)
    with resource_lock("workspace-credentials", path, wait=True):
        credentials = _load_workspace_credentials_unlocked(workspace)
        credentials.update(supplied)
        _save_workspace_credentials_unlocked(credentials, workspace)


def _local_users(config: SetupConfig) -> list[tuple[str, str]]:
    return [
        (service, username)
        for service, username in (
            ("gogs", config.username if config.gogs else None),
            ("antistatic", config.antistatic_admin),
            ("syncthing", config.syncthing_admin),
        )
        if username
    ]


def _samba_users(config: SetupConfig) -> list[str]:
    return [
        user.strip()
        for spec in config.samba_shares or [] if len(spec) >= 4
        for user in spec[3].split(",") if user.strip()
    ]


def _smb_key(spec: list[str]) -> CredentialKey:
    if len(spec) != 5:
        raise ValueError("SMB mount requires MOUNTPOINT HOST CREDENTIALS SHARE SUBDIR")
    return _credential_key("smb", f"//{spec[1]}/{spec[3]}", spec[2].partition(":")[0])


def _collect_credential_inputs(
    config: SetupConfig,
) -> tuple[set[CredentialKey], dict[CredentialKey, str]]:
    requests = {
        _credential_key(service, config.host, username)
        for service, username in _local_users(config)
    }
    supplied: dict[CredentialKey, str] = {}

    def add_password(key: CredentialKey, password: str) -> None:
        password = _checked_password(key, password)
        if key in supplied and supplied[key] != password:
            service, destination, username = key
            raise ValueError(f"Conflicting credentials for {service} user {username} at {destination}")
        supplied[key] = password

    for username in _samba_users(config):
        username, separator, password = username.partition(":")
        key = _credential_key("samba", config.host, username)
        requests.add(key)
        if separator:
            add_password(key, password)

    for spec in config.smb_mounts or []:
        key = _smb_key(spec)
        requests.add(key)
        _, separator, password = spec[2].partition(":")
        if separator:
            add_password(key, password)

    for origin, username in config.git_credentials or []:
        requests.add(_credential_key("git", origin, username))
    if config.git_auth_credential is not None:
        requests.add(_credential_key("github", config.host, config.git_auth_credential))

    for username, password in config.share_credentials or []:
        username = _normalize_credential_username(username)
        matches = [key for key in requests if key[2] == username]
        if len(matches) != 1:
            detail = "matches multiple destinations or services" if matches else "has no declared consumer"
            raise ValueError(
                f"--credential {username} {detail}. Use basaltw credentials set "
                "USERNAME --service SERVICE --destination DESTINATION"
            )
        add_password(matches[0], password)

    return requests, supplied


def get_runtime_credential(
    config: SetupConfig, username: str, *, service: str = "gogs", origin: str = "",
) -> str | None:
    """Read only the entry staged for this consumer in the private setup payload."""
    username = _normalize_credential_username(username)
    for staged_service, selector, name, password in config.resolved_credentials or []:
        if (staged_service, selector, name) == (service, origin, username):
            return password

    # Direct target invocations can still pass an explicit --credential to a
    # local service when that username identifies exactly one consumer.
    if service == "git" or config.resolved_credentials is not None:
        return None
    local_services = [
        name for name, user in _local_users(config) if user == username
    ]
    if local_services != [service] or any(
        user.partition(":")[0].strip() == username for user in _samba_users(config)
    ):
        return None
    passwords = {
        password if service == "antistatic" else password.strip()
        for name, password in config.share_credentials or []
        if name.strip() == username
    }
    if len(passwords) > 1:
        raise ValueError(f"Conflicting credentials for {service} user {username}")
    return next(iter(passwords), None)


def parse_resolved_credential(value: str) -> list[str]:
    """Decode one private target argument without exposing invalid input."""
    try:
        record = json.loads(value)
        if not isinstance(record, list) or len(record) != 4 or not all(
            isinstance(item, str) for item in record
        ):
            raise ValueError
        service, origin, username, password = record
        if (service not in {"git", "gogs", "syncthing", "antistatic"}
                or (service == "git" and normalize_git_https_origin(origin) != origin)
                or (service != "git" and origin)
                or _normalize_credential_username(username) != username
                or not password.strip()):
            raise ValueError
    except (ValueError, RecursionError):
        raise ArgumentTypeError("Invalid resolved credential") from None
    return record


def _missing_credential(
    key: CredentialKey, saved: dict[CredentialKey, str],
) -> ValueError:
    service, destination, username = key
    label = {
        "antistatic": "Antistatic admin:",
        "git": "Git user",
        "github": "GitHub credential:",
        "samba": "share user:",
        "smb": "SMB mount user:",
        "syncthing": "Syncthing admin:",
    }[service]
    prefix = "Missing " if service == "github" else "Missing credential for "
    prefix += f"{label} {username} at {destination}."
    selection = shlex.join([username, "--service", service, "--destination", destination])
    if ("legacy", "", username) in saved:
        return ValueError(
            f"{prefix} A legacy entry exists. After verifying its destination, "
            f"run basaltw credentials migrate {selection}"
        )
    return ValueError(f"{prefix} Run basaltw credentials set {selection}")


def _read_git_ca_bundle_from_ssh(
    source_path: str,
    workspace: str | None,
) -> str:
    """Read a bounded public certificate bundle over host-key-verified SSH."""
    ssh_source = parse_git_ca_ssh_source(source_path)
    if ssh_source is None:
        raise ValueError("Git CA SSH source expected")

    known_hosts_path = get_known_hosts_path(workspace)
    if os.path.islink(known_hosts_path) or not os.path.isfile(known_hosts_path):
        raise ValueError(
            f"SSH host key for Git CA source {ssh_source.host} is not enrolled. "
            f"Run basaltw ssh-key enroll {ssh_source.host}"
        )
    read_command = [
        "head",
        "-c",
        str(MAX_GIT_CA_BUNDLE_BYTES + 1),
        "--",
        ssh_source.path,
    ]
    if ssh_source.username != "root":
        read_command = ["sudo", "-n", *read_command]
    batch_mode = ssh_batch_mode()
    command = build_ssh_command(
        ssh_source.host,
        ssh_source.username,
        port=ssh_source.port,
        remote_command=shell_join(read_command),
        batch_mode=batch_mode,
        known_hosts_path=known_hosts_path,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=ssh_process_timeout(60, batch_mode=batch_mode),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"Could not retrieve Git CA certificate over SSH from "
            f"{ssh_source.username}@{ssh_source.host}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or "SSH certificate retrieval failed").strip()
        raise ValueError(
            f"Could not retrieve Git CA certificate over SSH from "
            f"{ssh_source.username}@{ssh_source.host}: {detail[:240]}"
        )
    try:
        return validate_git_ca_pem(result.stdout)
    except ValueError as exc:
        raise ValueError(
            f"Invalid Git CA certificate source {source_path}: {exc}"
        ) from exc


def _read_git_ca_bundle(
    source_path: str,
    workspace: str | None = None,
) -> str:
    """Read and validate one local or authenticated SSH PEM bundle."""
    if parse_git_ca_ssh_source(source_path) is not None:
        return _read_git_ca_bundle_from_ssh(source_path, workspace)

    from lib.validation import validate_filesystem_path

    validate_filesystem_path(source_path, must_exist=True)
    if os.path.islink(source_path) or not os.path.isfile(source_path):
        raise ValueError(
            f"Git CA certificate source must be a regular non-symlink file: {source_path}"
        )
    source_mode = os.stat(source_path).st_mode & 0o777
    if source_mode & 0o022:
        raise ValueError(
            f"Git CA certificate source must not be group/world-writable: {source_path}"
        )
    if os.path.getsize(source_path) > MAX_GIT_CA_BUNDLE_BYTES:
        raise ValueError(f"Git CA certificate source exceeds 1 MiB: {source_path}")
    with open(source_path, "r", encoding="utf-8") as file_obj:
        content = file_obj.read()

    try:
        return validate_git_ca_pem(content)
    except ValueError as exc:
        raise ValueError(f"Invalid Git CA certificate source {source_path}: {exc}") from exc


def _prepare_git_ca_pems(
    config: SetupConfig,
    workspace: str | None = None,
) -> list[list[str]] | None:
    prepared: list[list[str]] = []
    for ca_spec in config.git_ca_certificates or []:
        if len(ca_spec) != 2:
            raise ValueError(
                "--git-ca-certificate requires HTTPS_ORIGIN and SOURCE"
            )
        origin, source_path = ca_spec
        normalized_origin = normalize_git_https_origin(origin)
        content = _read_git_ca_bundle(source_path, workspace)
        prepared.append([normalized_origin, encode_git_ca_pem(content)])
    return prepared or None


def _resolve_named_smb_mounts(
    smb_mounts: list[list[str]] | None,
    passwords: dict[CredentialKey, str],
) -> list[list[str]] | None:
    if not smb_mounts:
        return smb_mounts

    resolved_mounts: list[list[str]] = []
    for mount_spec in smb_mounts:
        key = _smb_key(mount_spec)
        resolved_spec = list(mount_spec)
        resolved_spec[2] = f"{key[2]}:{passwords[key]}"
        resolved_mounts.append(resolved_spec)
    return resolved_mounts


def prepare_runtime_config(config: SetupConfig, workspace: str | None = None) -> SetupConfig:
    """Return a runtime config with workspace credentials resolved."""
    from lib.validation import (
        validate_agent_git_settings,
        validate_github_auth_sources,
        validate_github_token,
    )

    validate_github_auth_sources(config)
    runtime_config = copy.deepcopy(config)
    requests, supplied = _collect_credential_inputs(config)
    saved = load_workspace_credentials(workspace)
    passwords: dict[CredentialKey, str] = {}
    samba_credentials: list[list[str]] = []
    scoped_credentials: list[list[str]] = []
    for key in sorted(requests):
        service, destination, username = key
        password = supplied.get(key, saved.get(key))
        if password is None:
            if service == "gogs":
                continue
            raise _missing_credential(key, saved)
        password = _checked_password(key, password)
        passwords[key] = password
        if service == "samba":
            samba_credentials.append([username, password])
        elif service == "github":
            runtime_config.git_auth_token = validate_github_token(password)
            runtime_config.git_auth_credential = None
            runtime_config.copy_agent_keys = True
        elif service != "smb":
            # Server-side setup runs locally on its selected target. Only Git
            # still needs a destination selector there to distinguish origins.
            selector = destination if service == "git" else ""
            scoped_credentials.append([service, selector, username, password])

    runtime_config.share_credentials = samba_credentials or None
    runtime_config.resolved_credentials = scoped_credentials or None
    runtime_config.smb_mounts = _resolve_named_smb_mounts(config.smb_mounts, passwords)
    runtime_config.git_ca_pems = _prepare_git_ca_pems(runtime_config, workspace)
    runtime_config.git_ca_certificates = None
    if config.git_auth_token is not None:
        runtime_config.git_auth_token = validate_github_token(config.git_auth_token)
        runtime_config.copy_agent_keys = True
    if config.git_auth_token is not None or config.git_auth_credential is not None:
        validate_agent_git_settings(runtime_config)
    return runtime_config
