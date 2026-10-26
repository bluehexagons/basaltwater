"""Shared SSH/SCP/rsync command builders."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Sequence

from lib.validation import validate_filesystem_path, validate_network_port
from lib.validators import validate_host
from lib.workspace import ensure_workspace_dir, get_known_hosts_path


@dataclass(frozen=True)
class _SSHTransport:
    host: str
    identity_file: str
    known_hosts_path: str
    port: int


_STRICT_TRANSPORT: ContextVar[_SSHTransport | None] = ContextVar("strict_ssh_transport", default=None)


@contextmanager
def strict_ssh_transport(
    host: str, *, identity_file: str, known_hosts_path: str, port: int = 22,
) -> Iterator[None]:
    """Bind build_ssh_command to a resolved endpoint for this execution context.

    Callers must resolve and check the identity and trust files before entry.
    Ignore ambient configuration, agents, forwarding and multiplexing, including
    explicitly supplied reusable sockets. Other threads/tasks keep their policy.
    """
    if not validate_host(host):
        raise ValueError("Invalid strict SSH host")
    for path in (identity_file, known_hosts_path):
        validate_filesystem_path(path)
        if not os.path.isabs(path):
            raise ValueError("Strict SSH transport requires resolved absolute paths")
    validate_network_port(port, "SSH port")
    token = _STRICT_TRANSPORT.set(_SSHTransport(host, identity_file, known_hosts_path, port))
    try:
        yield
    finally:
        _STRICT_TRANSPORT.reset(token)


def ssh_batch_mode() -> bool:
    """Return whether SSH should fail instead of prompting for input.

    OpenSSH can ask for a passphrase through the controlling terminal even
    when its stdout and stderr are captured.  Keep that behavior for commands
    started from a terminal.  Commands started with redirected stdin cannot
    answer a prompt reliably, so they must use an already-loaded SSH agent (or
    fail clearly instead of hanging). Strict transport always uses batch mode.
    """

    return _STRICT_TRANSPORT.get() is not None or not sys.stdin.isatty()


def ssh_process_timeout(
    timeout: int | float | None,
    *,
    batch_mode: bool | None = None,
) -> int | float | None:
    """Return a subprocess timeout appropriate for an OpenSSH invocation.

    A wall-clock subprocess timeout also counts time spent at OpenSSH's
    controlling-terminal passphrase prompt. Interactive commands should wait
    for the operator and remain interruptible with Ctrl-C. Non-interactive
    commands retain their bounded timeout and already use SSH batch mode.
    """

    resolved_batch_mode = ssh_batch_mode() if batch_mode is None else batch_mode
    return timeout if resolved_batch_mode else None


def shell_join(argv: Sequence[str]) -> str:
    """Quote an argv sequence for remote shell execution."""

    return shlex.join(list(argv))


def chain_remote_commands(commands: Sequence[Sequence[str]]) -> str:
    """Quote and chain multiple remote commands with &&."""

    return " && ".join(shell_join(command) for command in commands)


def get_workspace_known_hosts_path() -> str:
    """Return the active workspace known_hosts path, ensuring the workspace exists."""

    ensure_workspace_dir()
    return get_known_hosts_path()


def get_ssh_control_path(
    host: str,
    username: str,
    ssh_key: str | None = None,
    *,
    known_hosts_path: str | None = None,
) -> str:
    """Return a private, reusable OpenSSH multiplexing socket path.

    A Proxmox inspection command can execute many SSH sessions in sequence.
    Multiplexing lets the first session authenticate an encrypted key and lets
    the remaining sessions reuse that authenticated connection instead of
    prompting for the key passphrase repeatedly.  The short-lived socket is
    stable across adjacent CLI invocations so ``probe`` followed by ``audit``
    can reuse it while OpenSSH's ``ControlPersist`` window is active.
    Strict transport returns OpenSSH's ``none`` sentinel without creating a path.
    """
    if _STRICT_TRANSPORT.get() is not None:
        return "none"
    trust_path = (
        os.path.abspath(os.path.expanduser(known_hosts_path))
        if known_hosts_path is not None else get_known_hosts_path()
    )
    identity = "\0".join((host, username, ssh_key or "", trust_path))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    control_dir = os.path.join(
        tempfile.gettempdir(), f"basaltwater-ssh-{os.getuid()}"
    )
    try:
        os.mkdir(control_dir, mode=0o700)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(control_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError(f"SSH control directory must be owned by this user with mode 0700: {control_dir}")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValueError(f"Unsafe SSH control directory: {control_dir}") from exc
    return os.path.join(control_dir, f"{digest}-%p.sock")


def build_ssh_command(
    host: str,
    username: str,
    ssh_key: str | None = None,
    *,
    port: int | str | None = None,
    remote_command: str | None = None,
    batch_mode: bool | None = None,
    connect_timeout: int | None = 30,
    server_alive_interval: int | None = 30,
    control_path: str | None = None,
    known_hosts_path: str | None = None,
    allow_host_key_prompt: bool = False,
) -> list[str]:
    """Build SSH options, allowing first-use confirmation only for live setup."""

    policy = _STRICT_TRANSPORT.get()
    command = ["ssh"]
    if policy is not None:
        if host != policy.host:
            raise ValueError("SSH host differs from the resolved strict endpoint")
        ssh_key, known_hosts_path, port = policy.identity_file, policy.known_hosts_path, policy.port
        batch_mode, allow_host_key_prompt, control_path = True, False, None
        command = [
            "/usr/bin/ssh", "-F", "/dev/null",
            "-o", "IdentityFile=none", "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none", "-o", "CertificateFile=none",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "UpdateHostKeys=no",
            "-o", "VerifyHostKeyDNS=no", "-o", "CanonicalizeHostname=no",
            "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
            "-o", "PreferredAuthentications=publickey", "-o", "ForwardAgent=no",
            "-o", "ClearAllForwardings=yes", "-o", "ProxyCommand=none", "-o", "ProxyJump=none",
            "-o", "ControlMaster=no", "-o", "ControlPersist=no", "-o", "ControlPath=none",
            "-o", "PermitLocalCommand=no", "-o", "RequestTTY=no", "-o", "ConnectionAttempts=1",
        ]
    if ssh_key:
        command.extend(["-i", ssh_key])
    if port is not None:
        command.extend(["-p", str(port)])

    resolved_known_hosts = (
        os.path.abspath(os.path.expanduser(known_hosts_path))
        if known_hosts_path is not None
        else get_workspace_known_hosts_path()
    )
    command.extend(["-o", f"UserKnownHostsFile={resolved_known_hosts}"])
    if batch_mode is None:
        batch_mode = ssh_batch_mode()
    # OpenSSH uses /dev/tty for confirmation even when stdin carries an upload.
    # `ask` still refuses changed keys; automation must use enrolled trust.
    host_key_checking = "ask" if allow_host_key_prompt and not batch_mode else "yes"
    command.extend(["-o", f"StrictHostKeyChecking={host_key_checking}"])
    if batch_mode is True:
        command.extend(["-o", "BatchMode=yes"])
    elif batch_mode is False:
        command.extend(["-o", "BatchMode=no"])
    if connect_timeout is not None:
        command.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if server_alive_interval is not None:
        command.extend(["-o", f"ServerAliveInterval={server_alive_interval}"])
    if control_path:
        command.extend([
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=60s",
            "-o", f"ControlPath={control_path}",
        ])

    command.append(f"{username}@{host}")
    if remote_command is not None:
        command.append(remote_command)
    return command


def ensure_remote_sudo(
    host: str,
    username: str,
    ssh_key: str | None = None,
    *,
    control_path: str | None = None,
    timeout: int = 60,
) -> bool:
    """Verify remote sudo and interactively authenticate when necessary.

    Setup uploads use SSH stdin for a tar stream, so a remote sudo prompt
    cannot safely be allowed to consume that stream. Probe non-interactively
    and require the setup account to have the intended passwordless policy.
    A prior ``sudo -v`` in another SSH session is not reliable because sudo
    normally scopes cached credentials to a terminal or parent process.
    """
    if username == "root":
        return True

    batch_mode = ssh_batch_mode()
    probe = build_ssh_command(
        host,
        username,
        ssh_key,
        remote_command="sudo -n true",
        batch_mode=batch_mode,
        connect_timeout=30,
        server_alive_interval=30,
        control_path=control_path,
    )
    attempts = 1 if batch_mode else 2
    for attempt in range(attempts):
        try:
            result = subprocess.run(
                probe,
                check=False,
                capture_output=True,
                text=True,
                timeout=ssh_process_timeout(timeout, batch_mode=batch_mode),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Error: could not verify remote sudo for {username}@{host}: {exc}")
            return False

        if result.returncode != 255 or attempt + 1 == attempts:
            break
        print(
            "Warning: interactive SSH authentication ended before remote sudo "
            "verification; retrying once."
        )

    if result.returncode == 0:
        return True

    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode == 255:
        print(
            f"Error: could not establish SSH for remote sudo verification "
            f"to {username}@{host}."
        )
        if detail:
            print(f"  SSH check: {detail[:240]}")
        return False
    print(
        f"Error: {username}@{host} does not provide the required non-interactive "
        "sudo access. Configure the setup user with the intended NOPASSWD rule "
        "before retrying."
    )
    if detail:
        print(f"  Remote sudo check: {detail[:240]}")
    return False


def build_scp_command(
    host: str,
    username: str,
    source_path: str,
    destination_path: str,
    ssh_key: str | None = None,
    *,
    port: int | str | None = None,
    batch_mode: bool | None = None,
    connect_timeout: int | None = 30,
) -> list[str]:
    """Build an SCP command with consistent options."""

    command = ["scp"]
    if ssh_key:
        command.extend(["-i", ssh_key])
    if port is not None:
        command.extend(["-P", str(port)])

    command.extend(["-o", f"UserKnownHostsFile={get_workspace_known_hosts_path()}"])
    command.extend(["-o", "StrictHostKeyChecking=yes"])
    if batch_mode is None:
        batch_mode = ssh_batch_mode()
    if batch_mode is True:
        command.extend(["-o", "BatchMode=yes"])
    elif batch_mode is False:
        command.extend(["-o", "BatchMode=no"])
    if connect_timeout is not None:
        command.extend(["-o", f"ConnectTimeout={connect_timeout}"])

    command.extend([source_path, f"{username}@{host}:{destination_path}"])
    return command


def build_rsync_ssh_transport(
    *,
    ssh_key: str | None = None,
    port: int | str | None = None,
    batch_mode: bool | None = None,
    connect_timeout: int | None = 30,
) -> str:
    """Build the rsync -e SSH transport string with quoted argv."""

    ssh_command = ["ssh"]
    if ssh_key:
        ssh_command.extend(["-i", ssh_key])
    if port is not None:
        ssh_command.extend(["-p", str(port)])
    ssh_command.extend(["-o", f"UserKnownHostsFile={get_workspace_known_hosts_path()}"])
    ssh_command.extend(["-o", "StrictHostKeyChecking=yes"])
    if batch_mode is None:
        batch_mode = ssh_batch_mode()
    if batch_mode is True:
        ssh_command.extend(["-o", "BatchMode=yes"])
    elif batch_mode is False:
        ssh_command.extend(["-o", "BatchMode=no"])
    if connect_timeout is not None:
        ssh_command.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    return shell_join(ssh_command)
