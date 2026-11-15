"""Native client observations and explicit enrollment/lifecycle boundaries.

No enrollment credential or pending authentication URL is durable state.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
import re
import selectors
import signal
import stat
import subprocess
import time

from lib.atomic_io import parse_json, read_json_file, write_json_atomic
from lib.concurrency import resource_lock
from lib.headscale_config import validate_headscale_url
from lib.headscale_dns_binding import resolver_lifecycle_guard
from lib.setup_directories import check_directory_path, directory_fd
from lib.validation import validate_filesystem_path, validate_network_ip

CLIENT_STATE = "/var/lib/basaltwater/tailscale/client.json"
NATIVE_STATE = "/var/lib/tailscale/tailscaled.state"
NATIVE_SOCKET = "/run/tailscale/tailscaled.sock"
CONTROL_CONFIG = "/etc/headscale/config.yaml"
SECURITY_FLOOR = (1, 104, 4)
VERIFIED_VERSION = "1.104.7"
PRIVACY_DROPIN = "/etc/systemd/system/tailscaled.service.d/10-basaltwater-privacy.conf"
PRIVACY_CONTENT = '[Service]\nEnvironment="TS_NO_LOGS_NO_SUPPORT=true"\n'


class ClientError(RuntimeError, ValueError):
    """Safe diagnostic that never includes native command output."""


class ClientStarting(ClientError):
    """A known transient backend state; never an enrolled identity."""


@dataclass(frozen=True)
class ClientIdentity:
    node_id: str
    addresses: tuple[str, ...]
    login_server: str
    tags: tuple[str, ...]
    online: bool
    hostname: str


def native(args: list[str], *, timeout: int = 30, limit: int = 1024 * 1024) -> str:
    """Bound time and both output streams; suppress raw failures and AuthURL.

    Deliberately bypass the general setup logger: debug prefs can contain keys.
    """
    output = bytearray()
    try:
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, start_new_session=True,
                              env={**os.environ, "LC_ALL": "C"}) as proc:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    deadline = time.monotonic() + timeout
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ClientError("Native client command timed out; reconcile identity")
                        for key, _ in selector.select(min(remaining, 1)):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                output.extend(chunk)
                                if len(output) > limit:
                                    raise ClientError("Native client output exceeds limit")
                    if proc.wait(timeout=max(0.1, deadline - time.monotonic())):
                        raise ClientError("Native client command failed; reconcile identity")
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        return output.decode("utf-8")
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ClientError("Native client command unavailable or invalid") from None


def version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-t[0-9a-f]+(?:-g[0-9a-f]+)?)?", value):
        raise ClientError("Unsupported native client version")
    result = tuple(int(n) for n in value.split("-", 1)[0].split("."))
    if result < SECURITY_FLOOR or result[0] != 1 or result[1] % 2:
        raise ClientError("Native client is below the security floor or unsupported")
    return result


def _object(args: list[str]) -> dict:
    try:
        value = parse_json(native(args))
        if not isinstance(value, dict):
            raise ValueError()
        # Do not expose native authentication URLs to any caller.
        value.pop("AuthURL", None)
        return value
    except (ValueError, TypeError):
        raise ClientError("Invalid native client JSON") from None


def snapshot() -> tuple[dict, dict]:
    client = _object(["tailscale", "version", "--json"])
    status = _object(["tailscale", "status", "--json", "--peers=false"])
    if version(client.get("short")) != version(status.get("Version")):
        raise ClientError("Client and running daemon versions differ")
    raw_prefs = _object(["tailscale", "debug", "prefs"])
    prefs = {"ControlURL": raw_prefs.get("ControlURL")}
    if status.get("BackendState") == "NoState":
        raise ClientStarting("Native client is initializing")
    if not isinstance(status.get("BackendState"), str) or status["BackendState"] not in {"NeedsLogin", "NeedsMachineAuth", "Stopped", "Starting", "Running"}:
        raise ClientError("Unsupported native client state")
    if status.get("TUN") is not True:
        raise ClientError("Native kernel TUN client required")
    if type(status.get("HaveNodeKey", False)) is not bool:
        raise ClientError("Invalid native node-key state")
    return status, prefs


def wait_for_daemon(*, timeout: float = 15) -> tuple[dict, dict]:
    """Bounded read-only startup observation; never repeat a service mutation."""
    deadline = time.monotonic() + timeout
    while True:
        require_private_daemon()
        try:
            status, prefs = snapshot()
            if status["BackendState"] != "Starting":
                return status, prefs
        except ClientStarting:
            pass
        if time.monotonic() >= deadline:
            raise ClientError("Native client did not become ready; reconcile service state")
        time.sleep(0.25)


def _network(prefs: dict, expected_url: str) -> str:
    expected = validate_headscale_url(expected_url)
    actual = prefs.get("ControlURL")
    if not isinstance(actual, str) or validate_headscale_url(actual) != expected:
        raise ClientError("Client belongs to another network; explicit migration required")
    return expected


def check_network(status: dict, prefs: dict, url: str, phase: str) -> None:
    """Allow bootstrap defaults only before any native identity is issued."""
    if (phase in {"prepared", "left"} and status.get("BackendState") == "NeedsLogin"
            and not status.get("HaveNodeKey", False)):
        # After explicit leave/migration the old URL may remain in prefs.
        if phase == "left" or prefs.get("ControlURL") in (None, "", "https://controlplane.tailscale.com"):
            return
    _network(prefs, url)


def _identity(status: dict, prefs: dict, expected_url: str, require_running: bool) -> ClientIdentity:
    if status.get("BackendState") == "Starting":
        raise ClientStarting("Native client is initializing")
    if status.get("BackendState") not in ("Running", "Stopped"):
        raise ClientError("Client enrollment is pending or unavailable")
    url = _network(prefs, expected_url)
    peer = status.get("Self")
    if not isinstance(peer, dict):
        raise ClientError("No observed enrolled client identity")
    node = peer.get("ID")
    hostname = peer.get("HostName")
    online = peer.get("Online")
    if (not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", node)
            or not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", hostname)
            or type(online) is not bool):
        raise ClientError("Invalid observed client identity")
    addresses = peer.get("TailscaleIPs")
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 8:
        raise ClientError("Missing observed overlay addresses")
    normalized = []
    for text in addresses:
        address = ipaddress.ip_address(validate_network_ip(text))
        if address not in ipaddress.ip_network("100.64.0.0/10" if address.version == 4 else "fd7a:115c:a1e0::/48"):
            raise ClientError("Client address is outside the overlay")
        normalized.append(str(address))
    if len(set(normalized)) != len(normalized):
        raise ClientError("Duplicate observed overlay addresses")
    tags = peer.get("Tags")
    if tags is None:
        tags = []
    if (not isinstance(tags, list) or len(tags) > 64
            or any(not isinstance(t, str) or not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]{0,62}", t) for t in tags)):
        raise ClientError("Invalid observed service tags")
    if require_running and (status.get("BackendState") != "Running" or not online):
        raise ClientError("Client is not running and online")
    return ClientIdentity(node, tuple(normalized), url, tuple(tags), online, hostname)


def observe_client(expected_url: str, *, require_running: bool = True) -> ClientIdentity:
    """Observe native typed identity without modifying the client."""
    status, prefs = snapshot()
    try:
        return _identity(status, prefs, expected_url, require_running)
    except ClientStarting:
        raise
    except (ValueError, TypeError):
        raise ClientError("Invalid observed native identity") from None


def read_binding() -> dict | None:
    check_directory_path(os.path.dirname(CLIENT_STATE))
    try:
        info = os.lstat(CLIENT_STATE)
    except FileNotFoundError:
        return None
    with directory_fd(os.path.dirname(CLIENT_STATE)) as fd:
        parent = os.fstat(fd)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise ClientError("Unsafe client ownership directory")
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise ClientError("Unsafe client ownership record")
    value = read_json_file(CLIENT_STATE, max_bytes=8192)
    if (not isinstance(value, dict) or set(value) not in (
                {"version", "url", "phase", "node_id"},
                {"version", "url", "phase", "node_id", "recovery"})
            or type(value["version"]) is not int or value["version"] not in {1, 2}
            or (value["version"] == 2) != ("recovery" in value)
            or not isinstance(value["phase"], str)
            or value["phase"] not in {"prepared", "joining", "enrolled", "left", "resuming", "leaving"}
            or not isinstance(value["node_id"], str)
            or (value["phase"] in {"enrolled", "resuming", "leaving"} and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", value["node_id"]))
            or (value["phase"] in {"prepared", "joining", "left"} and value["node_id"])):
        raise ClientError("Invalid client ownership record; preserve for recovery")
    validate_headscale_url(value["url"])
    if value["version"] == 2:
        recovery = value["recovery"]
        field = {"joining": "prior_url", "leaving": "destination_url"}.get(value["phase"])
        if field is None or not isinstance(recovery, dict) or set(recovery) != {field}:
            raise ClientError("Invalid client recovery evidence")
        if field == "prior_url":
            _reviewed_endpoint(recovery[field])
        else:
            validate_headscale_url(recovery[field])
    elif value["phase"] == "leaving":
        raise ClientError("Missing client recovery intent")
    return value


def save_binding(url: str, phase: str, node_id: str = "", *, recovery: dict | None = None) -> None:
    with directory_fd(os.path.dirname(CLIENT_STATE), create=True, mode=0o700) as fd:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ClientError("Unsafe client ownership directory")
        value = {"version": 1, "url": validate_headscale_url(url), "phase": phase, "node_id": node_id}
        if recovery is not None:
            value.update(version=2, recovery=recovery)
        write_json_atomic(CLIENT_STATE, value)


def _reviewed_endpoint(value: object) -> object:
    """Retain only a validated endpoint, including native bootstrap defaults."""
    if value not in (None, ""):
        validate_headscale_url(value)
    return value


def _require_logged_out(status: dict) -> None:
    """Require absence of both a native node key and an observed identity."""
    if (status.get("BackendState") != "NeedsLogin"
            or status.get("HaveNodeKey", False) is not False
            or status.get("Self") not in (None, {})):
        raise ClientError("Native logout unconfirmed; retain recovery intent")


def _finish_leave(binding: dict) -> None:
    require_private_daemon()
    status, prefs = snapshot()
    _require_logged_out(status)
    _network(prefs, binding["url"])
    save_binding(binding["recovery"]["destination_url"], "left")


def reject_control_host(url: str) -> None:
    """Inspect actual native control configuration, even if setup omits hosting."""
    if os.path.lexists(CONTROL_CONFIG):
        check_directory_path(os.path.dirname(CONTROL_CONFIG))
        descriptor = os.open(CONTROL_CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022 or info.st_size > 1024 * 1024):
                raise ClientError("Cannot establish local control-server identity")
            text = stream.read(1024 * 1024 + 1)
        if len(text.encode()) > 1024 * 1024:
            raise ClientError("Cannot establish local control-server identity")
        try:
            if text.lstrip().startswith("{"):
                native_config = parse_json(text)
                control = native_config.get("server_url") if isinstance(native_config, dict) else None
            else:
                # Fail closed for unfamiliar YAML rather than assuming this is a peer.
                matches = re.findall(r'^server_url:\s*[\"\']?(https://[^\s\"\']+)[\"\']?\s*$', text, re.M)
                control = matches[0] if len(matches) == 1 else None
            same_network = validate_headscale_url(control) == validate_headscale_url(url)
        except (ValueError, TypeError):
            raise ClientError("Cannot establish local control-server identity") from None
        if same_network:
            raise ClientError("Local control host cannot join this network")


def owned(url: str) -> dict:
    binding = read_binding()
    if binding is None:
        raise ClientError("Unowned client; explicit reviewed adoption required")
    if binding["url"] != validate_headscale_url(url):
        raise ClientError("Persisted network differs; explicit migration required")
    return binding


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ClientError("Client lifecycle mutations require root")


@contextmanager
def client_lock():
    _require_root()
    with resource_lock("tailscale-client", CLIENT_STATE):
        yield


def check_native_state() -> None:
    """Require a private, owned retained identity without reading its secrets."""
    with directory_fd(os.path.dirname(NATIVE_STATE)) as fd:
        parent = os.fstat(fd)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise ClientError("Unsafe retained native identity directory")
        info = os.stat(os.path.basename(NATIVE_STATE), dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ClientError("Unsafe retained native client identity")


def require_private_daemon() -> None:
    """Reuse the adapter's effective manager AND running process privacy proof."""
    from common.service_tools.headscale_client_remote import _privacy

    try:
        check_native_state()
        _privacy()
    except (OSError, ValueError):
        raise ClientError("Running private native daemon required") from None


def check_privacy_dropin(path: str = PRIVACY_DROPIN) -> None:
    with directory_fd(os.path.dirname(path)) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ClientError("Unsafe native privacy directory")
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022
                    or stream.read(4096) != PRIVACY_CONTENT):
                raise ClientError("Reviewed native privacy drop-in required")


def _enable_retained_profile() -> None:
    """Set only WantRunning on the private local daemon, preserving its prefs.

    Bare `tailscale up` requires a nonempty native login name for its simple
    resume path; tagged Headscale profiles do not always have one. EditPrefs
    with an explicit mask avoids reauthentication and unrelated flag defaults.
    The response can contain private preferences and is deliberately discarded.
    """
    require_private_daemon()
    check_directory_path(os.path.dirname(NATIVE_SOCKET))
    info = os.lstat(NATIVE_SOCKET)
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1):
        raise ClientError("Owned native daemon socket required")
    native(["curl", "--disable", "--silent", "--show-error", "--fail",
            "--noproxy", "*", "--unix-socket", NATIVE_SOCKET,
            "--request", "PATCH", "--header", "Content-Type: application/json",
            "--data", '{"WantRunning":true,"WantRunningSet":true}',
            "--output", "/dev/null", "--max-time", "10",
            "http://local-tailscaled.sock/localapi/v0/prefs"], timeout=15)


def _resume_client(url: str, expected_node_id: str, *, dropin: str = PRIVACY_DROPIN) -> ClientIdentity:
    """Locked implementation; retain uncertainty before any service mutation."""
    binding = owned(url)
    if binding["phase"] != "enrolled" or binding["node_id"] != expected_node_id:
        raise ClientError("Resume requires reviewed enrolled node binding")
    reject_control_host(url)
    check_native_state()
    check_privacy_dropin(dropin)
    version(_object(["tailscale", "version", "--json"]).get("short"))
    active = native(["systemctl", "show", "--property=ActiveState", "--value", "tailscaled"]).strip()
    if active == "active":
        require_private_daemon()
        if observe_client(url, require_running=False).node_id != expected_node_id:
            raise ClientError("Live identity differs from reviewed binding")
    elif active not in {"inactive", "failed"}:
        raise ClientError("Native service state uncertain; review before resume")
    # Hold the teardown guard before starting: failure must be safe to quiesce.
    with resolver_lifecycle_guard():
        save_binding(url, "resuming", expected_node_id)
        try:
            native(["systemctl", "enable", "--now", "tailscaled"])
            status, prefs = wait_for_daemon()
            identity = _identity(status, prefs, url, require_running=False)
            if identity.node_id != expected_node_id or identity.login_server != validate_headscale_url(url):
                raise ClientError("Resumed node differs from reviewed binding")
            if status["BackendState"] == "Stopped":
                _enable_retained_profile()
            deadline = time.monotonic() + 15
            while True:
                require_private_daemon()
                try:
                    identity = observe_client(url, require_running=False)
                    if identity.node_id != expected_node_id or identity.login_server != validate_headscale_url(url):
                        raise ClientError("Resumed node differs from reviewed binding")
                    if identity.online:
                        identity = observe_client(url)
                        if identity.node_id != expected_node_id or identity.login_server != validate_headscale_url(url):
                            raise ClientError("Resumed node differs from reviewed binding")
                        break
                except ClientStarting:
                    pass
                if time.monotonic() >= deadline:
                    raise ClientError("Resumed client did not become online; reconcile identity")
                time.sleep(0.25)
            save_binding(url, "enrolled", expected_node_id)
            return identity
        except BaseException:
            # Even a lost start acknowledgement can mean the daemon is live.
            # Never clear the marker, including when quiescence itself fails.
            try:
                save_binding(url, "resuming", expected_node_id)
            finally:
                native(["systemctl", "disable", "--now", "tailscaled"])
                state = native(["systemctl", "show", "--property=ActiveState", "--value", "tailscaled"])
                if state.strip() not in {"inactive", "failed"}:
                    raise ClientError("Resume uncertain and quiescence unverified; retain binding") from None
            raise


def resume_client(url: str, *, expected_node_id: str) -> ClientIdentity:
    """Resume only an exact reviewed managed enrollment, without credentials."""
    with client_lock():
        return _resume_client(url, expected_node_id)


def _reconcile(url: str) -> ClientIdentity:
    binding = owned(url)
    if binding["phase"] == "leaving":
        raise ClientError("Logout intent retained; explicitly confirm logged-out reconciliation")
    require_private_daemon()
    identity = observe_client(url, require_running=False)
    if binding["node_id"] and binding["node_id"] != identity.node_id:
        raise ClientError("Client node identity changed; review before adoption")
    save_binding(url, "enrolled", identity.node_id)
    return identity


def reconcile(url: str, *, confirm_logged_out: bool = False) -> ClientIdentity | None:
    """Serialize lost-ack reconciliation with enrollment and teardown."""
    with client_lock():
        if confirm_logged_out is True:
            with resolver_lifecycle_guard():
                binding = owned(url)
                if binding["phase"] != "leaving":
                    raise ClientError("No reviewed logout intent to reconcile")
                _finish_leave(binding)
                return None
        return _reconcile(url)


def adopt_client(url: str, *, expected_node_id: str) -> ClientIdentity:
    """Explicit adoption after caller shows the observed node for review."""
    with client_lock():
        if read_binding() is not None:
            raise ClientError("Client already managed")
        reject_control_host(validate_headscale_url(url))
        require_private_daemon()
        identity = observe_client(url)
        if identity.node_id != expected_node_id:
            raise ClientError("Adoption identity changed")
        save_binding(url, "enrolled", identity.node_id)
        return identity


def enroll_client(url: str, *, key_file: str | None = None,
                  expected_tags: tuple[str, ...] = (), interactive: bool = False) -> ClientIdentity:
    """Explicit enrollment; interactive output goes only to a caller-owned TTY.

    The caller must provide an authorized private terminal, never a setup log.
    A joining marker survives cancellation and prevents machine-key replay.
    """
    with client_lock():
        url = validate_headscale_url(url)
        if (not isinstance(expected_tags, (tuple, list)) or len(expected_tags) > 64
                or any(not isinstance(t, str) or not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]{0,62}", t) for t in expected_tags)):
            raise ClientError("Invalid approved service tags")
        reject_control_host(url)
        binding = owned(url)
        if binding["phase"] == "leaving":
            raise ClientError("Reconcile retained logout intent before enrollment")
        require_private_daemon()
        status, prefs = snapshot()
        check_network(status, prefs, url, binding["phase"])
        if status["BackendState"] == "Running":
            identity = _reconcile(url)
            if set(identity.tags) != set(expected_tags):
                raise ClientError("Observed service tags differ; administrative review required")
            return identity
        if binding["phase"] not in {"prepared", "left"}:
            raise ClientError("Enrollment already issued; reconcile or explicitly leave before retry")
        _require_logged_out(status)
        recovery = {"prior_url": _reviewed_endpoint(prefs.get("ControlURL"))}
        args = ["tailscale", "up", "--login-server=" + url, "--accept-dns=true", "--timeout=60s"]
        if key_file is None:
            if not interactive or expected_tags or not os.isatty(1) or not os.isatty(2):
                raise ClientError("Personal enrollment requires an authorized private terminal")
            save_binding(url, "joining", recovery=recovery)
            try:
                result = subprocess.run(args, check=False, timeout=65)
                if result.returncode:
                    raise ClientError("Enrollment pending; reconcile before retry")
            except subprocess.SubprocessError:
                raise ClientError("Enrollment interrupted; reconcile before retry") from None
        else:
            if interactive or not expected_tags or any(not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]{0,62}", t) for t in expected_tags):
                raise ClientError("Machine enrollment requires explicit approved service tags")
            validate_filesystem_path(key_file, must_exist=True)
            check_directory_path(os.path.dirname(key_file))
            with directory_fd(os.path.dirname(os.path.abspath(key_file))) as parent:
                parent_info = os.fstat(parent)
                if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o077:
                    raise ClientError("Key directory must be private and owned")
                fd = os.open(key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_mode & 0o077 or info.st_nlink != 1 or not 1 <= info.st_size <= 4096):
                        raise ClientError("Unsafe transient enrollment key file")
                    save_binding(url, "joining", recovery=recovery)
                    try:
                        native(args + ["--auth-key=file:" + key_file], timeout=65)
                    finally:
                        current = os.lstat(key_file)
                        if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                            os.unlink(key_file)
                finally:
                    os.close(fd)
        identity = _reconcile(url)
        if set(identity.tags) != set(expected_tags):
            raise ClientError("Observed service tags differ; administrative review required")
        if not identity.online:
            raise ClientError("Enrollment is not online; reconcile before retry")
        return identity


def disable_client(url: str) -> None:
    with client_lock(), resolver_lifecycle_guard():
        binding = owned(url)
        # A stopped daemon cannot answer prefs; the persisted binding still
        # protects setup selection. A running daemon must also match.
        active = native(["systemctl", "show", "--property=ActiveState", "--value", "tailscaled"])
        if active.strip() == "active":
            status, prefs = snapshot()
            check_network(status, prefs, url, binding["phase"])
            if status["BackendState"] in {"Running", "Stopped"}:
                _reconcile(url)
        native(["systemctl", "disable", "--now", "tailscaled"])
        state = native(["systemctl", "show", "tailscaled", "--property=ActiveState,UnitFileState"])
        fields = dict(line.split("=", 1) for line in state.splitlines() if "=" in line)
        if (fields.get("ActiveState") not in {"inactive", "failed"}
                or fields.get("UnitFileState") not in {"disabled", "masked", "static", "indirect"}):
            raise ClientError("Client disable was not verified; reconcile service state")


def leave_client(url: str, *, expected_node_id: str) -> None:
    """Reviewable leave: exact currently observed node, no data deletion."""
    with client_lock(), resolver_lifecycle_guard():
        identity = _reconcile(url)
        if identity.node_id != expected_node_id:
            raise ClientError("Leave identity changed; review again")
        save_binding(url, "leaving", expected_node_id, recovery={"destination_url": validate_headscale_url(url)})
        native(["tailscale", "logout"])
        _finish_leave(owned(url))


def migrate_client(url: str, new_url: str, *, expected_node_id: str) -> None:
    """Reviewed leave and prepare only; a new explicit enrollment is required."""
    new_url = validate_headscale_url(new_url)
    if validate_headscale_url(url) == new_url:
        raise ClientError("Migration requires a different network")
    reject_control_host(new_url)
    with client_lock(), resolver_lifecycle_guard():
        identity = _reconcile(url)
        if identity.node_id != expected_node_id:
            raise ClientError("Migration identity changed; review again")
        save_binding(url, "leaving", expected_node_id, recovery={"destination_url": new_url})
        native(["tailscale", "logout"])
        _finish_leave(owned(url))


def abandon_enrollment(url: str, *, confirm: bool = False) -> None:
    """Explicitly discard an inspected pending attempt after remote review.

    Caller must first revoke/reconcile the issued key or pending request with
    the administrator. This never substitutes for revoking a registered node.
    """
    if confirm is not True:
        raise ClientError("Pending enrollment abandonment requires explicit confirmation")
    with client_lock(), resolver_lifecycle_guard():
        binding = owned(url)
        require_private_daemon()
        status, prefs = snapshot()
        if binding["phase"] != "joining" or binding["node_id"]:
            raise ClientError("Attempt is not pending; reconcile observed identity")
        _require_logged_out(status)
        actual = _reviewed_endpoint(prefs.get("ControlURL"))
        allowed = [validate_headscale_url(url)]
        if "recovery" in binding:
            allowed.append(binding["recovery"]["prior_url"])
        if actual not in allowed:
            raise ClientError("Pending endpoint differs from reviewed enrollment prestate")
        save_binding(url, "left")
