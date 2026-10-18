"""One-shot privileged SSH reconciliation. Never installed as a service payload."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import sys

# Isolated Python deliberately omits the checkout from sys.path. This trusted
# setup helper executes once under sudo; persistent units execute native sshd.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.cachyos_ssh import BOOTSTRAP_PORT, BOOTSTRAP_SERVICE, SERVICE, SSH_PORT
from lib.atomic_io import write_text_atomic
from lib.cachyos import is_cachyos
from lib.remote_utils import run
from lib.setup_directories import directory_fd
from lib.unit_transaction import remove_units, replace_units
from lib.validators import validate_username

CONFIG_DIR = Path("/etc/basaltwater/cachyos-ssh")
UNIT_DIR = Path("/etc/systemd/system")
DESCRIPTION = "Basaltwater CachyOS SSH"
BOOTSTRAP_DESCRIPTION = "Basaltwater CachyOS SSH password bootstrap"
VENDOR_UNITS = ("sshd.service", "sshd.socket", "ssh.service", "ssh.socket",
                "sshd@.service")


def _command(*args: str) -> str:
    return run(list(args), capture_output=True, timeout=30).stdout or ""


def _show(unit: str) -> dict[str, str]:
    output = _command("/usr/bin/systemctl", "show", unit, "--no-pager",
                      "--property=LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths,"
                      "MainPID,Transient,Description,RuntimeMaxUSec,TimeoutStopUSec,KillMode,Restart,User,ExecStart,"
                      "InaccessiblePaths")
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _secure(info: os.stat_result, *, directory: bool = False) -> None:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022 or (not directory and info.st_nlink != 1)):
        raise ValueError("Managed SSH destinations must be root-owned and not writable by others")


def _root_directory(path: Path, *, create: bool = False) -> None:
    # Check ancestors too: a safe final directory under a writable parent is
    # replaceable. directory_fd additionally refuses symlinks at every level.
    for parent in reversed((path, *path.parents)):
        try:
            with directory_fd(str(parent), create=create) as fd:
                _secure(os.fstat(fd), directory=True)
        except FileNotFoundError:
            if not create:
                return
            raise


def _read(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as stream:
        _secure(os.fstat(stream.fileno()))
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("Managed SSH file exceeds limit")
    return content


def _config(username: str, mode: str) -> str:
    bootstrap = mode == "bootstrap"
    password = mode in {"password", "bootstrap"}
    return ("# Basaltwater managed standalone sshd configuration\n"
            f"Port {BOOTSTRAP_PORT if bootstrap else SSH_PORT}\n"
            "AddressFamily inet\nListenAddress 0.0.0.0\n"
            f"PidFile /run/{'basaltwater-cachyos-ssh-bootstrap' if bootstrap else 'basaltwater-cachyos-ssh'}.pid\n"
            f"AllowUsers {username}\nPermitRootLogin no\nPermitEmptyPasswords no\n"
            "PubkeyAuthentication yes\nKbdInteractiveAuthentication no\nUsePAM yes\n"
            f"PasswordAuthentication {'yes' if password else 'no'}\n"
            f"AuthenticationMethods {'publickey password' if password else 'publickey'}\n"
            "Subsystem sftp internal-sftp\n")


def _config_path(username: str, mode: str) -> Path:
    digest = hashlib.sha256(_config(username, mode).encode()).hexdigest()[:20]
    return CONFIG_DIR / f"{username}.{mode}.{digest}.conf"


def _immutable_config(username: str, mode: str) -> Path:
    path = _config_path(username, mode)
    expected = _config(username, mode)
    previous = _read(path)
    if previous is not None and previous != expected:
        raise ValueError(f"Modified SSH config retained: {path}")
    if previous is None:
        write_text_atomic(str(path), expected, mode=0o600, uid=0, gid=0)
    return path


def _unit(path: Path) -> str:
    return (f"[Unit]\nDescription={DESCRIPTION}\nAfter=network.target\n\n"
            "[Service]\nType=exec\nUser=root\n"
            f"ExecStart=/usr/bin/sshd -D -e -f {path}\n"
            "Restart=on-failure\nRestartSec=5s\nKillMode=control-group\nTimeoutStopSec=5s\n\n"
            "[Install]\nWantedBy=multi-user.target\n")


def _owned_config(path: Path, modes: set[str]) -> bool:
    if path.parent != CONFIG_DIR:
        return False
    match = re.fullmatch(r"([a-z_][a-z0-9_-]{0,31})\.(key|password|bootstrap)\.([a-f0-9]{20})\.conf", path.name)
    if not match or match[1] == "root" or match[2] not in modes:
        return False
    return path == _config_path(match[1], match[2]) and _read(path) == _config(match[1], match[2])


def _main_owned(state: dict[str, str]) -> bool:
    content = _read(UNIT_DIR / SERVICE)
    if content is None:
        if state.get("LoadState") != "not-found":
            raise ValueError("Foreign main SSH unit retained")
        return False
    match = re.search(r"^ExecStart=/usr/bin/sshd -D -e -f (\S+)$", content, re.M)
    if (not match or not _owned_config(Path(match[1]), {"key", "password"})
            or content != _unit(Path(match[1]))
            or state.get("FragmentPath") != str(UNIT_DIR / SERVICE)
            or state.get("Transient") != "no"):
        raise ValueError("Modified or foreign main SSH unit retained")
    return True


def _no_overrides(unit: str, state: dict[str, str]) -> None:
    if state.get("DropInPaths", ""):
        raise ValueError(f"SSH unit drop-ins retained: {unit}")
    for base in _unit_directories():
        # Also refuse empty or currently unloaded unit-specific drop-in dirs.
        if os.path.lexists(base / (unit + ".d")):
            raise ValueError(f"SSH unit drop-in directory retained: {unit}")


def _unit_directories() -> tuple[Path, ...]:
    return UNIT_DIR, Path("/run/systemd/system"), Path("/usr/lib/systemd/system")


def _bootstrap_owned(state: dict[str, str]) -> bool:
    if state.get("LoadState") == "not-found":
        return False
    command = state.get("ExecStart", "")
    match = re.search(r"argv\[\]=/usr/bin/sshd -D -e -f (\S+) ;", command)
    if (state.get("Transient") != "yes" or state.get("Description") != BOOTSTRAP_DESCRIPTION
            or state.get("User") != "root" or state.get("KillMode") != "control-group"
            or state.get("Restart") != "no" or not match
            or not _owned_config(Path(match[1]), {"bootstrap"})):
        raise ValueError("Unrecognized bootstrap unit retained; inspect it manually")
    return True


def _close_bootstrap() -> None:
    state = _show(BOOTSTRAP_SERVICE)
    _no_overrides(BOOTSTRAP_SERVICE, state)
    if _bootstrap_owned(state):
        _command("/usr/bin/systemctl", "stop", BOOTSTRAP_SERVICE)
        if _show(BOOTSTRAP_SERVICE).get("ActiveState") not in {"inactive", "failed"}:
            raise RuntimeError("SSH bootstrap did not stop")


def _seconds(value: str) -> float:
    """Parse systemd's formatted timespan without assuming minutes stay minutes."""
    parts = re.findall(r"(\d+)(h|min|s|ms|us)", value)
    if not parts or " ".join(number + unit for number, unit in parts) != value:
        raise ValueError("Unrecognized systemd runtime limit")
    factors = {"h": 3600, "min": 60, "s": 1, "ms": 0.001, "us": 0.000001}
    return sum(int(number) * factors[unit] for number, unit in parts)


def _port_free(port: int, allowed_pid: str = "0") -> None:
    lines = _command("/usr/bin/ss", "-H", "-ltnp", "sport", "=", f":{port}").splitlines()
    for line in lines:
        pids = re.findall(r"pid=(\d+)", line)
        if allowed_pid == "0" or not pids or set(pids) != {allowed_pid}:
            raise ValueError(f"Port {port} is occupied by an unrelated listener")


def _validate_config(path: Path, username: str, mode: str) -> None:
    _command("/usr/bin/sshd", "-t", "-f", str(path))
    output = _command("/usr/bin/sshd", "-T", "-f", str(path))
    actual = dict(line.split(" ", 1) for line in output.splitlines() if " " in line)
    expected = dict(line.lower().split(" ", 1) for line in _config(username, mode).splitlines()
                    if " " in line and not line.startswith("#"))
    for key in ("port", "addressfamily", "allowusers", "permitrootlogin", "permitemptypasswords",
                "pubkeyauthentication", "kbdinteractiveauthentication", "usepam",
                "passwordauthentication", "authenticationmethods"):
        if actual.get(key) != expected[key]:
            raise RuntimeError(f"Native sshd configuration verification failed: {key}")


def _start_bootstrap(username: str, minutes: int) -> None:
    try:
        path = _immutable_config(username, "bootstrap")
        _validate_config(path, username, "bootstrap")
        _port_free(BOOTSTRAP_PORT)
        # pam_systemd normally moves SSH sessions into logind scopes outside
        # this unit's KillMode boundary. Keep the bootstrap's PAM authentication
        # but deny logind's system bus/varlink endpoints in its mount namespace,
        # so a session cannot outlive the transient service by registration.
        _command("/usr/bin/systemd-run", "--unit=" + BOOTSTRAP_SERVICE,
                 "--description=" + BOOTSTRAP_DESCRIPTION, "--collect", "--property=User=root",
                 "--property=Type=exec",
                 f"--property=RuntimeMaxSec={minutes * 60}", "--property=TimeoutStopSec=5s",
                 "--property=KillMode=control-group", "--property=Restart=no",
                 "--property=InaccessiblePaths=/run/systemd /run/dbus",
                 "/usr/bin/sshd", "-D", "-e", "-f", str(path))
        state = _show(BOOTSTRAP_SERVICE)
        if (not _bootstrap_owned(state) or state.get("ActiveState") != "active"
                or _seconds(state.get("RuntimeMaxUSec", "")) != minutes * 60
                or not 0 < _seconds(state.get("TimeoutStopUSec", "")) <= 5
                or set(state.get("InaccessiblePaths", "").split()) != {"/run/systemd", "/run/dbus"}):
            raise RuntimeError("SSH bootstrap runtime deadline could not be verified")
    except BaseException:
        # Validation and failed starts must close any recognized endpoint, but
        # never stop a foreign unit that appeared under this name.
        _close_bootstrap()
        raise


def reconcile(username: str, mode: str, minutes: int) -> None:
    """Revalidate privilege/identity, then reconcile only owned SSH resources."""
    if (not validate_username(username) or username == "root" or mode not in {"leave", "off", "key", "password"}
            or type(minutes) is not int or not 0 <= minutes <= 60 or (minutes and mode != "key")):
        raise ValueError("Invalid SSH setup arguments")
    if os.geteuid() != 0 or not is_cachyos() or platform.machine() != "x86_64":
        raise ValueError("SSH helper requires root on CachyOS x86_64")
    account = pwd.getpwnam(username)
    if account.pw_uid == 0 or os.environ.get("SUDO_UID") != str(account.pw_uid):
        raise ValueError("SSH helper requires sudo from the selected desktop account")
    _root_directory(CONFIG_DIR, create=True)
    _root_directory(UNIT_DIR)
    with directory_fd(str(CONFIG_DIR)) as directory:
        fd = os.open("setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            _secure(os.fstat(fd))
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o600:
                raise ValueError("SSH setup lock must be private")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _reconcile(username, mode, minutes)
        finally:
            os.close(fd)


def _reconcile(username: str, mode: str, minutes: int) -> None:
    # Close an earlier one-shot window even when no new window was requested.
    _close_bootstrap()
    if mode == "leave":
        return
    state = _show(SERVICE)
    _no_overrides(SERVICE, state)
    owned = _main_owned(state)
    if mode == "off":
        if owned:
            remove_units((SERVICE,), unit_dir=str(UNIT_DIR))
        return
    for unit in VENDOR_UNITS:
        vendor = _show(unit)
        if (vendor.get("ActiveState") not in {"inactive", "failed"}
                or vendor.get("UnitFileState") not in {"", "disabled", "static", "masked"}):
            raise ValueError(f"Conflicting vendor SSH unit retained: {unit}")
    _port_free(SSH_PORT, state.get("MainPID", "0") if owned else "0")
    _native_binaries()
    _command("/usr/bin/ssh-keygen", "-A")
    path = _immutable_config(username, mode)
    _validate_config(path, username, mode)
    replace_units({SERVICE: _unit(path)}, activate=(SERVICE,), unit_dir=str(UNIT_DIR))
    if minutes:
        _start_bootstrap(username, minutes)


def _native_binaries() -> None:
    for binary in ("/usr/bin/sshd", "/usr/bin/ssh-keygen"):
        _secure(os.stat(binary, follow_symlinks=False))
        if _command("/usr/bin/pacman", "-Qqo", "--", binary).strip() != "openssh":
            raise ValueError("SSH executables must belong to the native openssh package")


def main() -> None:
    if len(sys.argv) != 4 or not re.fullmatch(r"[0-9]{1,2}", sys.argv[3]):
        raise ValueError("Expected username, leave/off/key/password, and window minutes")
    reconcile(sys.argv[1], sys.argv[2], int(sys.argv[3]))


if __name__ == "__main__":
    main()
