"""Install the official GitHub runner on a dedicated Debian 13 x86-64 host."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

from lib.atomic_io import read_json_file, write_bytes_atomic, write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.github_runner_config import (
    ACCOUNT, APP, DATA, GATE, HOME, HOOK, HOOK_DIR, POLICY, SERVICE, SHA256, TOKEN_FILE, VERSION, validate_policy,
)
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, read_os_release, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import replace_units
from lib.validation import validate_filesystem_path, validate_github_runner_settings


def check_runner_platform(config: SetupConfig) -> None:
    validate_github_runner_settings(config)
    release = read_os_release()
    if (release.get("ID") != "debian" or release.get("VERSION_ID") != "13"
            or platform.machine() != "x86_64" or not can_manage_system_services(config.machine_type)):
        raise ValueError("GitHub runner phase 1 requires native Debian 13/Trixie x86-64 with systemd")


def _private_token(path: str) -> bytes:
    validate_filesystem_path(path, must_exist=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077 or info.st_size > 4097):
            raise ValueError("Runner token must be a private, owned regular file (chmod 600)")
        token = stream.read(4097).strip()
    if not re.fullmatch(rb"[A-Za-z0-9_-]{20,4096}", token):
        raise ValueError("Invalid short-lived runner registration token")
    return token


def stage_registration_token(source: str, destination: str) -> None:
    """Copy only a bounded one-hour token to the private expiring setup lease."""
    token = _private_token(source)
    os.makedirs(destination, mode=0o700)
    write_bytes_atomic(os.path.join(destination, "token"), token, mode=0o600)


def render_hook() -> str:
    # A failed pre-job step alone can leave `if: always()` steps runnable.
    # Kill the shell's parent Runner.Worker on *any* error, including Python
    # startup/import errors. The Listener survives to accept the next job.
    return f'''#!/bin/bash
worker=$PPID
trap '/bin/kill -KILL "$worker"' EXIT
trap 'exit 1' HUP INT TERM
set -eu
/usr/bin/python3 -I {GATE}
trap - EXIT HUP INT TERM
'''


def render_unit() -> str:
    return f'''# Managed by Basaltwater GitHub runner
[Unit]
Description=Repository GitHub Actions runner
Wants=network-online.target
After=network-online.target

[Service]
User={ACCOUNT}
Group={ACCOUNT}
WorkingDirectory={APP}
Environment=HOME={HOME}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED={HOOK}
ExecStartPre=/usr/bin/python3 -I {GATE} --check
ExecStart={APP}/bin/Runner.Listener run --startuptype service
Restart=always
RestartSec=10
TimeoutStopSec=120
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictSUIDSGID=yes
ReadWritePaths={DATA}

[Install]
WantedBy=multi-user.target
'''


def _directory(path: str, *, uid: int = 0, gid: int = 0, mode: int = 0o755) -> None:
    with directory_fd(path, create=True, mode=mode) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != uid or info.st_mode & 0o022:
            # Newly created directories start root-owned. Never seize an
            # unrelated existing directory from an unprivileged owner.
            if info.st_uid != 0:
                raise ValueError(f"Unsafe runner directory: {path}")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)


def _account() -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(["useradd", "--system", "--user-group", "--home-dir", HOME,
             "--shell", "/usr/sbin/nologin", ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
    groups = os.getgrouplist(ACCOUNT, account.pw_gid)
    if (account.pw_uid == 0 or account.pw_dir != HOME or account.pw_shell != "/usr/sbin/nologin"
            or groups != [account.pw_gid]):
        raise ValueError("Runner account must be dedicated, unprivileged and have no supplementary groups")
    return account


def _download_release(destination: Path) -> None:
    url = f"https://github.com/actions/runner/releases/download/v{VERSION}/actions-runner-linux-x64-{VERSION}.tar.gz"
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("xb") as stream:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ValueError("Runner release exceeds 512 MiB")
            digest.update(chunk)
            stream.write(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError("Official GitHub runner release checksum mismatch")


def _extract_release(archive: Path, destination: Path) -> None:
    total = 0
    links: list[tarfile.TarInfo] = []
    with tarfile.open(archive, "r:gz") as source:
        for index, member in enumerate(source):
            if member.isdir() and member.name in {".", "./"}:
                continue
            parts = Path(member.name).parts
            total += member.size
            if (not parts or member.name.startswith("/") or ".." in parts
                    or not (member.isfile() or member.isdir() or member.issym())
                    or total > 1536 * 1024 * 1024 or index >= 50000):
                raise ValueError("Unsafe runner release archive")
            target = destination.joinpath(*parts)
            if member.issym():
                links.append(member)
            elif member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    # Publish links last so archive extraction can never traverse one. Only
    # links to existing regular release content are needed by Node's tools.
    root = destination.resolve()
    for member in links:
        target = destination / member.name
        source = (target.parent / member.linkname).resolve()
        if (Path(member.linkname).is_absolute() or not source.is_relative_to(root)
                or not source.exists() or target.exists() or target.is_symlink()
                or not target.parent.resolve().is_relative_to(root)):
            raise ValueError("Unsafe runner release symlink")
        target.symlink_to(member.linkname)


def _register(archive: Path, application: Path, policy: dict, token: bytes, account: pwd.struct_passwd) -> None:
    """Run configuration unprivileged in a disposable tree; keep the release immutable."""
    with tempfile.TemporaryDirectory(prefix="registration-", dir=DATA) as temporary:
        registration = Path(temporary)
        _extract_release(archive, registration)
        # Chown only our pristine tree, before executing any account-owned code.
        for current, directories, files in os.walk(registration):
            for name in (*directories, *files):
                os.chown(Path(current) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.chown(registration, account.pw_uid, account.pw_gid)
        # CommandSettings in the pinned runner consumes and clears this input:
        # actions/runner v2.338.0 src/Runner.Listener/CommandSettings.cs.
        environment = {"PATH": "/usr/bin:/bin", "HOME": HOME, "ACTIONS_RUNNER_INPUT_TOKEN": token.decode("ascii")}
        # Token travels only in this short-lived process's environment, never
        # argv, source, logs, saved setup or a systemd EnvironmentFile.
        try:
            result = run(["runuser", "-u", ACCOUNT, "--", str(registration / "config.sh"),
                 "--unattended", "--url", "https://github.com/" + policy["repository"],
                 "--name", "basaltwater-" + os.uname().nodename,
                 "--work", DATA + "/work", "--disableupdate",
                 "--labels", ",".join(policy["labels"])],
                cwd=temporary, env=environment, check=False, capture_output=True, timeout=180)
            if result.returncode != 0:
                raise RuntimeError("Runner registration failed; obtain a fresh repository registration token")
        except (subprocess.SubprocessError, TimeoutError):
            raise RuntimeError("Runner registration failed; obtain a fresh repository registration token") from None
        for name in (".runner", ".credentials", ".credentials_rsaparams"):
            descriptor = os.open(registration / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                content = stream.read(64 * 1024 + 1)
            if not stat.S_ISREG(info.st_mode) or len(content) > 64 * 1024:
                raise ValueError("Invalid generated runner credential file")
            write_bytes_atomic(str(application / name), content, uid=0, gid=account.pw_gid, mode=0o640)


def setup_github_runner(config: SetupConfig) -> None:
    check_runner_platform(config)
    policy = validate_policy(dict(schema=1, repository=config.ci_repository,
                                  allowed_users=list(dict.fromkeys(user.lower() for user in config.ci_allowed_users or [])),
                                  labels=list(dict.fromkeys(config.ci_labels or [])), runner_version=VERSION))
    if is_dry_run():
        print(f"  [DRY-RUN] Official runner {VERSION} for {policy['repository']}; dedicated account, no Docker")
        return
    for path in (APP, DATA, HOOK_DIR, os.path.dirname(POLICY)):
        check_directory_path(path)
    try:
        previous = validate_policy(read_json_file(POLICY, max_bytes=16 * 1024))
    except FileNotFoundError:
        previous = None
    if previous and (previous["repository"] != policy["repository"] or previous["labels"] != policy["labels"]):
        raise ValueError("Repository/label replacement requires deliberate deregistration; see the runner guide")
    if previous is None and os.path.lexists(APP):
        raise ValueError("Unmanaged or interrupted runner installation exists; see runner recovery")
    token = None
    if config.ci_registration_payload:
        try:
            token = _private_token(TOKEN_FILE)
        finally:
            os.unlink(TOKEN_FILE)
    if previous is None and token is None:
        raise ValueError("Initial runner registration requires --ci-registration-token-file")
    account = _account()
    _directory(DATA)
    _directory(HOME, uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    _directory(DATA + "/work", uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    _directory(DATA + "/diag", uid=account.pw_uid, gid=account.pw_gid, mode=0o700)
    # Only this telemetry subdirectory is readable by the panel account.
    _directory(DATA + "/telemetry", uid=account.pw_uid, gid=account.pw_gid)
    _directory(HOOK_DIR)
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "--no-install-recommends", "ca-certificates", "libicu76",
         "libssl3t64", "libkrb5-3", "zlib1g", "liblttng-ust1t64", "libcurl4t64", "libunwind8"])
    if previous is None:
        with tempfile.TemporaryDirectory(prefix=".github-runner-", dir="/opt") as temporary:
            staging = Path(temporary)
            staging.chmod(0o755)
            archive = staging / "runner.tar.gz"
            _download_release(archive)
            application = staging / "app"
            application.mkdir(mode=0o755)
            _extract_release(archive, application)
            _register(archive, application, policy, token, account)
            os.symlink(DATA + "/diag", application / "_diag")
            write_text_atomic(str(application / ".env"), "", mode=0o644)
            write_text_atomic(str(application / ".path"), "/usr/local/bin:/usr/bin:/bin", mode=0o644)
            os.rename(application, APP)
    else:
        run(["systemctl", "stop", SERVICE], timeout=120)
    write_text_atomic(HOOK, render_hook(), mode=0o755)
    write_json_atomic(POLICY, policy, mode=0o644)
    replace_units({SERVICE: render_unit()}, activate=(SERVICE,))
