"""Checksum-pinned, root-owned Node for service launchers and provisioning."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import posixpath
import stat
import tarfile
import tempfile
import urllib.request

from lib.node_toolchain import is_supported_node
from lib.remote_utils import run
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


SERVICE_NODE_VERSION = "24.22.1"
SERVICE_NODE_PARENT = "/opt/basaltwater-node"
SERVICE_NODE_ROOT = f"{SERVICE_NODE_PARENT}/v{SERVICE_NODE_VERSION}"
SERVICE_NODE = f"{SERVICE_NODE_ROOT}/bin/node"
SERVICE_NPM_CLI = f"{SERVICE_NODE_ROOT}/lib/node_modules/npm/bin/npm-cli.js"
# Official https://nodejs.org/dist/v24.22.1/SHASUMS256.txt.
NODE_ARCHIVES = {
    "x86_64": ("x64", "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6"),
    "aarch64": ("arm64", "dbabf7a1cbf947e6059ffad2c27b80041db97dea1e553b62afdee7f13fca336e"),
}
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def node_environment() -> dict[str, str]:
    """Keep host trust settings while excluding Node module/preload injection."""
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"NODE_OPTIONS", "NODE_PATH"} and not key.lower().startswith("npm_config_")}
    environment["PATH"] = os.pathsep.join((str(Path(SERVICE_NODE).parent), os.defpath))
    return environment


def _root_owned_path(path: Path, *, directory: bool = False) -> None:
    validate_filesystem_path(str(path), must_exist=True)
    for current in (path, *path.parents):
        metadata = current.lstat()
        expected_type = stat.S_ISDIR if current != path or directory else stat.S_ISREG
        if (not expected_type(metadata.st_mode) or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022):
            raise RuntimeError(f"Service Node path must be root-owned and not writable by others: {current}")


def verify_service_node() -> None:
    """Never execute a service interpreter from an untrusted or obsolete path."""
    for path in (SERVICE_NODE, SERVICE_NPM_CLI):
        _root_owned_path(Path(path))
    result = run([SERVICE_NODE, "--version"], capture_output=True, env=node_environment())
    version = result.stdout.strip().removeprefix("v")
    if version != SERVICE_NODE_VERSION or not is_supported_node(version):
        raise RuntimeError(f"Service Node requires {SERVICE_NODE_VERSION}; found {version!r}")


def _extract_node_archive(archive_path: Path, destination: Path, *, digest: str, member_root: str) -> Path:
    """Validate bytes and archive paths before making any executable available."""
    with archive_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise RuntimeError("Service Node archive checksum verification failed")
    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if (not parts or parts[0] != member_root or ".." in parts
                    or not (member.isfile() or member.isdir() or member.issym())):
                raise RuntimeError(f"Unsafe service Node archive member: {member.name}")
            if member.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                if target != member_root and not target.startswith(member_root + "/"):
                    raise RuntimeError(f"Escaping service Node archive link: {member.name}")
        # The data filter also rejects absolute/escaping link targets and strips
        # unsafe ownership/mode metadata. Extraction is into a private directory.
        archive.extractall(destination, filter="data")
    root = destination / member_root
    for executable in (root / "bin/node", root / "lib/node_modules/npm/bin/npm-cli.js"):
        if executable.is_symlink() or not executable.is_file():
            raise RuntimeError(f"Service Node archive is missing a regular executable: {executable.name}")
    for current, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            if not path.is_symlink():
                path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o022)
    return root


def install_service_node() -> bool:
    """Publish an immutable official runtime without replacing distro Node."""
    if os.geteuid() != 0:
        raise RuntimeError("Service Node provisioning requires root-owned setup")
    try:
        architecture, digest = NODE_ARCHIVES[platform.machine()]
    except KeyError as exc:
        raise RuntimeError("Service Node supports Linux x86_64 and aarch64") from exc
    if platform.system() != "Linux":
        raise RuntimeError("Service Node provisioning requires Linux")
    parent = Path(SERVICE_NODE_PARENT)
    with directory_fd(str(parent), create=True):
        _root_owned_path(parent, directory=True)
    if os.path.lexists(SERVICE_NODE_ROOT):
        verify_service_node()
        return False
    archive_root = f"node-v{SERVICE_NODE_VERSION}-linux-{architecture}"
    url = f"https://nodejs.org/dist/v{SERVICE_NODE_VERSION}/{archive_root}.tar.xz"
    with tempfile.TemporaryDirectory(prefix=".node-stage-", dir=parent) as temporary:
        stage = Path(temporary)
        archive_path = stage / "node.tar.xz"
        with urllib.request.urlopen(url, timeout=60) as response, archive_path.open("wb") as output:
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Service Node archive exceeds its download limit")
                output.write(chunk)
        extracted = _extract_node_archive(archive_path, stage, digest=digest, member_root=archive_root)
        result = run([str(extracted / "bin/node"), "--version"], capture_output=True,
                     env=node_environment())
        if result.stdout.strip() != "v" + SERVICE_NODE_VERSION:
            raise RuntimeError("Downloaded service Node failed version verification")
        extracted.chmod(0o755)
        # An existing destination is never deleted to force a repair. A corrupt
        # installed runtime fails verification and requires explicit inspection.
        extracted.rename(SERVICE_NODE_ROOT)
    verify_service_node()
    return True
