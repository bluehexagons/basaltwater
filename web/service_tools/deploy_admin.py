#!/usr/bin/env python3
"""Narrow privileged operations used by the remote deployment account."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator

# The installed executable uses root-owned supporting code. Module imports
# leave sys.path alone so repository tests cannot load a stale /opt checkout.
if not __package__:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    source_root = '/opt/basaltwater' if script_dir == '/usr/local/sbin' else os.path.dirname(os.path.dirname(script_dir))
    sys.path.insert(0, source_root)

from lib.cicd_deploy_policy import validate_nginx_deployment, validate_nginx_path
from lib.nginx_config import GENERATED_CONFIG_MARKER, generate_merged_nginx_config


NGINX_AVAILABLE_DIR = "/etc/nginx/sites-available"
NGINX_ENABLED_DIR = "/etc/nginx/sites-enabled"
NGINX_BINARY = "/usr/sbin/nginx"
SYSTEMCTL_BINARY = "/bin/systemctl"
STAGED_CONFIG_PREFIX = "/tmp/basaltwater-nginx-"
NGINX_LOCK_PATH = "/run/lock/basaltwater/deploy-nginx.lock"
MAX_NGINX_CONFIG_BYTES = 1024 * 1024
DEPLOY_POLICY_FILE = '/etc/basaltwater/cicd/deploy_policy.json'

_CONFIG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,253}$")
_SERVICE_NAME_PATTERN = re.compile(r"^node-[a-z0-9][a-z0-9_.-]{0,126}$")
_OPERATION_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def validate_config_name(value: str) -> str:
    """Validate a name used below nginx's sites directories."""

    if not _CONFIG_NAME_PATTERN.fullmatch(value):
        raise ValueError("invalid nginx configuration name")
    return value


def validate_service_name(value: str) -> str:
    """Limit service management to generated Node units."""

    base_name = value[:-8] if value.endswith(".service") else value
    if base_name.endswith(".service") or not _SERVICE_NAME_PATTERN.fullmatch(base_name):
        raise ValueError("invalid deploy service name")
    return f"{base_name}.service"


def validate_operation_id(value: str) -> str:
    """Validate a controller-generated deployment operation identifier."""

    if not _OPERATION_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid deployment operation ID")
    return value


@contextmanager
def _nginx_mutation_lock() -> Iterator[None]:
    """Serialize nginx configuration validation and rollback transactions."""

    lock_dir = os.path.dirname(NGINX_LOCK_PATH)
    os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    directory_info = os.lstat(lock_dir)
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or directory_info.st_uid != os.geteuid()
        or directory_info.st_mode & 0o077
    ):
        raise ValueError(f"unsafe nginx lock directory: {lock_dir}")
    descriptor = os.open(
        NGINX_LOCK_PATH,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        lock_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.geteuid()
        ):
            raise ValueError(f"unsafe nginx lock file: {NGINX_LOCK_PATH}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _run_checked(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(detail)


def _read_regular_file(path: str, max_bytes: int) -> tuple[bytes, int, int]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"path is not a regular file: {path}")
        if file_stat.st_size > max_bytes:
            raise ValueError(f"file exceeds {max_bytes} bytes: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise ValueError(f"file exceeds {max_bytes} bytes: {path}")
        return content, stat.S_IMODE(file_stat.st_mode), file_stat.st_uid
    finally:
        os.close(fd)


def _atomic_write(path: str, content: bytes, mode: int = 0o644) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}-", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as file_obj:
            fd = -1
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _replace_symlink(path: str, target: str) -> None:
    temp_path = f"{path}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        os.symlink(target, temp_path)
        os.replace(temp_path, path)
    finally:
        if os.path.lexists(temp_path):
            os.unlink(temp_path)


def _capture_nginx_paths(config_name: str) -> tuple[bytes | None, int, str | None]:
    available_path = os.path.join(NGINX_AVAILABLE_DIR, config_name)
    enabled_path = os.path.join(NGINX_ENABLED_DIR, config_name)

    previous_content: bytes | None = None
    previous_mode = 0o644
    if os.path.lexists(available_path):
        previous_content, previous_mode, _owner = _read_regular_file(
            available_path,
            MAX_NGINX_CONFIG_BYTES,
        )
        if not previous_content.startswith((GENERATED_CONFIG_MARKER + '\n').encode()):
            raise ValueError(f"refusing to modify unmanaged nginx configuration: {available_path}")

    previous_link: str | None = None
    if os.path.lexists(enabled_path):
        if not os.path.islink(enabled_path):
            raise ValueError(f"enabled nginx path is not a symlink: {enabled_path}")
        if previous_content is None or os.path.realpath(enabled_path) != available_path:
            raise ValueError(f"enabled nginx path does not reference the managed site: {enabled_path}")
        previous_link = os.readlink(enabled_path)

    return previous_content, previous_mode, previous_link


def _restore_nginx_paths(
    config_name: str,
    previous_content: bytes | None,
    previous_mode: int,
    previous_link: str | None,
) -> None:
    available_path = os.path.join(NGINX_AVAILABLE_DIR, config_name)
    enabled_path = os.path.join(NGINX_ENABLED_DIR, config_name)

    if previous_content is None:
        if os.path.lexists(available_path):
            os.unlink(available_path)
    else:
        _atomic_write(available_path, previous_content, previous_mode)

    if previous_link is None:
        if os.path.lexists(enabled_path):
            os.unlink(enabled_path)
    else:
        _replace_symlink(enabled_path, previous_link)


def _allowed_site_roots() -> list[str]:
    """Read optional root-owned authority for custom deployment directories."""
    try:
        content, mode, owner = _read_regular_file(DEPLOY_POLICY_FILE, MAX_NGINX_CONFIG_BYTES)
    except FileNotFoundError:
        return ['/var/www']
    if owner != 0 or mode & 0o022:
        raise ValueError('deploy policy must be root-owned and not writable by group or others')
    policy = json.loads(content)
    if not isinstance(policy, dict) or set(policy) != {'allowed_base_dirs'}:
        raise ValueError('deploy policy requires allowed_base_dirs')
    roots = policy['allowed_base_dirs']
    if not isinstance(roots, list) or not roots:
        raise ValueError('allowed_base_dirs must be a nonempty list')
    for root in roots:
        validate_nginx_path(root)
        if root == '/':
            raise ValueError('the filesystem root cannot be a deployment base')
    return roots


def install_nginx_config(config_name: str, operation_id: str) -> None:
    """Render a bounded site request locally; never install uploaded nginx text."""

    safe_name = validate_config_name(config_name)
    safe_operation_id = validate_operation_id(operation_id)
    staged_path = (
        f"{STAGED_CONFIG_PREFIX}{safe_name}-{safe_operation_id}.json"
    )
    content, _mode, owner_uid = _read_regular_file(staged_path, MAX_NGINX_CONFIG_BYTES)

    sudo_uid = os.environ.get("SUDO_UID")
    if sudo_uid is not None and owner_uid != int(sudo_uid):
        raise ValueError("staged nginx configuration is not owned by the invoking user")

    deployment = validate_nginx_deployment(json.loads(content))
    if deployment['domain'].replace('.', '_') != safe_name:
        raise ValueError('request domain does not match the nginx site name')
    serve_path = os.path.realpath(deployment['serve_path'])
    if not any(
        serve_path != os.path.realpath(root)
        and os.path.commonpath([serve_path, os.path.realpath(root)]) == os.path.realpath(root)
        for root in _allowed_site_roots()
    ):
        raise ValueError('site directory must be below an allowed deployment base')
    content = generate_merged_nginx_config(
        deployment['domain'], [{**deployment, 'needs_proxy': False}],
        disable_symlinks=True,
    ).encode('utf-8')

    os.makedirs(NGINX_AVAILABLE_DIR, mode=0o755, exist_ok=True)
    os.makedirs(NGINX_ENABLED_DIR, mode=0o755, exist_ok=True)
    previous = _capture_nginx_paths(safe_name)
    available_path = os.path.join(NGINX_AVAILABLE_DIR, safe_name)
    enabled_path = os.path.join(NGINX_ENABLED_DIR, safe_name)

    try:
        _atomic_write(available_path, content)
        _replace_symlink(enabled_path, available_path)
        _run_checked([NGINX_BINARY, "-t"])
    except Exception:
        _restore_nginx_paths(safe_name, *previous)
        raise
    finally:
        if os.path.exists(staged_path):
            os.unlink(staged_path)


def remove_nginx_config(config_name: str) -> None:
    """Remove one validated nginx site, rolling back if validation fails."""

    safe_name = validate_config_name(config_name)
    previous = _capture_nginx_paths(safe_name)
    available_path = os.path.join(NGINX_AVAILABLE_DIR, safe_name)
    enabled_path = os.path.join(NGINX_ENABLED_DIR, safe_name)

    try:
        if os.path.lexists(enabled_path):
            os.unlink(enabled_path)
        if os.path.lexists(available_path):
            os.unlink(available_path)
        _run_checked([NGINX_BINARY, "-t"])
    except Exception:
        _restore_nginx_paths(safe_name, *previous)
        raise


def reload_nginx() -> None:
    """Validate and reload nginx."""

    _run_checked([NGINX_BINARY, "-t"])
    _run_checked([SYSTEMCTL_BINARY, "reload", "nginx.service"])


def restart_service(service_name: str) -> None:
    """Restart one generated application service."""

    _run_checked([SYSTEMCTL_BINARY, "restart", validate_service_name(service_name)])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install-site")
    install_parser.add_argument("config_name")
    install_parser.add_argument("operation_id")

    remove_parser = subparsers.add_parser("remove-nginx")
    remove_parser.add_argument("config_name")

    subparsers.add_parser("reload-nginx")

    restart_parser = subparsers.add_parser("restart-service")
    restart_parser.add_argument("service_name")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a validated privileged deployment operation."""

    if os.geteuid() != 0:
        print("deploy admin helper must run as root", file=sys.stderr)
        return 1

    args = _build_parser().parse_args(argv)
    try:
        if args.command in {"install-site", "remove-nginx", "reload-nginx"}:
            with _nginx_mutation_lock():
                if args.command == "install-site":
                    install_nginx_config(args.config_name, args.operation_id)
                elif args.command == "remove-nginx":
                    remove_nginx_config(args.config_name)
                else:
                    reload_nginx()
        elif args.command == "restart-service":
            restart_service(args.service_name)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"deploy admin operation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
