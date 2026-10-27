"""One-shot privileged SSH reconciliation. Never installed as a service payload."""

from __future__ import annotations

import fcntl
import gzip
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
from lib.machine_state import can_manage_system_services
from lib.remote_utils import run
from lib.setup_directories import directory_fd
from lib.unit_transaction import remove_units, replace_units
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

CONFIG_DIR = Path("/etc/basaltwater/cachyos-ssh")
UNIT_DIR = Path("/etc/systemd/system")
DESCRIPTION = "Basaltwater CachyOS SSH"
BOOTSTRAP_DESCRIPTION = "Basaltwater CachyOS SSH password bootstrap"
VENDOR_UNITS = ("sshd.service", "sshd.socket", "ssh.service", "ssh.socket")
VENDOR_DIR = Path("/usr/lib/systemd/system")
PACKAGE_DB = Path("/var/lib/pacman/local")


def _command(*args: str) -> str:
    return run(list(args), capture_output=True, timeout=30).stdout or ""


def _show(unit: str) -> dict[str, str]:
    output = _command("/usr/bin/systemctl", "show", unit, "--no-pager",
                      "--property=LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths,"
                      "MainPID,Transient,Description,RuntimeMaxUSec,TimeoutStopUSec,KillMode,Restart,User,ExecStart,"
                      "InaccessiblePaths,Accept,Triggers")
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
    with os.fdopen(fd, encoding="utf-8", newline="") as stream:
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


def _unit(path: Path, *, legacy: bool = False) -> str:
    return (f"[Unit]\nDescription={DESCRIPTION}\nAfter=network.target\n\n"
            "[Service]\nType=exec\nUser=root\n"
            f"ExecStart=/usr/bin/sshd -D -e -f {path}\n"
            "Restart=on-failure\nRestartSec=5s\n"
            f"KillMode={'control-group' if legacy else 'process'}\nTimeoutStopSec=5s\n\n"
            "[Install]\nWantedBy=multi-user.target\n")


def _owned_config(path: Path, modes: set[str], *, metadata_only: bool = False) -> bool:
    if path.parent != CONFIG_DIR:
        return False
    match = re.fullmatch(r"([a-z_][a-z0-9_-]{0,31})\.(key|password|bootstrap)\.([a-f0-9]{20})\.conf", path.name)
    if not match or match[1] == "root" or match[2] not in modes:
        return False
    return (path == _config_path(match[1], match[2])
            and (metadata_only or _read(path) == _config(match[1], match[2])))


def _main_owned(state: dict[str, str], *, metadata_only: bool = False) -> bool:
    content = _read(UNIT_DIR / SERVICE)
    if content is None:
        if state.get("LoadState") != "not-found":
            raise ValueError("Foreign main SSH unit retained")
        return False
    match = re.search(r"^ExecStart=/usr/bin/sshd -D -e -f (\S+)$", content, re.M)
    if (not match or not _owned_config(Path(match[1]), {"key", "password"}, metadata_only=metadata_only)
            or content not in {_unit(Path(match[1])), _unit(Path(match[1]), legacy=True)}
            or state.get("FragmentPath") != str(UNIT_DIR / SERVICE)
            or state.get("Transient") != "no" or state.get("User") != "root"
            or state.get("Description") != DESCRIPTION or state.get("Restart") != "on-failure"
            or state.get("KillMode") not in {"process", "control-group"}
            or not re.fullmatch(r"\{ path=/usr/bin/sshd ; argv\[\]=/usr/bin/sshd -D -e -f "
                                + re.escape(match[1]) + r" ;[^{}\n]*\}", state.get("ExecStart", ""))):
        raise ValueError("Modified or foreign main SSH unit retained")
    return True


def _no_overrides(unit: str, state: dict[str, str]) -> None:
    if state.get("DropInPaths", ""):
        raise ValueError(f"SSH unit drop-ins retained: {unit}")
    for base in _unit_directories():
        # Also refuse empty or currently unloaded unit-specific drop-in dirs.
        for name in (unit + ".d", unit.rsplit(".", 1)[1] + ".d"):
            if os.path.lexists(base / name):
                raise ValueError(f"SSH unit drop-in directory retained: {base / name}")


def _unit_directories() -> tuple[Path, ...]:
    return UNIT_DIR, Path("/run/systemd/system"), Path("/usr/lib/systemd/system")


def _bootstrap_owned(state: dict[str, str], *, metadata_only: bool = False) -> bool:
    if state.get("LoadState") == "not-found":
        return False
    command = state.get("ExecStart", "")
    match = re.search(r"argv\[\]=/usr/bin/sshd -D -e -f (\S+) ;", command)
    if (state.get("Transient") != "yes" or state.get("Description") != BOOTSTRAP_DESCRIPTION
            or state.get("User") != "root" or state.get("KillMode") != "control-group"
            or state.get("Restart") != "no" or not match
            or not _owned_config(Path(match[1]), {"bootstrap"}, metadata_only=metadata_only)):
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


def _port_free(port: int, allowed_pid: str = "0", *, other_pids: tuple[str, ...] = (),
               metadata_only: bool = False) -> None:
    allowed = {pid for pid in (allowed_pid, *other_pids) if re.fullmatch(r"[1-9][0-9]*", pid)}
    lines = _command("/usr/bin/ss", "-H", "-ltnp", "sport", "=", f":{port}").splitlines()
    for line in lines:
        pids = re.findall(r"pid=(\d+)", line)
        if not allowed or (not pids and not metadata_only) or not set(pids) <= allowed:
            raise ValueError(f"Port {port} is occupied by an unrelated listener")


def _known_state(unit: str, state: dict[str, str]) -> None:
    # No start job is executing while our Restart=on-failure daemon waits
    # between attempts. Let ownership checks and the unit transaction repair
    # or disable it; actual start/stop transitions remain unsafe to adopt.
    retrying = (unit == SERVICE and state.get("LoadState") == "loaded"
                and state.get("ActiveState") == "activating"
                and state.get("SubState") == "auto-restart")
    if (state.get("LoadState") not in {"loaded", "not-found"}
            or (state.get("ActiveState") not in {"active", "inactive", "failed"} and not retrying)
            or state.get("UnitFileState") not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect"}
            or (state.get("LoadState") == "loaded" and not state.get("UnitFileState"))
            or (state["LoadState"] == "not-found" and
                (state["ActiveState"] != "inactive" or state["UnitFileState"] != ""))):
        raise ValueError(f"Uninspectable, masked or transitioning SSH unit retained: {unit}")


def _packaged_file(path: Path) -> None:
    """Require a native, unmodified package file; ignore unrelated config edits."""
    _root_directory(path.parent)
    content = _read(path)
    if content is None:
        raise ValueError(f"Missing native SSH unit: {path}")
    if _command("/usr/bin/pacman", "-Qqo", "--", str(path)).strip() != "openssh":
        raise ValueError(f"Foreign SSH package file retained: {path}")
    package = _command("/usr/bin/pacman", "-Q", "--", "openssh").strip()
    match = re.fullmatch(r"openssh ([A-Za-z0-9.+_:~\-]+)", package)
    if not match:
        raise ValueError("Cannot identify the installed openssh package version")
    metadata = PACKAGE_DB / ("openssh-" + match[1]) / "mtree"
    _root_directory(metadata.parent)
    try:
        fd = os.open(metadata, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as raw:
            _secure(os.fstat(raw.fileno()))
            with gzip.GzipFile(fileobj=raw) as stream:
                manifest = stream.read(1024 * 1024 + 1)
        if len(manifest) > 1024 * 1024:
            raise ValueError("openssh package manifest exceeds 1 MiB")
        # Unlike pacman -Qkk's size/mtime checks, verify the actual unit bytes
        # against the package's SHA256 entry. Unrelated sshd_config edits do
        # not prevent adoption. No package database file is ever modified.
        entry = "./usr/lib/systemd/system/" + path.name
        hashes = [field.removeprefix("sha256digest=")
                  for line in manifest.decode("utf-8").splitlines()
                  if line.split(" ", 1)[0] == entry
                  for field in line.split()[1:] if field.startswith("sha256digest=")]
        if hashes != [hashlib.sha256(content.encode("utf-8")).hexdigest()]:
            raise ValueError(f"Modified stock SSH unit retained: {path}")
    except (OSError, EOFError, UnicodeError) as exc:
        raise ValueError(f"Cannot verify openssh package manifest: {metadata}") from exc


def _native_states() -> dict[str, dict[str, str]]:
    snapshots = {}
    for unit in VENDOR_UNITS:
        state = _show(unit)
        _known_state(unit, state)
        _no_overrides(unit, state)
        if state["LoadState"] == "not-found":
            # An unloaded /etc or /run definition must not become an implicit
            # adoption after daemon-reload.
            if any(os.path.lexists(base / unit) for base in _unit_directories()):
                raise ValueError(f"Unloaded SSH unit retained: {unit}")
            continue
        if (unit not in {"sshd.service", "sshd.socket"}
                or state.get("FragmentPath") != str(VENDOR_DIR / unit)
                or state.get("Transient") != "no"
                or any(os.path.lexists(base / unit) for base in _unit_directories() if base != VENDOR_DIR)):
            raise ValueError(f"Foreign native SSH unit retained: {unit}")
        _packaged_file(VENDOR_DIR / unit)
        if unit == "sshd.service":
            # Arch's daemon unit uses this exact invocation and preserves
            # children on stop. Never infer session safety from PAM/logind.
            if (state.get("KillMode") != "process" or state.get("User") not in {"", "root"}
                    or not re.fullmatch(r"\{ path=/usr/bin/sshd ; argv\[\]=/usr/bin/sshd -D ;[^{}\n]*\}",
                                        state.get("ExecStart", ""))):
                raise ValueError("Stock sshd.service must run native sshd -D with KillMode=process")
        elif unit == "sshd.socket":
            # Only a package-owned socket that activates the checked daemon is
            # safe. Accept=yes templates can have uninspectable live instances.
            if state.get("Accept") != "no" or state.get("Triggers", "").split() != ["sshd.service"]:
                raise ValueError("SSH socket must activate stock sshd.service with Accept=no")
        snapshots[unit] = state
    if "sshd.socket" in snapshots and "sshd.service" not in snapshots:
        raise ValueError("SSH socket has no recognizable stock daemon")
    return snapshots


def _key_metadata(info: os.stat_result, uid: int, *, directory: bool) -> None:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid not in {0, uid} or info.st_mode & 0o022
            or (not directory and info.st_nlink != 1)):
        raise ValueError("authorized_keys and parents must be owned by the account/root and not writable by others")


def _public_candidates(content: str) -> list[str]:
    # Fingerprinting also accepts known_hosts entries, whose hostname field is
    # not a valid authorized_keys option. Require the authorized_keys grammar
    # first, then let native OpenSSH validate the encoded public-key structure.
    flags = {"agent-forwarding", "cert-authority", "no-agent-forwarding", "no-port-forwarding",
             "no-pty", "no-user-rc", "no-x11-forwarding", "no-touch-required", "port-forwarding",
             "pty", "restrict", "user-rc", "verify-required", "x11-forwarding"}
    values = {"command", "environment", "expiry-time", "from", "permitlisten", "permitopen", "principals", "tunnel"}
    option = r'[a-zA-Z0-9-]+(?:="(?:[^"\\]|\\.)*")?'
    pattern = re.compile(r"^(?:(?P<options>" + option + r"(?:," + option + r")*)[ \t]+)?"
                         r"(?:ssh-[a-z0-9@.-]+|ecdsa-sha2-[a-z0-9@.-]+|sk-[a-z0-9@.-]+)"
                         r"[ \t]+[A-Za-z0-9+/]+={0,2}(?:[ \t]+.*)?$")
    candidates = []
    for raw in content.splitlines():
        line = raw.strip()
        if len(line.encode("utf-8")) > 8192:
            continue
        match = pattern.fullmatch(line)
        if match is None:
            continue
        options = re.findall(option, match["options"] or "")
        if all((item.split("=", 1)[0].lower() in values if "=" in item else item.lower() in flags)
               for item in options):
            candidates.append(line)
    return candidates


def _authorized_key(username: str) -> bool:
    """Read only authorized_keys through checked directory descriptors."""
    account = pwd.getpwnam(username)
    home = account.pw_dir
    validate_filesystem_path(home)
    if not os.path.isabs(home) or os.path.normpath(home) != home:
        raise ValueError("SSH account home must be an absolute normalized path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _key_metadata(os.fstat(fd), account.pw_uid, directory=True)
        for component in (*Path(home).parts[1:], ".ssh"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _key_metadata(os.fstat(fd), account.pw_uid, directory=True)
        key_fd = os.open("authorized_keys", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(key_fd, encoding="utf-8") as stream:
            _key_metadata(os.fstat(stream.fileno()), account.pw_uid, directory=False)
            content = stream.read(1024 * 1024 + 1)
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("authorized_keys exceeds 1 MiB")
        # Only OpenSSH public-key lines are candidates. In particular do not
        # ask ssh-keygen to fingerprint PEM/private-key blocks or RFC4716 files.
        candidates = _public_candidates(content)
        if not candidates:
            return False
        # ssh-keygen parses OpenSSH wire keys (including authorized_keys options),
        # rather than treating a base64-looking string as an enrolled key.
        result = run(["/usr/bin/ssh-keygen", "-lf", "-"], input_data="\n".join(candidates) + "\n",
                     capture_output=True, check=False, timeout=30)
        return result.returncode == 0 and bool((result.stdout or "").strip())
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot safely inspect the selected account's authorized_keys") from exc
    finally:
        os.close(fd)


def inspect_setup(username: str, mode: str, minutes: int, *, metadata_only: bool = False
                  ) -> tuple[dict[str, str], bool, dict[str, dict[str, str]]]:
    """Read-only adoption plan. Root repeats it immediately before mutations.

    metadata_only skips private managed config reads and tolerates hidden root
    listener PIDs when a recognized daemon is active; it never skips key checks.
    """
    if (not validate_username(username) or username == "root"
            or mode not in {"leave", "off", "key", "password"}
            or type(minutes) is not int or not 0 <= minutes <= 60 or (minutes and mode != "key")):
        raise ValueError("Invalid SSH setup arguments")
    if pwd.getpwnam(username).pw_uid == 0:
        raise ValueError("SSH requires an existing non-root account")
    bootstrap = _show(BOOTSTRAP_SERVICE)
    _no_overrides(BOOTSTRAP_SERVICE, bootstrap)
    _bootstrap_owned(bootstrap, metadata_only=metadata_only)
    if mode == "leave":
        return {}, False, {}
    _root_directory(UNIT_DIR)
    _root_directory(CONFIG_DIR)
    state = _show(SERVICE)
    _known_state(SERVICE, state)
    _no_overrides(SERVICE, state)
    owned = _main_owned(state, metadata_only=metadata_only)
    native = _native_states()
    if native:
        _native_binaries()
    allowed = [state.get("MainPID", "0")] if owned and state["ActiveState"] == "active" else []
    daemon = native.get("sshd.service", {})
    if daemon.get("ActiveState") == "active":
        allowed.append(daemon.get("MainPID", "0"))
    if native.get("sshd.socket", {}).get("ActiveState") == "active":
        allowed.append("1")
    _port_free(SSH_PORT, other_pids=tuple(allowed), metadata_only=metadata_only)
    if mode in {"key", "password"}:
        if minutes:
            _port_free(BOOTSTRAP_PORT, bootstrap.get("MainPID", "0"), metadata_only=metadata_only)
        if mode == "key" and not minutes and any(
                item["ActiveState"] == "active" or item["UnitFileState"] in {"enabled", "enabled-runtime"}
                for item in native.values()) and not _authorized_key(username):
            raise ValueError(f"Cannot adopt native SSH as key-only: enroll a public key in {username}'s "
                             "~/.ssh/authorized_keys (ssh-copy-id), or explicitly select persistent "
                             "password authentication or a bounded SSH password window")
    return state, owned, native


def _validate_config(path: Path, username: str, mode: str) -> None:
    _command("/usr/bin/sshd", "-t", "-f", str(path))
    output = _command("/usr/bin/sshd", "-T", "-f", str(path))
    actual = {key.lower(): value for line in output.splitlines() if " " in line
              for key, value in [line.split(" ", 1)]}
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
    if (os.geteuid() != 0 or not is_cachyos() or platform.machine() != "x86_64"
            or not can_manage_system_services()):
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
    state, owned, native = inspect_setup(username, mode, minutes)
    # Refusals above are read-only. Close an earlier one-shot window even when
    # no new window was requested, and never replay a persisted deadline.
    _close_bootstrap()
    if mode == "leave":
        return
    path = None
    if mode != "off":
        _native_binaries()
        _command("/usr/bin/ssh-keygen", "-A")
        path = _immutable_config(username, mode)
        _validate_config(path, username, mode)
        # Establish/verify the explicitly requested bounded recovery endpoint
        # before disabling a password-capable native daemon.
        if minutes:
            _start_bootstrap(username, minutes)
    touched: dict[str, dict[str, str]] = {}
    try:
        if owned:
            _session_safe_main(state)
        # Stop the socket first so it cannot reactivate the native daemon.
        for unit in ("sshd.socket", "sshd.service"):
            if unit in native:
                touched[unit] = native[unit]
                _disable_native(unit, native[unit])
        if mode == "off":
            if owned:
                remove_units((SERVICE,), unit_dir=str(UNIT_DIR))
            return
        assert path is not None
        if (owned and _read(UNIT_DIR / SERVICE) == _unit(path)
                and state["ActiveState"] == "active"):
            # Enabling does not restart the listener or its existing sessions.
            if state["UnitFileState"] != "enabled":
                _command("/usr/bin/systemctl", "enable", SERVICE)
            current = _show(SERVICE)
            if current.get("ActiveState") != "active" or current.get("UnitFileState") != "enabled":
                raise RuntimeError("Unchanged SSH unit did not remain active and enabled")
        else:
            replace_units({SERVICE: _unit(path)}, activate=(SERVICE,), unit_dir=str(UNIT_DIR))
        if mode == "key" and not minutes:
            print("  Key-only SSH requires your public key in ~/.ssh/authorized_keys before connecting")
    except BaseException as error:
        errors = []
        if minutes:
            try:
                _close_bootstrap()
            except BaseException as exc:
                errors.append(f"close {BOOTSTRAP_SERVICE}: {exc}")
        errors.extend(_restore_native(touched))
        if errors:
            raise RuntimeError(f"SSH reconciliation failed ({error}); native SSH rollback needs "
                               "manual recovery: " + "; ".join(errors)) from error
        raise


def _session_safe_main(state: dict[str, str]) -> None:
    """Migrate the previous owned unit without stopping any process.

    Commit this safety-only change before the activating transaction, so even
    its rollback restores a KillMode=process definition. Config stays the same.
    """
    content = _read(UNIT_DIR / SERVICE)
    assert content is not None
    match = re.search(r"^ExecStart=/usr/bin/sshd -D -e -f (\S+)$", content, re.M)
    assert match is not None
    if content != _unit(Path(match[1])) or state.get("KillMode") != "process":
        replace_units({SERVICE: _unit(Path(match[1]))}, activate=(), unit_dir=str(UNIT_DIR))
    if _show(SERVICE).get("KillMode") != "process":
        raise RuntimeError("Cannot verify session-preserving KillMode for managed SSH")


def _disable_native(unit: str, previous: dict[str, str]) -> None:
    if previous["ActiveState"] == "active":
        _command("/usr/bin/systemctl", "stop", unit)
    if previous["UnitFileState"] in {"enabled", "enabled-runtime"}:
        flags = ("--runtime",) if previous["UnitFileState"] == "enabled-runtime" else ()
        _command("/usr/bin/systemctl", "disable", *flags, unit)
    actual = _show(unit)
    _known_state(unit, actual)
    if actual["ActiveState"] == "active" or actual["UnitFileState"] in {"enabled", "enabled-runtime"}:
        raise RuntimeError(f"Native SSH did not stop/disable: {unit}")


def _restore_native(snapshots: dict[str, dict[str, str]]) -> list[str]:
    errors = []
    for unit, previous in reversed(tuple(snapshots.items())):
        # Do not stop the managed daemon here: unit_transaction owns its
        # rollback. Any unresolved port conflict remains an actionable error.
        actions = []
        if previous["UnitFileState"] in {"enabled", "enabled-runtime"}:
            flags = ("--runtime",) if previous["UnitFileState"] == "enabled-runtime" else ()
            actions.append(("/usr/bin/systemctl", "enable", *flags, unit))
        if previous["ActiveState"] == "active":
            actions.append(("/usr/bin/systemctl", "start", unit))
        for command in actions:
            try:
                _command(*command)
            except BaseException as exc:
                errors.append(f"{' '.join(command)}: {exc}")
        try:
            actual = _show(unit)
            _known_state(unit, actual)
            if ((actual["ActiveState"] == "active") != (previous["ActiveState"] == "active")
                    or actual["UnitFileState"] != previous["UnitFileState"]):
                raise RuntimeError("restored activity/enablement does not match snapshot")
        except BaseException as exc:
            errors.append(f"verify {unit}: {exc}")
    return errors


def _native_binaries() -> None:
    for binary in ("/usr/bin/sshd", "/usr/bin/ssh-keygen"):
        _root_directory(Path(binary).parent)
        _secure(os.stat(binary, follow_symlinks=False))
        if _command("/usr/bin/pacman", "-Qqo", "--", binary).strip() != "openssh":
            raise ValueError("SSH executables must belong to the native openssh package")


def main() -> None:
    if len(sys.argv) != 4 or not re.fullmatch(r"[0-9]{1,2}", sys.argv[3]):
        raise ValueError("Expected username, leave/off/key/password, and window minutes")
    reconcile(sys.argv[1], sys.argv[2], int(sys.argv[3]))


if __name__ == "__main__":
    main()
