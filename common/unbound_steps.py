"""Transactional distro Unbound setup on a separately enrolled Debian DNS node."""

from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tempfile
from urllib.parse import urlsplit

from lib.atomic_io import parse_json, remove_file_durable, write_bytes_atomic, write_json_atomic
from lib.config import SetupConfig
from lib.headscale_config import validate_headscale_settings
from lib.headscale_dns_binding import DNSBinding, dns_binding_lease, parse_dns_binding
from lib.machine_state import can_manage_firewall, can_manage_system_services
from lib.maintenance_defaults import APT_LOCK_OPTIONS
from lib.operation_state import OperationStateError, OperationStateStore
from lib.remote_utils import get_os_id, is_dry_run, is_package_installed, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.state_read import StateReadError
from lib.unbound_config import (
    HEALTH_NAME, LocalRecord, health_identity, parse_local_records, records_document, render_unbound,
)
from lib.unit_transaction import inspect_unit_state
from lib.validation import validate_filesystem_path

SERVICE = "unbound.service"
FIREWALL_PREFIX = "basaltwater unbound "
PACKAGES = ("unbound", "dns-root-data", "bind9-dnsutils")
MAX_FILE = 256 * 1024


@dataclass(frozen=True)
class UnboundPaths:
    config_dir: str = "/etc/unbound"
    state_dir: str = "/var/lib/basaltwater/unbound"
    defaults: str = "/etc/default/unbound"
    dropin: str = "/etc/systemd/system/unbound.service.d/basaltwater-overlay.conf"

    @property
    def main(self) -> str:
        return self.config_dir + "/unbound.conf"

    @property
    def fragments(self) -> str:
        return self.config_dir + "/unbound.conf.d"

    @property
    def fragment(self) -> str:
        return self.fragments + "/zz-basaltwater-overlay.conf"

    @property
    def manifest(self) -> str:
        return self.state_dir + "/managed.json"

    @property
    def binding(self) -> str:
        return self.state_dir + "/binding.json"

    @property
    def records(self) -> str:
        return self.state_dir + "/records.json"

    @property
    def operation(self) -> str:
        return self.state_dir + "/operation.json"

    @property
    def backup(self) -> str:
        return self.state_dir + "/recovery.json"


def _read(path: str, *, private: bool = False) -> bytes | None:
    validate_filesystem_path(path)
    check_directory_path(os.path.dirname(path))
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & (0o077 if private else 0o022)):
            raise ValueError("Unsafe resolver file: " + path)
        content = stream.read(MAX_FILE + 1)
    if len(content) > MAX_FILE:
        raise ValueError("Resolver file exceeds size limit: " + path)
    return content


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _files(paths: UnboundPaths) -> tuple[str, ...]:
    return paths.fragment, paths.defaults, paths.dropin, paths.records


def _manifest(paths: UnboundPaths) -> dict | None:
    raw = _read(paths.manifest, private=True)
    if raw is None:
        return None
    try:
        value = parse_json(raw)
        if (not isinstance(value, dict) or set(value) != {"version", "enabled", "binding", "files"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["enabled"]) is not bool or not isinstance(value["files"], dict)
                or set(value["files"]) != set(_files(paths))):
            raise ValueError("Invalid managed resolver schema")
        parse_dns_binding(value["binding"])
        for path, expected in value["files"].items():
            current = _read(path, private=path == paths.records)
            if current is None or not isinstance(expected, str) or _hash(current) != expected:
                raise ValueError("Managed resolver configuration changed outside its transaction")
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise StateReadError(paths.manifest, "invalid or changed managed resolver installation") from exc


def _lines(content: bytes) -> list[list[str]]:
    result = []
    for line in content.decode("utf-8").splitlines():
        fields = shlex.split(line, comments=True)
        if fields:
            result.append(fields)
    return result


def _stock_fragments(paths: UnboundPaths, *, managed: bool) -> list[str]:
    """Keep packaged includes intact; existing custom resolvers need adoption."""
    main = _read(paths.main)
    if main is None or _lines(main) != [["include-toplevel:", paths.fragments + "/*.conf"]]:
        raise ValueError("Unmanaged Unbound main configuration; explicit resolver adoption is required")
    expected = {
        "root-auto-trust-anchor-file.conf": [["server:"], ["auto-trust-anchor-file:", "/var/lib/unbound/root.key"]],
        "remote-control.conf": [["remote-control:"], ["control-enable:", "yes"],
                                ["control-interface:", "/run/unbound.ctl"]],
    }
    observed = set()
    for path in sorted(Path(paths.fragments).glob("*.conf")):
        if str(path) == paths.fragment and managed:
            continue
        if path.name not in expected or _lines(_read(str(path)) or b"") != expected[path.name]:
            raise ValueError("Existing Unbound fragments require explicit adoption; files were preserved")
        observed.add(path.name)
    if observed != set(expected):
        raise ValueError("Packaged Unbound control/trust-anchor configuration is missing")
    return [paths.fragments + "/" + name for name in sorted(expected)]


def _preflight(config: SetupConfig) -> None:
    validate_headscale_settings(config, complete=True)
    if (os.geteuid() != 0 or get_os_id() != "debian" or shutil.which("apt-get") is None
            or not can_manage_system_services(config.machine_type)
            or not can_manage_firewall(config.machine_type)):
        raise ValueError("Managed Unbound requires Debian root, APT, systemd and private firewall capability")


def _observe(network_url: str) -> DNSBinding:
    # Lazy import keeps parsing/rendering usable before client integration lands.
    from lib.headscale_client import observe_client

    identity = observe_client(network_url)
    binding = parse_dns_binding(DNSBinding(identity.login_server, identity.node_id, identity.addresses).to_dict())
    result = run(["ip", "-j", "address", "show", "dev", "tailscale0"], capture_output=True)
    if not isinstance(result.stdout, str) or len(result.stdout) > 64 * 1024:
        raise ValueError("Could not inspect the enrolled overlay interface")
    try:
        interfaces = parse_json(result.stdout)
        addresses = {str(ipaddress.ip_address(address["local"]))
                     for interface in interfaces for address in interface["addr_info"]}
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid overlay interface observation") from exc
    if not set(binding.addresses) <= addresses:
        raise ValueError("Observed client addresses are not assigned to the overlay interface")
    return binding


def _same_identity(left: DNSBinding, right: DNSBinding) -> bool:
    return (left.network_url, left.node_id, set(left.addresses)) == (right.network_url, right.node_id, set(right.addresses))


def _unit_configuration(paths: UnboundPaths, *, managed: bool) -> None:
    result = run(["systemctl", "show", SERVICE, "--property=FragmentPath,DropInPaths", "--no-pager"], capture_output=True)
    fields = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    if fields.get("FragmentPath") not in {"/usr/lib/systemd/system/unbound.service", "/lib/systemd/system/unbound.service"}:
        raise ValueError("Unbound requires the packaged native unit; custom unit adoption is separate")
    if set(shlex.split(fields.get("DropInPaths", ""))) != ({paths.dropin} if managed else set()):
        raise ValueError("Unmanaged Unbound unit overrides require explicit adoption")


def _ports(binding: DNSBinding, *, managed: bool) -> None:
    result = run(["ss", "-H", "-lntup", "( sport = :53 )"], capture_output=True)
    if not isinstance(result.stdout, str) or len(result.stdout) > 64 * 1024:
        raise ValueError("Could not inspect DNS listener ownership")
    pid = None
    if managed:
        observed = run(["systemctl", "show", SERVICE, "--property=MainPID", "--value"], capture_output=True)
        text = (observed.stdout or "").strip()
        if not text.isdigit():
            raise ValueError("Could not inspect managed DNS process identity")
        pid = int(text)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[0] not in {"tcp", "udp"}:
            raise ValueError("Unrecognized DNS listener observation")
        address = fields[4].rsplit(":", 1)[0].strip("[]").split("%", 1)[0]
        if address not in {"*", "0.0.0.0", "::", *binding.addresses}:
            continue
        owners = {int(value) for value in re.findall(r"\bpid=(\d+)", line)}
        if not managed or pid == 0 or owners != {pid} or '"unbound"' not in line:
            raise ValueError("DNS port conflicts with a listener not owned by the managed Unbound unit")


def _rules() -> list[tuple[int, str]]:
    result = run(["ufw", "status", "numbered"], capture_output=True)
    if not isinstance(result.stdout, str) or not result.stdout.startswith("Status: active\n"):
        raise ValueError("Private Unbound serving requires an active UFW firewall")
    return [(int(match.group(1)), line.split("#", 1)[1].strip() if "#" in line else "")
            for line in result.stdout.splitlines()
            if (match := re.match(r"^\[\s*(\d+)\]", line.strip()))]


def _firewall(binding: DNSBinding | None) -> None:
    current = _rules()
    desired = {}
    for address in (() if binding is None else binding.addresses):
        source = "100.64.0.0/10" if ipaddress.ip_address(address).version == 4 else "fd7a:115c:a1e0::/48"
        for protocol in ("tcp", "udp"):
            comment = FIREWALL_PREFIX + protocol + " " + address
            desired[comment] = ["ufw", "allow", "in", "on", "tailscale0", "proto", protocol,
                                "from", source, "to", address, "port", "53", "comment", comment]
    comments = {comment for _, comment in current}
    for comment, command in desired.items():
        if comment not in comments:
            run(command, capture_output=True)
    current = _rules()
    if not set(desired) <= {comment for _, comment in current}:
        raise RuntimeError("UFW did not retain the private DNS rules")
    for number, comment in sorted(current, reverse=True):
        if comment.startswith(FIREWALL_PREFIX) and comment not in desired:
            run(["ufw", "--force", "delete", str(number)], capture_output=True)


def _private_directory(path: str) -> None:
    with directory_fd(path, create=True, mode=0o700) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("Unsafe resolver state directory")
        os.fchmod(descriptor, 0o700)


def _write(path: str, content: bytes, mode: int) -> None:
    parent = os.path.dirname(path)
    with directory_fd(parent, create=True):
        write_bytes_atomic(path, content, mode=mode)


def _install() -> None:
    missing = [package for package in PACKAGES if not is_package_installed(package)]
    if not missing:
        return
    # Only a fresh, unowned installation reaches this path. Prevent postinst
    # from starting a default listener before private configuration is ready.
    run(["systemctl", "mask", "--runtime", SERVICE], capture_output=True)
    try:
        environment = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        run(["apt-get", "install", "-y", "-qq", *APT_LOCK_OPTIONS, *missing], env=environment)
        if any(not is_package_installed(package) for package in PACKAGES):
            raise RuntimeError("Unbound package installation was not verified")
    finally:
        run(["systemctl", "unmask", "--runtime", SERVICE], capture_output=True)


def verify_resolver(binding: DNSBinding, *, recursion: bool = True) -> None:
    """Can also be run from an enrolled peer before network DNS publication."""
    binding = parse_dns_binding(binding.to_dict())
    for address in binding.addresses:
        for protocol in (["+notcp", "+ignore"], ["+tcp"]):
            result = run(["dig", "@" + address, HEALTH_NAME, "TXT", "+short", "+time=3", "+tries=1", *protocol],
                         capture_output=True, timeout=15)
            if (result.stdout or "").strip() != '"' + health_identity(binding) + '"':
                raise RuntimeError("DNS health response does not match the observed node/network binding")
        if recursion:
            for protocol in (["+notcp", "+ignore"], ["+tcp"]):
                for name, rrtype, status, extra in (
                    (".", "DNSKEY", "NOERROR", []),
                    ("dnssec-failed.org.", "A", "SERVFAIL", []),
                    ("dnssec-failed.org.", "A", "NOERROR", ["+cdflag"]),
                ):
                    result = run(["dig", "@" + address, name, rrtype, "+dnssec", "+noall",
                                  "+comments", "+answer", "+time=5", "+tries=1", *protocol, *extra],
                                 capture_output=True, timeout=20)
                    output = result.stdout or ""
                    flags = re.search(r";; flags: ([^;]*);", output)
                    if ("status: " + status not in output or flags is None
                            or (name == "." and "ad" not in flags.group(1).split())
                            or (status == "NOERROR" and not re.search(r"(?m)^[^;\s].*\sIN\s", output))):
                        raise RuntimeError("Recursive valid/bogus DNSSEC verification failed")


def _candidate(paths: UnboundPaths, content: bytes, stocks: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix=".candidate-", dir=paths.state_dir) as temporary:
        fragment, main = temporary + "/resolver.conf", temporary + "/unbound.conf"
        write_bytes_atomic(fragment, content)
        includes = [*stocks, fragment]
        write_bytes_atomic(main, "".join("include-toplevel: " + json.dumps(path) + "\n" for path in includes).encode())
        run(["unbound-checkconf", main], capture_output=True)


def _snapshot(paths: UnboundPaths, state: dict, binding: DNSBinding | None) -> dict:
    files = {}
    for path in (*_files(paths), paths.manifest):
        raw = _read(path, private=path in {paths.records, paths.manifest})
        files[path] = None if raw is None else {
            "content": base64.b64encode(raw).decode("ascii"), "mode": stat.S_IMODE(os.stat(path).st_mode),
        }
    return {"version": 1, "files": files, "state": state,
            "binding": None if binding is None else binding.to_dict()}


def _restore(snapshot: dict) -> None:
    for path, previous in snapshot["files"].items():
        if previous is None:
            remove_file_durable(path)
        else:
            _write(path, base64.b64decode(previous["content"], validate=True), previous["mode"])
    run(["systemctl", "daemon-reload"], capture_output=True)
    state = snapshot["state"]
    enabled = state["UnitFileState"] in {"enabled", "enabled-runtime"}
    flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
    run(["systemctl", "enable" if enabled else "disable", *flags, SERVICE], capture_output=True)
    if state["ActiveState"] == "active":
        run(["systemctl", "restart", SERVICE], capture_output=True)
    current = inspect_unit_state(SERVICE)
    if ((current["ActiveState"] == "active") != (state["ActiveState"] == "active")
            or (current["UnitFileState"] in {"enabled", "enabled-runtime"}) != enabled):
        raise RuntimeError("Prior resolver unit state was not restored")


def configure_unbound(config: SetupConfig, *, paths: UnboundPaths = UnboundPaths(),
                      records: tuple[LocalRecord, ...] | None = None,
                      expected_binding: DNSBinding | None = None) -> None:
    """Preserve working DNS on errors; explicit rebind requires an inspected identity.

    Package installation and trust-anchor updates remain outside file rollback.
    Interrupted or failed rollback retains a blocking operation and private backup.
    """
    if config.unbound is None:
        return
    validate_headscale_settings(config, complete=True)
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would reconcile private Unbound after observing enrolled node identity; DNS publication is separate")
        return
    _preflight(config)
    for path in (paths.config_dir, paths.state_dir, os.path.dirname(paths.defaults), os.path.dirname(paths.dropin)):
        validate_filesystem_path(path)
        check_directory_path(path)
    with dns_binding_lease(paths.binding) as lease:
        previous_binding = lease.binding
        if not config.unbound and previous_binding is not None and previous_binding.advertised:
            raise ValueError("Withdraw or replace the advertised DNS resolver before disabling Unbound")
        managed = _manifest(paths)
        if managed is None and not config.unbound:
            return
        if managed is None:
            if (is_package_installed("unbound") or _read(paths.main) is not None
                    or any(_read(path) is not None for path in _files(paths)) or previous_binding is not None):
                raise ValueError("Existing Unbound installation requires explicit adoption or recovery")
        elif previous_binding is None or not _same_identity(parse_dns_binding(managed["binding"]), previous_binding):
            raise ValueError("Managed resolver and private node binding disagree; reconcile recovery state")
        binding = _observe(config.tailscale_login_server) if config.unbound else previous_binding
        if binding is None:
            raise ValueError("Managed resolver has no observed network binding")
        if previous_binding is not None:
            if not _same_identity(previous_binding, binding):
                if previous_binding.advertised or expected_binding != previous_binding:
                    raise ValueError("DNS node identity or addresses changed; withdraw and explicitly reconcile before rebind")
            binding = replace(binding, advertised=previous_binding.advertised, publication_id=previous_binding.publication_id)
        _ports(binding, managed=managed is not None)
        _rules()
        if any(":" in address for address in binding.addresses):
            defaults = _read("/etc/default/ufw") or b""
            if not re.search(rb"(?m)^IPV6=yes\s*$", defaults):
                raise ValueError("IPv6 overlay DNS requires IPV6=yes in UFW")
        forbidden = tuple(value for value in (urlsplit(binding.network_url).hostname, config.headscale_domain, config.headscale_base_domain,
                                               config.headscale_onboarding_domain) if value)
        if records is None:
            raw = _read(paths.records, private=True)
            records = () if raw is None else parse_local_records(parse_json(raw), forbidden_domains=forbidden)
        records = parse_local_records(records_document(records), forbidden_domains=forbidden)
        if records and not config.headscale_base_domain:
            raise ValueError("Local records require the intended MagicDNS base domain to check zone collisions")
        content = render_unbound(binding, records, forbidden_domains=forbidden).encode()
        state = inspect_unit_state(SERVICE)
        if managed is None and state["LoadState"] != "not-found":
            raise ValueError("An existing Unbound unit requires explicit adoption")
        if managed is not None:
            _unit_configuration(paths, managed=True)
        _private_directory(paths.state_dir)
        store = OperationStateStore(paths.operation)
        with store.ownership():
            if store.load() is not None or _read(paths.backup, private=True) is not None:
                raise OperationStateError("Unfinished Unbound transaction; preserve and inspect operation and recovery files")
            snapshot = _snapshot(paths, state, previous_binding)
            write_json_atomic(paths.backup, snapshot)
            operation = store.begin("unbound-configuration", paths.fragment, "prepared", context={"backup": paths.backup})
            mutated = False
            try:
                if config.unbound:
                    store.transition(operation.operation_id, "installing")
                    if managed is None:
                        mutated = True
                        _write(paths.defaults, b"# Managed by Basaltwater\nRESOLVCONF=false\nROOT_TRUST_ANCHOR_UPDATE=true\n", 0o644)
                        _install()
                        _unit_configuration(paths, managed=False)
                    stocks = _stock_fragments(paths, managed=managed is not None)
                    run(["/usr/libexec/unbound-helper", "root_trust_anchor_update"], capture_output=True)
                    _candidate(paths, content, stocks)
                    store.transition(operation.operation_id, "publishing")
                    mutated = True
                    _write(paths.defaults, b"# Managed by Basaltwater\nRESOLVCONF=false\nROOT_TRUST_ANCHOR_UPDATE=true\n", 0o644)
                    _write(paths.dropin, b"[Unit]\nAfter=network-online.target tailscaled.service\nWants=network-online.target tailscaled.service\n", 0o644)
                    _write(paths.fragment, content, 0o644)
                    write_json_atomic(paths.records, records_document(records))
                    _firewall(binding)
                    run(["unbound-checkconf", paths.main], capture_output=True)
                    run(["systemctl", "daemon-reload"], capture_output=True)
                    run(["systemctl", "enable", SERVICE], capture_output=True)
                    run(["systemctl", "restart", SERVICE], capture_output=True)
                    if inspect_unit_state(SERVICE)["ActiveState"] != "active":
                        raise RuntimeError("Unbound did not activate")
                    verify_resolver(binding)
                else:
                    store.transition(operation.operation_id, "disabling")
                    mutated = True
                    run(["systemctl", "disable", "--now", SERVICE], capture_output=True)
                    stopped = inspect_unit_state(SERVICE)
                    if stopped["ActiveState"] not in {"inactive", "failed"} or stopped["UnitFileState"] in {"enabled", "enabled-runtime"}:
                        raise RuntimeError("Unbound is still active or enabled")
                    _firewall(None)
                lease.save(binding)
                files = {path: _hash(_read(path, private=path == paths.records) or b"") for path in _files(paths)}
                write_json_atomic(paths.manifest, {"version": 1, "enabled": config.unbound,
                                                   "binding": binding.to_dict(), "files": files})
                store.complete(operation.operation_id, keep_ownership=True)
            except BaseException as original:
                try:
                    if mutated:
                        store.transition(operation.operation_id, "rolling-back")
                        run(["systemctl", "stop", SERVICE], capture_output=True)
                        if inspect_unit_state(SERVICE)["ActiveState"] not in {"inactive", "failed"}:
                            raise RuntimeError("Could not establish DNS quiescence; candidate files retained")
                        _restore(snapshot)
                        _firewall(previous_binding if state["ActiveState"] == "active" else None)
                        if previous_binding is None:
                            lease.clear()
                        else:
                            lease.save(previous_binding)
                        if state["ActiveState"] == "active" and previous_binding is not None:
                            verify_resolver(previous_binding)
                    store.complete(operation.operation_id, outcome="rolled_back" if mutated else "failed", keep_ownership=True)
                except BaseException as recovery:
                    store.transition(operation.operation_id, "rollback-failed", status="recovery_required")
                    raise RuntimeError("Unbound recovery required; preserve " + paths.operation + " and " + paths.backup) from recovery
                remove_file_durable(paths.backup)
                raise original
            remove_file_durable(paths.backup)
    print("  ✓ Managed private Unbound " + ("ready; verify from a peer before publishing DNS" if config.unbound else "disabled; state preserved"))
