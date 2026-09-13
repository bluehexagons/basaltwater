"""Run repository code without broker credentials and snapshot build artifacts."""

from __future__ import annotations

from contextlib import contextmanager
import json
import grp
import os
from pathlib import Path, PurePosixPath
import pwd
import resource
import stat
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.cicd_deadline import command_timeout, run_command
from lib.validation import validate_filesystem_path


BUILD_USER = "cicd-build"
BUILD_HOME = "/var/lib/infra_tools/cicd/build"
SNAPSHOT_PARENT = "/var/lib/infra_tools/cicd"
MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024
MAX_SNAPSHOT_FILES = 100_000
MAX_HEADER_BYTES = 16 * 1024
BUILD_PATH = f"{BUILD_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin"


@contextmanager
def receiver_state():
    """Keep SQLite journals owned by the receiver in the single-threaded broker."""
    if os.geteuid() != 0:
        raise RuntimeError("CI/CD broker must run as root")
    try:
        account = pwd.getpwnam("webhook")
    except KeyError as exc:
        raise RuntimeError("CI/CD receiver account is missing") from exc
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise RuntimeError("CI/CD receiver must be unprivileged")
    old_gid = os.getegid()
    try:
        os.setegid(account.pw_gid)
        os.seteuid(account.pw_uid)
        yield
    finally:
        os.seteuid(0)
        os.setegid(old_gid)


def run_build_command(command: list[str], *, timeout: float, **kwargs):
    """Drop every identity/capability and discard the broker's environment."""
    if os.geteuid() != 0:
        raise RuntimeError("CI/CD broker must run as root to isolate builds")
    account = pwd.getpwnam(BUILD_USER)
    broker = pwd.getpwnam("webhook")
    if account.pw_uid in (0, broker.pw_uid) or account.pw_gid in (0, broker.pw_gid) or account.pw_dir != BUILD_HOME:
        raise RuntimeError("CI/CD build account must have a separate UID, group and managed home")
    if grp.getgrgid(account.pw_gid).gr_name != BUILD_USER:
        raise RuntimeError("CI/CD build account must use its dedicated primary group")
    if "env" in kwargs:
        raise ValueError("Build environment is managed by the credential boundary")
    environment = {
        "HOME": BUILD_HOME, "USER": BUILD_USER, "LOGNAME": BUILD_USER,
        "PATH": BUILD_PATH, "LANG": "C.UTF-8", "NVM_DIR": f"{BUILD_HOME}/.nvm",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
    }
    return run_command([
        "/usr/bin/setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}",
        "--clear-groups", "--inh-caps=-all", "--ambient-caps=-all",
        "--bounding-set=-all", "--no-new-privs", "--", *command,
    ], timeout=timeout, env=environment, **kwargs)


def _export_workspace(workspace: str, output) -> None:
    """Export only bytes readable by the build identity, with bounded framing."""
    validate_filesystem_path(workspace, must_exist=True)
    if not stat.S_ISDIR(os.lstat(workspace).st_mode):
        raise ValueError("Artifact workspace must be a real directory")

    def unreadable(error):
        raise error

    total = count = 0
    for directory, directories, files in os.walk(workspace, followlinks=False, onerror=unreadable):
        if directory != workspace:
            count += 1
            header = json.dumps({"path": os.path.relpath(directory, workspace), "size": 0,
                                 "executable": False, "directory": True}).encode() + b"\n"
            if count > MAX_SNAPSHOT_FILES or len(header) > MAX_HEADER_BYTES:
                raise ValueError("Artifact snapshot exceeds file or header limit")
            output.write(header)
        directories[:] = [name for name in directories if name not in {".git", "node_modules", "__pycache__"}]
        for name in directories:
            if os.path.islink(os.path.join(directory, name)):
                raise ValueError("Artifact directories cannot be symlinks")
        for name in files:
            if name.endswith(".log"):
                continue
            path = os.path.join(directory, name)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Artifacts must be regular files")
                total += info.st_size
                count += 1
                if total > MAX_SNAPSHOT_BYTES or count > MAX_SNAPSHOT_FILES:
                    raise ValueError("Artifact snapshot exceeds size or file limit")
                header = json.dumps({"path": os.path.relpath(path, workspace), "size": info.st_size,
                                     "executable": bool(info.st_mode & 0o111), "directory": False}).encode() + b"\n"
                if len(header) > MAX_HEADER_BYTES:
                    raise ValueError("Artifact path exceeds header limit")
                output.write(header)
                remaining = info.st_size
                while remaining:
                    chunk = source.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise ValueError("Artifact changed during export")
                    output.write(chunk)
                    remaining -= len(chunk)
    output.write(b'{"end":true}\n')


def _extract_snapshot(source, destination: Path) -> None:
    """Read untrusted framing into a new, broker-private directory."""
    total = count = 0
    while True:
        command_timeout(300)
        header = source.readline(MAX_HEADER_BYTES + 1)
        if len(header) > MAX_HEADER_BYTES or not header.endswith(b"\n"):
            raise ValueError("Invalid or incomplete artifact snapshot header")
        entry = json.loads(header)
        if entry == {"end": True}:
            if source.read(1):
                raise ValueError("Unexpected bytes after artifact snapshot")
            return
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "executable", "directory"}:
            raise ValueError("Invalid artifact snapshot entry")
        name, size = entry["path"], entry["size"]
        if (not isinstance(name, str) or not name or "\0" in name
                or PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))
                or type(size) is not int or size < 0 or type(entry["executable"]) is not bool
                or type(entry["directory"]) is not bool or (entry["directory"] and size != 0)):
            raise ValueError("Invalid artifact path or size")
        total += size
        count += 1
        if total > MAX_SNAPSHOT_BYTES or count > MAX_SNAPSHOT_FILES:
            raise ValueError("Artifact snapshot exceeds size or file limit")
        path = destination / name
        validate_filesystem_path(str(path), must_exist=False)
        for parent in reversed(path.parents):
            if parent == destination or destination in parent.parents:
                parent.mkdir(exist_ok=True)
                parent.chmod(0o755)
        if entry["directory"]:
            path.mkdir(exist_ok=True)
            path.chmod(0o755)
            continue
        # Exclusive regular files only: no archive links, ownership, or modes.
        with path.open("xb") as target:
            os.fchmod(target.fileno(), 0o755 if entry["executable"] else 0o644)
            remaining = size
            while remaining:
                command_timeout(300)
                chunk = source.read(min(remaining, 64 * 1024))
                if not chunk:
                    raise ValueError("Incomplete artifact snapshot")
                target.write(chunk)
                remaining -= len(chunk)


@contextmanager
def artifact_snapshot(workspace: str):
    """Copy through an unprivileged exporter before privileged deployment reads."""
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=SNAPSHOT_PARENT) as temporary:
        root = Path(temporary)
        destination = root / "artifacts"
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        with tempfile.TemporaryFile(dir=temporary) as archive:
            result = run_build_command([
                "/usr/bin/python3", "-I", os.path.abspath(__file__), workspace,
            ], timeout=300, cwd="/", stdout=archive)
            if result.returncode != 0:
                raise RuntimeError("Build artifact export failed; deployment was not started")
            archive.seek(0)
            _extract_snapshot(archive, destination)
        yield str(destination)


if __name__ == "__main__":
    # Limit the temporary transfer, including framing overhead.
    limit = MAX_SNAPSHOT_BYTES + MAX_SNAPSHOT_FILES * MAX_HEADER_BYTES
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    _export_workspace(sys.argv[1], sys.stdout.buffer)
