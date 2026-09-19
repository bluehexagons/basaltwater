"""Persistent hostname and static network setup for Debian targets."""

from __future__ import annotations

import glob
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.machine_state import can_manage_mdns
from lib.remote_utils import install_package, is_dry_run, run
from lib.types import JSONDict, MaybeStr, StrList
from lib.validation import (
    validate_filesystem_path,
    validate_network_interface_name,
    validate_network_setup_settings,
    validate_system_hostname,
)


NETWORKD_CONFIG_PATH = "/etc/systemd/network/00-basaltwater-static.network"
IFUPDOWN_MAIN_CONFIG_PATH = "/etc/network/interfaces"
IFUPDOWN_CONFIG_DIR = "/etc/network/interfaces.d"
IFUPDOWN_CONFIG_PATH = "/etc/network/interfaces.d/basaltwater_static"
CLOUD_INIT_CONFIG_DIR = "/etc/cloud/cloud.cfg.d"
CLOUD_INIT_NETWORK_CONFIG_PATH = "/etc/cloud/cloud.cfg.d/99-basaltwater-network.cfg"
CLOUD_INIT_HOSTNAME_CONFIG_PATH = "/etc/cloud/cloud.cfg.d/99-basaltwater-hostname.cfg"
NETWORK_TRANSITION_PATH = "/run/basaltwater-network-transition.json"
NETWORK_TRANSITION_VERSION = 2
MDNS_PACKAGES = ("avahi-daemon", "libnss-mdns")
MDNS_SERVICE = "avahi-daemon"
POLICY_TABLE_RANGE = range(20000, 20100)
NETWORK_TRANSITION_STATES = {"prepared", "committed"}
NETWORKMANAGER_ROLLBACK_PROPERTIES = (
    "ipv4.method",
    "ipv4.addresses",
    "ipv4.gateway",
    "ipv4.never-default",
    "ipv4.dns",
    "ipv4.ignore-auto-dns",
    "ipv6.method",
    "ipv6.addresses",
    "ipv6.gateway",
    "ipv6.never-default",
    "ipv6.dns",
    "ipv6.ignore-auto-dns",
)


@dataclass(frozen=True)
class NetworkTransition:
    """Validated state for a reversible live network transition."""

    transition_id: str
    state: str
    config: SetupConfig
    interface: str
    backend: str
    added_addresses: list[str]
    policy_tables: dict[str, int]
    rollback: JSONDict
    payload: JSONDict


def configure_system_hostname(config: SetupConfig) -> None:
    """Persist and immediately apply the requested system hostname."""

    if not config.system_hostname:
        return

    hostname = validate_system_hostname(config.system_hostname)
    if config.dry_run or is_dry_run():
        print(f"  [DRY-RUN] Would set system hostname to {hostname}")
        return

    run(f"hostnamectl set-hostname {shlex.quote(hostname)}")
    if os.path.isdir(CLOUD_INIT_CONFIG_DIR):
        write_text_atomic(
            CLOUD_INIT_HOSTNAME_CONFIG_PATH,
            "# Managed by basaltwater\npreserve_hostname: true\n",
            mode=0o644,
        )
    print(f"  ✓ System hostname set to {hostname}")


def configure_mdns(config: SetupConfig) -> None:
    """Install and reconcile Avahi-based hostname discovery on the LAN."""

    if not (config.enable_mdns or config.clear_mdns):
        return

    if not can_manage_mdns():
        print(
            "  ✓ Skipping mDNS setup "
            "(OCI containers cannot run a target system service)"
        )
        return

    if config.clear_mdns:
        if config.dry_run or is_dry_run():
            print(f"  [DRY-RUN] Would disable {MDNS_SERVICE} mDNS service")
            return
        run(f"systemctl disable --now {MDNS_SERVICE}", check=False)
        print("  ✓ mDNS hostname advertising disabled")
        return

    if config.dry_run or is_dry_run():
        print(
            "  [DRY-RUN] Would install Avahi mDNS support and enable "
            f"{MDNS_SERVICE}"
        )
        return

    for package in MDNS_PACKAGES:
        if not install_package(
            f"{package} for mDNS hostname discovery",
            package,
            f"apt-get install -y -qq {shlex.quote(package)}",
        ):
            raise RuntimeError(f"mDNS package installation failed: {package}")

    run(f"systemctl enable --now {MDNS_SERVICE}")
    print("  ✓ mDNS hostname advertising enabled (use <hostname>.local on the LAN)")


def _interface_from_route_output(output: str) -> MaybeStr:
    for line in output.splitlines():
        fields = line.split()
        try:
            candidate = fields[fields.index("dev") + 1]
        except (ValueError, IndexError):
            continue
        candidate = candidate.split("@", 1)[0]
        if candidate != "lo":
            return validate_network_interface_name(candidate)
    return None


def _resolve_network_interface(configured: MaybeStr) -> str:
    if configured:
        return validate_network_interface_name(configured)

    for command in (
        "ip -o route show default",
        "ip -o -6 route show default",
        "ip -o link show up",
    ):
        result = run(command, check=False, capture_output=True)
        interface = _interface_from_route_output(result.stdout or "")
        if interface:
            return interface
    raise RuntimeError(
        "Could not detect a network interface; specify one with --network-interface"
    )


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _service_active(service: str) -> bool:
    result = run(
        f"systemctl is-active --quiet {shlex.quote(service)}",
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _detect_network_backend() -> str:
    if _command_available("nmcli") and _service_active("NetworkManager"):
        return "networkmanager"
    if _service_active("systemd-networkd"):
        return "networkd"
    if os.path.exists(IFUPDOWN_MAIN_CONFIG_PATH) or _command_available("ifup"):
        return "ifupdown"
    raise RuntimeError(
        "No supported active network backend found (NetworkManager, systemd-networkd, or ifupdown)"
    )


def _reject_managed_bridge(interface: str) -> None:
    """Refuse generic edits to a bridge whose topology must be preserved."""

    if os.path.isdir(f"/sys/class/net/{interface}/bridge"):
        raise RuntimeError(
            f"Refusing generic static network changes for bridge {interface}; "
            "Proxmox and other bridge hosts require a topology-aware network plan"
        )


def _networkmanager_connection(interface: str) -> str:
    result = run(
        "nmcli --escape no --get-values GENERAL.CONNECTION device show "
        f"{shlex.quote(interface)}",
        check=False,
        capture_output=True,
    )
    connection = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not connection or connection[0] in {"", "--"}:
        raise RuntimeError(f"NetworkManager has no active connection for {interface}")
    return connection[0]


def _configure_networkmanager(
    config: SetupConfig,
    interface: str,
    connection_name: MaybeStr = None,
) -> None:
    connection_name = connection_name or _networkmanager_connection(interface)
    quoted_connection = shlex.quote(connection_name)

    if config.static_ipv4:
        parts = [
            "nmcli connection modify",
            quoted_connection,
            "ipv4.method manual",
            f"ipv4.addresses {shlex.quote(config.static_ipv4)}",
            f"ipv4.gateway {shlex.quote(config.network_gateway4 or '')}",
            f"ipv4.never-default {'no' if config.network_gateway4 else 'yes'}",
        ]
        run(" ".join(parts))

    if config.static_ipv6:
        parts = [
            "nmcli connection modify",
            quoted_connection,
            "ipv6.method manual",
            f"ipv6.addresses {shlex.quote(config.static_ipv6)}",
            f"ipv6.gateway {shlex.quote(config.network_gateway6 or '')}",
            f"ipv6.never-default {'no' if config.network_gateway6 else 'yes'}",
        ]
        run(" ".join(parts))

    if config.network_dns is not None:
        ipv4_dns = [value for value in config.network_dns if ":" not in value]
        ipv6_dns = [value for value in config.network_dns if ":" in value]
        dns_parts = ["nmcli connection modify", quoted_connection]
        if ipv4_dns:
            dns_parts.extend(
                [
                    f"ipv4.dns {shlex.quote(','.join(ipv4_dns))}",
                    "ipv4.ignore-auto-dns yes",
                ]
            )
        if ipv6_dns:
            dns_parts.extend(
                [
                    f"ipv6.dns {shlex.quote(','.join(ipv6_dns))}",
                    "ipv6.ignore-auto-dns yes",
                ]
            )
        if len(dns_parts) > 2:
            run(" ".join(dns_parts))


def _render_networkd_config(config: SetupConfig, interface: str) -> str:
    lines = [
        "# Managed by basaltwater. Changes will be overwritten.",
        "[Match]",
        f"Name={interface}",
        "",
        "[Network]",
        "DHCP=ipv4" if not config.static_ipv4 else "DHCP=no",
        f"IPv6AcceptRA={'no' if config.static_ipv6 else 'yes'}",
    ]
    for address in (config.static_ipv4, config.static_ipv6):
        if address:
            lines.append(f"Address={address}")
    for gateway in (config.network_gateway4, config.network_gateway6):
        if gateway:
            lines.append(f"Gateway={gateway}")
    for dns_server in config.network_dns or []:
        lines.append(f"DNS={dns_server}")
    return "\n".join(lines) + "\n"


def _configure_networkd(config: SetupConfig, interface: str) -> None:
    write_text_atomic(
        NETWORKD_CONFIG_PATH,
        _render_networkd_config(config, interface),
        mode=0o644,
    )


def _strip_ifupdown_stanzas(content: str, interface: str) -> str:
    stanza_pattern = re.compile(
        rf"^\s*iface\s+{re.escape(interface)}\s+inet6?\s+\S+\s*(?:#.*)?$"
    )
    output: StrList = []
    skipping_stanza = False
    for line in content.splitlines(keepends=True):
        if skipping_stanza:
            if not line.strip() or line.startswith((" ", "\t")):
                continue
            skipping_stanza = False
        if stanza_pattern.match(line.rstrip("\r\n")):
            skipping_stanza = True
            continue
        output.append(line)
    return "".join(output)


def _write_with_backup(path: str, content: str) -> None:
    current_mode = 0o644
    if os.path.exists(path):
        current_mode = stat.S_IMODE(os.stat(path).st_mode)
        backup_path = f"{path}.basaltwater.bak"
        if not os.path.exists(backup_path):
            shutil.copy2(path, backup_path)
    write_text_atomic(path, content, mode=current_mode)


def _remove_existing_ifupdown_stanzas(interface: str) -> None:
    paths = [IFUPDOWN_MAIN_CONFIG_PATH]
    paths.extend(sorted(glob.glob(os.path.join(IFUPDOWN_CONFIG_DIR, "*"))))
    managed_path = os.path.abspath(IFUPDOWN_CONFIG_PATH)
    for path in paths:
        if (
            os.path.abspath(path) == managed_path
            or path.endswith(".basaltwater.bak")
            or not os.path.isfile(path)
        ):
            continue
        try:
            with open(path, "r", encoding="utf-8") as file_obj:
                original = file_obj.read()
        except OSError as exc:
            raise RuntimeError(f"Failed to read ifupdown config {path}: {exc}") from exc
        updated = _strip_ifupdown_stanzas(original, interface)
        if updated != original:
            _write_with_backup(path, updated)


def _ensure_ifupdown_include() -> None:
    source_line = f"source {IFUPDOWN_CONFIG_DIR}/*"
    if os.path.exists(IFUPDOWN_MAIN_CONFIG_PATH):
        with open(IFUPDOWN_MAIN_CONFIG_PATH, "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
    else:
        content = "auto lo\niface lo inet loopback\n"
    include_pattern = re.compile(
        rf"^\s*(?:source\s+{re.escape(IFUPDOWN_CONFIG_DIR)}/\*|"
        rf"source-directory\s+{re.escape(IFUPDOWN_CONFIG_DIR)})\s*$",
        re.MULTILINE,
    )
    if include_pattern.search(content):
        return
    updated = content.rstrip() + f"\n\n{source_line}\n"
    _write_with_backup(IFUPDOWN_MAIN_CONFIG_PATH, updated)


def _render_ifupdown_config(config: SetupConfig, interface: str) -> str:
    lines = [
        "# Managed by basaltwater. Changes will be overwritten.",
        f"auto {interface}",
    ]
    if config.static_ipv4:
        lines.extend(
            [
                f"iface {interface} inet static",
                f"    address {config.static_ipv4}",
            ]
        )
        if config.network_gateway4:
            lines.append(f"    gateway {config.network_gateway4}")
    else:
        lines.append(f"iface {interface} inet dhcp")

    if config.network_dns:
        lines.append(f"    dns-nameservers {' '.join(config.network_dns)}")

    if config.static_ipv6:
        lines.extend(
            [
                "",
                f"iface {interface} inet6 static",
                f"    address {config.static_ipv6}",
            ]
        )
        if config.network_gateway6:
            lines.append(f"    gateway {config.network_gateway6}")
    else:
        lines.extend(["", f"iface {interface} inet6 auto"])
    return "\n".join(lines) + "\n"


def _configure_ifupdown(config: SetupConfig, interface: str) -> None:
    _ensure_ifupdown_include()
    write_text_atomic(
        IFUPDOWN_CONFIG_PATH,
        _render_ifupdown_config(config, interface),
        mode=0o644,
    )
    _remove_existing_ifupdown_stanzas(interface)


def _disable_cloud_init_networking() -> None:
    if not os.path.isdir(CLOUD_INIT_CONFIG_DIR):
        return
    write_text_atomic(
        CLOUD_INIT_NETWORK_CONFIG_PATH,
        "# Managed by basaltwater\nnetwork: {config: disabled}\n",
        mode=0o644,
    )


def _persist_static_network(
    config: SetupConfig,
    interface: str,
    backend: str,
    rollback: JSONDict | None = None,
) -> None:
    if backend == "networkmanager":
        connection_name = None
        if rollback and rollback.get("kind") == "networkmanager":
            candidate = rollback.get("connection")
            if isinstance(candidate, str):
                connection_name = candidate
        _configure_networkmanager(config, interface, connection_name)
    elif backend == "networkd":
        _configure_networkd(config, interface)
    elif backend == "ifupdown":
        _configure_ifupdown(config, interface)
    else:
        raise RuntimeError(f"Unsupported pending network backend: {backend}")
    _disable_cloud_init_networking()


def _active_interface_addresses(interface: str, version: int) -> set[str]:
    family_flag = "-4" if version == 4 else "-6"
    family_name = "inet" if version == 4 else "inet6"
    result = run(
        f"ip -o {family_flag} address show dev {shlex.quote(interface)}",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not inspect IPv{version} addresses on {interface}: "
            f"{(result.stderr or '').strip() or 'ip command failed'}"
        )

    addresses: set[str] = set()
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        try:
            address = fields[fields.index(family_name) + 1]
        except (ValueError, IndexError):
            continue
        addresses.add(str(ipaddress.ip_interface(address).ip))
    return addresses


def _select_policy_table(version: int) -> int:
    family_flag = "-4" if version == 4 else "-6"
    result = run(
        f"ip {family_flag} rule show",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not inspect IPv{version} policy routing rules")

    used: set[int] = set()
    for line in (result.stdout or "").splitlines():
        priority_match = re.match(r"\s*(\d+):", line)
        table_match = re.search(r"\b(?:lookup|table)\s+(\d+)\b", line)
        if priority_match:
            used.add(int(priority_match.group(1)))
        if table_match:
            used.add(int(table_match.group(1)))

    for table in POLICY_TABLE_RANGE:
        if table in used:
            continue
        route_result = run(
            f"ip {family_flag} route show table {table}",
            check=False,
            capture_output=True,
        )
        if route_result.returncode != 0:
            raise RuntimeError(f"Could not inspect IPv{version} routing table {table}")
        if not (route_result.stdout or "").strip():
            return table
    raise RuntimeError(f"No unused IPv{version} policy routing table is available")


def _transition_addresses(
    config: SetupConfig,
) -> list[ipaddress.IPv4Interface | ipaddress.IPv6Interface]:
    return [
        ipaddress.ip_interface(value)
        for value in (config.static_ipv4, config.static_ipv6)
        if value
    ]


def _snapshot_file(path: str) -> JSONDict:
    validate_filesystem_path(path)
    if not os.path.lexists(path):
        return {"path": path, "kind": "absent"}
    if os.path.islink(path):
        return {"path": path, "kind": "symlink", "target": os.readlink(path)}
    if not os.path.isfile(path):
        raise RuntimeError(f"Cannot snapshot non-file network path: {path}")
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Could not snapshot network config {path}: {exc}") from exc
    return {"path": path, "kind": "file", "content": content, "mode": mode}


def _ifupdown_snapshot_paths() -> list[str]:
    paths = {IFUPDOWN_MAIN_CONFIG_PATH, IFUPDOWN_CONFIG_PATH, CLOUD_INIT_NETWORK_CONFIG_PATH}
    for path in glob.glob(os.path.join(IFUPDOWN_CONFIG_DIR, "*")):
        if os.path.isfile(path) or os.path.islink(path):
            paths.add(path)
        if not path.endswith(".basaltwater.bak"):
            paths.add(f"{path}.basaltwater.bak")
    paths.add(f"{IFUPDOWN_MAIN_CONFIG_PATH}.basaltwater.bak")
    return sorted(paths)


def _capture_persistence_rollback(backend: str, interface: str) -> JSONDict:
    """Capture exactly what persistence can overwrite before any live changes."""

    if backend == "networkmanager":
        connection = _networkmanager_connection(interface)
        properties: dict[str, list[str]] = {}
        for property_name in NETWORKMANAGER_ROLLBACK_PROPERTIES:
            result = run(
                f"nmcli --escape no --get-values {property_name} connection show "
                f"{shlex.quote(connection)}",
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Could not snapshot NetworkManager property {property_name}"
                )
            properties[property_name] = (result.stdout or "").splitlines()
        return {
            "kind": "networkmanager",
            "connection": connection,
            "properties": properties,
            "files": [_snapshot_file(CLOUD_INIT_NETWORK_CONFIG_PATH)],
        }

    if backend not in {"networkd", "ifupdown"}:
        raise RuntimeError(f"Unsupported pending network backend: {backend}")
    paths = (
        [NETWORKD_CONFIG_PATH, CLOUD_INIT_NETWORK_CONFIG_PATH]
        if backend == "networkd"
        else _ifupdown_snapshot_paths()
    )
    return {
        "kind": backend,
        "files": [_snapshot_file(path) for path in paths],
    }


def _allowed_rollback_file(path: str, backend: str) -> bool:
    absolute = os.path.abspath(path)
    if absolute != path:
        return False
    common_paths = {CLOUD_INIT_NETWORK_CONFIG_PATH}
    if backend == "networkd":
        return path in common_paths | {NETWORKD_CONFIG_PATH}
    if backend != "ifupdown":
        return False
    if path in common_paths | {
        IFUPDOWN_MAIN_CONFIG_PATH,
        f"{IFUPDOWN_MAIN_CONFIG_PATH}.basaltwater.bak",
        IFUPDOWN_CONFIG_PATH,
    }:
        return True
    return os.path.dirname(path) == os.path.abspath(IFUPDOWN_CONFIG_DIR)


def _validate_persistence_rollback(rollback: object, backend: str) -> JSONDict:
    if not isinstance(rollback, dict) or rollback.get("kind") != backend:
        raise RuntimeError("Invalid pending persistence rollback data")

    if backend == "networkmanager":
        connection = rollback.get("connection")
        properties = rollback.get("properties")
        files = rollback.get("files")
        if (
            not isinstance(connection, str)
            or not connection
            or any(ord(char) < 32 or ord(char) == 127 for char in connection)
            or not isinstance(properties, dict)
            or set(properties) != set(NETWORKMANAGER_ROLLBACK_PROPERTIES)
            or not isinstance(files, list)
            or len(files) != 1
            or not isinstance(files[0], dict)
            or files[0].get("path") != CLOUD_INIT_NETWORK_CONFIG_PATH
        ):
            raise RuntimeError("Invalid pending NetworkManager rollback data")
        for values in properties.values():
            if not isinstance(values, list) or not all(
                isinstance(value, str) and "\n" not in value and "\r" not in value
                for value in values
            ):
                raise RuntimeError("Invalid pending NetworkManager property snapshot")
        snapshot = files[0]
        kind = snapshot.get("kind")
        if not isinstance(kind, str):
            raise RuntimeError("Invalid pending NetworkManager file snapshot")
        if kind not in {"absent", "file", "symlink"}:
            raise RuntimeError("Invalid pending NetworkManager file snapshot")
        if kind == "file" and (
            not isinstance(snapshot.get("content"), str)
            or not isinstance(snapshot.get("mode"), int)
            or not 0 <= snapshot["mode"] <= 0o7777
        ):
            raise RuntimeError("Invalid pending NetworkManager file contents")
        if kind == "symlink" and not isinstance(snapshot.get("target"), str):
            raise RuntimeError("Invalid pending NetworkManager symlink snapshot")
        return rollback

    files = rollback.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("Invalid pending network file snapshots")
    seen: set[str] = set()
    for snapshot in files:
        if not isinstance(snapshot, dict):
            raise RuntimeError("Invalid pending network file snapshot")
        path = snapshot.get("path")
        kind = snapshot.get("kind")
        if (
            not isinstance(path, str)
            or path in seen
            or not _allowed_rollback_file(path, backend)
            or not isinstance(kind, str)
            or kind not in {"absent", "file", "symlink"}
        ):
            raise RuntimeError("Invalid pending network file snapshot")
        validate_filesystem_path(path)
        seen.add(path)
        if kind == "file" and (
            not isinstance(snapshot.get("content"), str)
            or not isinstance(snapshot.get("mode"), int)
            or not 0 <= snapshot["mode"] <= 0o7777
        ):
            raise RuntimeError("Invalid pending network file contents")
        if kind == "symlink" and not isinstance(snapshot.get("target"), str):
            raise RuntimeError("Invalid pending network symlink snapshot")
    required_paths = {
        CLOUD_INIT_NETWORK_CONFIG_PATH,
        NETWORKD_CONFIG_PATH if backend == "networkd" else IFUPDOWN_MAIN_CONFIG_PATH,
    }
    if backend == "ifupdown":
        required_paths.add(IFUPDOWN_CONFIG_PATH)
    if not required_paths.issubset(seen):
        raise RuntimeError("Pending network rollback is missing required file snapshots")
    return rollback


def _restore_file_snapshot(snapshot: JSONDict) -> None:
    path = snapshot["path"]
    kind = snapshot["kind"]
    if kind == "absent":
        if os.path.lexists(path):
            if os.path.isdir(path) and not os.path.islink(path):
                raise RuntimeError(f"Refusing to remove directory at network config path: {path}")
            os.unlink(path)
        return
    if kind == "file":
        if os.path.isdir(path) and not os.path.islink(path):
            raise RuntimeError(f"Refusing to replace directory at network config path: {path}")
        write_text_atomic(path, snapshot["content"], mode=snapshot["mode"])
        return
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            raise RuntimeError(f"Refusing to replace directory at network config path: {path}")
        os.unlink(path)
    os.symlink(snapshot["target"], path)


def _restore_persistence_rollback(rollback: JSONDict, backend: str) -> None:
    rollback = _validate_persistence_rollback(rollback, backend)
    if backend == "networkmanager":
        connection = rollback["connection"]
        properties = rollback["properties"]
        parts = ["nmcli connection modify", shlex.quote(connection)]
        for property_name in NETWORKMANAGER_ROLLBACK_PROPERTIES:
            parts.extend(
                [property_name, shlex.quote(",".join(properties[property_name]))]
            )
        run(" ".join(parts))
        _restore_file_snapshot(rollback["files"][0])
        return
    for snapshot in rollback["files"]:
        _restore_file_snapshot(snapshot)


def _prepare_live_network_transition(
    config: SetupConfig,
    interface: str,
    backend: str,
) -> None:
    """Add requested addresses without removing the address carrying this SSH run."""

    if os.path.exists(NETWORK_TRANSITION_PATH):
        abort_network_transition()

    addresses = _transition_addresses(config)
    active_by_version = {
        version: _active_interface_addresses(interface, version)
        for version in {address.version for address in addresses}
    }
    added_addresses = [
        str(address)
        for address in addresses
        if str(address.ip) not in active_by_version[address.version]
    ]

    policy_tables: dict[str, int] = {}
    for version, gateway in (
        (4, config.network_gateway4),
        (6, config.network_gateway6),
    ):
        if gateway:
            policy_tables[str(version)] = _select_policy_table(version)

    rollback = _capture_persistence_rollback(backend, interface)
    payload: JSONDict = {
        "version": NETWORK_TRANSITION_VERSION,
        "transition_id": secrets.token_hex(32),
        "state": "prepared",
        "backend": backend,
        "interface": interface,
        "static_ipv4": config.static_ipv4,
        "static_ipv6": config.static_ipv6,
        "gateway4": config.network_gateway4,
        "gateway6": config.network_gateway6,
        "dns": list(config.network_dns or []),
        "added_addresses": added_addresses,
        "policy_tables": policy_tables,
        "rollback": rollback,
    }
    write_json_atomic(NETWORK_TRANSITION_PATH, payload, mode=0o600)

    try:
        for address in added_addresses:
            parsed = ipaddress.ip_interface(address)
            family_flag = "-4" if parsed.version == 4 else "-6"
            run(
                f"ip {family_flag} address add {shlex.quote(str(parsed))} "
                f"dev {shlex.quote(interface)}"
            )

        for address in addresses:
            gateway = (
                config.network_gateway4
                if address.version == 4
                else config.network_gateway6
            )
            if not gateway:
                continue
            family_flag = "-4" if address.version == 4 else "-6"
            host_prefix = 32 if address.version == 4 else 128
            table = policy_tables[str(address.version)]
            run(
                f"ip {family_flag} route replace table {table} "
                f"{shlex.quote(str(address.network))} dev {shlex.quote(interface)} "
                f"src {shlex.quote(str(address.ip))}"
            )
            run(
                f"ip {family_flag} route replace table {table} default via "
                f"{shlex.quote(gateway)} dev {shlex.quote(interface)}"
            )
            run(
                f"ip {family_flag} rule add priority {table} from "
                f"{shlex.quote(f'{address.ip}/{host_prefix}')} table {table}"
            )
    except Exception:
        abort_network_transition()
        raise


def _load_network_transition() -> NetworkTransition:
    try:
        with open(NETWORK_TRANSITION_PATH, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except FileNotFoundError as exc:
        raise RuntimeError("No pending network transition exists") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read pending network transition: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("version") != NETWORK_TRANSITION_VERSION:
        raise RuntimeError("Invalid pending network transition format")

    transition_id = payload.get("transition_id")
    state = payload.get("state")
    if (
        not isinstance(transition_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", transition_id)
        or not isinstance(state, str)
        or state not in NETWORK_TRANSITION_STATES
    ):
        raise RuntimeError("Invalid pending network transition identity or state")

    backend = payload.get("backend")
    if not isinstance(backend, str) or backend not in {
        "networkmanager",
        "networkd",
        "ifupdown",
    }:
        raise RuntimeError("Invalid pending network backend")
    interface_value = payload.get("interface")
    if not isinstance(interface_value, str):
        raise RuntimeError("Invalid pending network interface")
    interface = validate_network_interface_name(interface_value)

    string_fields: dict[str, MaybeStr] = {}
    for field in ("static_ipv4", "static_ipv6", "gateway4", "gateway6"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise RuntimeError(f"Invalid pending network field: {field}")
        string_fields[field] = value

    dns = payload.get("dns", [])
    if not isinstance(dns, list) or not all(isinstance(value, str) for value in dns):
        raise RuntimeError("Invalid pending DNS settings")

    config = SetupConfig(
        host="localhost",
        username="root",
        system_type="server_lite",
        static_ipv4=string_fields["static_ipv4"],
        static_ipv6=string_fields["static_ipv6"],
        network_gateway4=string_fields["gateway4"],
        network_gateway6=string_fields["gateway6"],
        network_dns=list(dns),
        network_interface=interface,
        activate_network=False,
    )
    validate_network_setup_settings(config)

    expected_addresses = {str(address) for address in _transition_addresses(config)}
    added_addresses = payload.get("added_addresses", [])
    if (
        not isinstance(added_addresses, list)
        or not all(isinstance(value, str) for value in added_addresses)
        or len(added_addresses) != len(set(added_addresses))
        or not set(added_addresses).issubset(expected_addresses)
    ):
        raise RuntimeError("Invalid pending added-address list")

    raw_tables = payload.get("policy_tables", {})
    if not isinstance(raw_tables, dict):
        raise RuntimeError("Invalid pending policy routing tables")
    policy_tables: dict[str, int] = {}
    for version, table in raw_tables.items():
        if version not in {"4", "6"} or not isinstance(table, int) or table not in POLICY_TABLE_RANGE:
            raise RuntimeError("Invalid pending policy routing table")
        policy_tables[version] = table

    expected_table_versions = {
        version
        for version, gateway in (("4", config.network_gateway4), ("6", config.network_gateway6))
        if gateway
    }
    if set(policy_tables) != expected_table_versions:
        raise RuntimeError("Pending policy routing tables do not match requested gateways")

    rollback = _validate_persistence_rollback(payload.get("rollback"), backend)
    return NetworkTransition(
        transition_id=transition_id,
        state=state,
        config=config,
        interface=interface,
        backend=backend,
        added_addresses=list(added_addresses),
        policy_tables=policy_tables,
        rollback=rollback,
        payload=payload,
    )


def _require_transition_id(
    transition: NetworkTransition,
    expected_id: MaybeStr,
) -> None:
    if expected_id is not None and transition.transition_id != expected_id:
        raise RuntimeError("Pending network transition identity does not match")


def commit_network_transition(expected_id: MaybeStr = None) -> None:
    """Persist a live transition only after the controller verified new-address SSH."""

    transition = _load_network_transition()
    _require_transition_id(transition, expected_id)
    if transition.state == "committed":
        print(f"  ✓ Network transition already persisted for {transition.interface}")
        return
    _reject_managed_bridge(transition.interface)
    try:
        _persist_static_network(
            transition.config,
            transition.interface,
            transition.backend,
            transition.rollback,
        )
        committed_payload = dict(transition.payload)
        committed_payload["state"] = "committed"
        write_json_atomic(NETWORK_TRANSITION_PATH, committed_payload, mode=0o600)
    except Exception as exc:
        try:
            _restore_persistence_rollback(transition.rollback, transition.backend)
        except Exception as rollback_exc:
            raise RuntimeError(
                f"Network persistence failed and rollback also failed: {rollback_exc}"
            ) from exc
        raise
    print(
        f"  ✓ Verified network transition persisted for {transition.interface} "
        f"via {transition.backend}"
    )


def abort_network_transition(expected_id: MaybeStr = None) -> None:
    """Restore persistence and remove temporary state from a transition."""

    if not os.path.exists(NETWORK_TRANSITION_PATH):
        return
    transition = _load_network_transition()
    _require_transition_id(transition, expected_id)
    _restore_persistence_rollback(transition.rollback, transition.backend)

    for version, table in transition.policy_tables.items():
        family_flag = "-4" if version == "4" else "-6"
        address_value = (
            transition.config.static_ipv4
            if version == "4"
            else transition.config.static_ipv6
        )
        if not address_value:
            raise RuntimeError(f"Pending IPv{version} policy route has no source address")
        address = ipaddress.ip_interface(address_value)
        host_prefix = 32 if address.version == 4 else 128
        run(
            f"ip {family_flag} rule del priority {table} from "
            f"{shlex.quote(f'{address.ip}/{host_prefix}')} table {table}",
            check=False,
            capture_output=True,
        )
        run(
            f"ip {family_flag} route flush table {table}",
            check=False,
            capture_output=True,
        )

    for address in transition.added_addresses:
        parsed = ipaddress.ip_interface(address)
        family_flag = "-4" if parsed.version == 4 else "-6"
        run(
            f"ip {family_flag} address del {shlex.quote(str(parsed))} "
            f"dev {shlex.quote(transition.interface)}",
            check=False,
            capture_output=True,
        )
    os.unlink(NETWORK_TRANSITION_PATH)


def finalize_network_transition(expected_id: MaybeStr = None) -> None:
    """Discard rollback state after every post-change accessibility check passes."""

    if not os.path.exists(NETWORK_TRANSITION_PATH):
        return
    transition = _load_network_transition()
    _require_transition_id(transition, expected_id)
    if transition.state != "committed":
        raise RuntimeError("Cannot finalize a network transition before persistence")
    os.unlink(NETWORK_TRANSITION_PATH)


def configure_static_network(config: SetupConfig) -> None:
    """Persist static networking without interrupting the active SSH session."""

    if not (config.static_ipv4 or config.static_ipv6):
        return

    validate_network_setup_settings(config)
    if config.dry_run or is_dry_run():
        interface = config.network_interface or "<default-route-interface>"
        action = "prepare and verify" if config.activate_network else "stage"
        print(f"  [DRY-RUN] Would {action} static network configuration for {interface}")
        return

    interface = _resolve_network_interface(config.network_interface)
    _reject_managed_bridge(interface)
    backend = _detect_network_backend()

    if config.activate_network:
        _prepare_live_network_transition(config, interface, backend)
        print(
            f"  ✓ Temporary addresses prepared for {interface}; retaining the current "
            "address while the controller verifies SSH"
        )
        return

    rollback = _capture_persistence_rollback(backend, interface)
    try:
        _persist_static_network(config, interface, backend, rollback)
    except Exception as exc:
        try:
            _restore_persistence_rollback(rollback, backend)
        except Exception as rollback_exc:
            raise RuntimeError(
                f"Static network staging failed and rollback also failed: {rollback_exc}"
            ) from exc
        raise

    print(
        f"  ✓ Static network configuration staged for {interface} via {backend}; "
        "it will take effect after reboot or an explicit interface restart"
    )


def _network_transition_main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "--commit-transition":
            commit_network_transition(argv[1])
            return 0
        if len(argv) == 2 and argv[0] == "--abort-transition":
            abort_network_transition(argv[1])
            return 0
        if len(argv) == 2 and argv[0] == "--finalize-transition":
            finalize_network_transition(argv[1])
            return 0
        print(
            "Usage: python3 -m common.network_steps "
            "--commit-transition|--abort-transition|--finalize-transition ID"
        )
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Network transition failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_network_transition_main(sys.argv[1:]))
