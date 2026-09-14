#!/usr/bin/env python3
"""Shared Proxmox guest-provisioning helpers."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ipaddress
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any, Callable, Optional

from lib.concurrency import ResourceBusyError, resource_lock
from lib.proxmox_hosts import ProxmoxHost, ProxmoxHostFacts, ProxmoxStoragePool
from lib.ssh_enrollment import replace_scanned_host_keys
from lib.ssh_utils import (
    build_ssh_command,
    ensure_remote_sudo,
    get_ssh_control_path,
    get_workspace_known_hosts_path,
    ssh_batch_mode,
    ssh_process_timeout,
)
from lib.types import StrList
from lib.validators import validate_username


class ProvisionError(Exception):
    """Raised when guest provisioning fails."""


@contextmanager
def guest_provisioning_locks(
    node: str,
    target_ip: str,
    hostname: str,
) -> Iterator[None]:
    """Reserve guest identity on this controller while allowing other guests."""
    resources = sorted(
        {
            f"{node}|address|{target_ip}",
            f"{node}|hostname|{hostname.lower()}",
        }
    )
    try:
        with ExitStack() as stack:
            for resource in resources:
                stack.enter_context(resource_lock("proxmox-guest", resource))
            yield
    except ResourceBusyError as exc:
        raise ProvisionError(
            f"Another infra-tools process is already provisioning {hostname} "
            f"({target_ip}) on {node}"
        ) from exc


def _create_with_vmid_retry(
    create_guest: Callable[..., None],
    get_next_vmid: Callable[[], int],
    create_kwargs: dict[str, Any],
    *,
    attempts: int = 8,
) -> int:
    """Create a guest, retrying IDs that another provision claimed first."""
    for attempt in range(attempts):
        vmid = int(create_kwargs["vmid"])
        try:
            create_guest(**create_kwargs)
            return vmid
        except ProvisionError as exc:
            if "already exists" not in str(exc).lower() or attempt + 1 == attempts:
                raise
            print(
                f"  ⚠ VMID {vmid} was allocated concurrently; "
                "retrying with a new VMID"
            )
            create_kwargs["vmid"] = get_next_vmid()
    raise AssertionError("unreachable VMID retry state")


def _ssh_opts(hosted_key: Optional[str] = None) -> StrList:
    """Build SSH options list for Proxmox node connections."""
    opts = [
        "-o", f"UserKnownHostsFile={get_workspace_known_hosts_path()}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"BatchMode={'yes' if ssh_batch_mode() else 'no'}",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30",
    ]
    if hosted_key:
        opts.extend(["-i", hosted_key])
    return opts


def _ssh_run(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    cmd: str,
    dry_run: bool = False,
    log_cmd: Optional[str] = None,
    quiet: bool = False,
    input_data: Optional[str] = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a command on the Proxmox host via SSH."""
    display_cmd = log_cmd if log_cmd is not None else cmd
    display_cmd = display_cmd[:80] + "..." if len(display_cmd) > 80 else display_cmd
    if not quiet:
        print(f"  Running on {node_ip}: {display_cmd}")
        sys.stdout.flush()

    if dry_run:
        print("  [DRY-RUN] Command not executed")
        return subprocess.CompletedProcess(
            args=[cmd], returncode=0, stdout="", stderr=""
        )

    hosted_key = None
    if "-i" in ssh_opts:
        key_index = ssh_opts.index("-i") + 1
        if key_index < len(ssh_opts):
            hosted_key = ssh_opts[key_index]
    ssh_cmd = ["ssh"] + ssh_opts + [
        "-o", "ControlMaster=auto",
        "-o", "ControlPersist=60s",
        "-o", f"ControlPath={get_ssh_control_path(node_ip, user, hosted_key)}",
        f"{user}@{node_ip}",
        cmd,
    ]
    result = subprocess.run(
        ssh_cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=ssh_process_timeout(timeout, batch_mode=ssh_batch_mode()),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr and not quiet:
            print(f"    Warning: {stderr[:200]}")
        if not quiet:
            sys.stdout.flush()
    return result


def _storage_pool_supports_content(
    pool: str,
    content_filter: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
    *,
    strict: bool = False,
) -> bool:
    result = _ssh_run(
        node_ip, user, ssh_opts,
        f"pvesm status --content {shlex.quote(content_filter)}",
        dry_run=dry_run,
    )

    if dry_run:
        return True

    if result.returncode != 0:
        if strict:
            return False
        fallback_result = _ssh_run(
            node_ip, user, ssh_opts,
            "pvesm status",
            dry_run=dry_run,
        )
        if fallback_result.returncode != 0:
            return False

        for line in fallback_result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if parts and parts[0] == pool and len(parts) >= 3 and parts[2] == "active":
                return True
        return False

    for line in result.stdout.strip().split('\n')[1:]:
        parts = line.split()
        if parts and parts[0] == pool and len(parts) >= 3 and parts[2] == "active":
            return True

    return False


def _list_proxmox_bridges(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> StrList:
    """Return available Linux bridge interfaces on the Proxmox host."""
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        "ip -o link show type bridge | awk -F': ' '{print $2}' | cut -d@ -f1 | sort -u",
        dry_run=dry_run,
    )

    if dry_run:
        return ["vmbr0"]

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ProvisionError(
            f"Failed to query bridges on Proxmox host {node_ip}: "
            f"{stderr or 'ssh command failed'}"
        )

    bridges = [bridge.strip() for bridge in result.stdout.splitlines() if bridge.strip()]
    if "vmbr0" in bridges:
        bridges = ["vmbr0"] + [bridge for bridge in bridges if bridge != "vmbr0"]
    return bridges


def auto_detect_bridge(
    node_ip: str,
    user: str = "root",
    hosted_key: Optional[str] = None,
    dry_run: bool = False,
    preferred_bridge: Optional[str] = None,
) -> str:
    """Auto-detect the guest bridge, preferring the host's default route."""
    opts = _ssh_opts(hosted_key)
    bridges = _list_proxmox_bridges(node_ip, user, opts, dry_run=dry_run)

    if not bridges:
        raise ProvisionError(
            "No Linux network bridge found on the Proxmox host"
        )

    if preferred_bridge:
        if preferred_bridge not in bridges:
            raise ProvisionError(
                f"Configured bridge '{preferred_bridge}' was not found on the Proxmox host"
            )
        bridge = preferred_bridge
    else:
        route_result = _ssh_run(
            node_ip,
            user,
            opts,
            "ip route show default | awk '{for (i=1; i<=NF; i++) if ($i == \"dev\") {print $(i+1); exit}}'",
            dry_run=dry_run,
        )
        route_bridge = (route_result.stdout or "").strip()
        if route_bridge and route_bridge not in bridges:
            raise ProvisionError(
                f"Default route uses '{route_bridge}', which is not a Proxmox bridge; "
                "specify --bridge explicitly"
            )
        bridge = route_bridge or bridges[0]
    print(f"  ✓ Detected bridge: {bridge}")
    return bridge


def _get_node_name(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> Optional[str]:
    """Return the short hostname for the Proxmox node."""
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        "hostname -s",
        dry_run=dry_run,
    )
    if dry_run:
        return "pve"
    if result.returncode != 0:
        return None
    node_name = (result.stdout or "").strip()
    return node_name or None


def _get_corosync_config(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> str:
    """Return the cluster corosync config when available."""
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        "cat /etc/pve/corosync.conf",
        dry_run=dry_run,
    )
    if dry_run:
        return (
            "totem {\n"
            "  cluster_name: homelab\n"
            "}\n"
            "nodelist {\n"
            "  node {\n"
            "    name: pve1\n"
            "    ring0_addr: 10.0.0.10\n"
            "  }\n"
            "  node {\n"
            "    name: pve2\n"
            "    ring0_addr: 10.0.0.11\n"
            "  }\n"
            "}\n"
        )
    if result.returncode != 0:
        return ""
    return result.stdout or ""


def _parse_corosync_config(config_text: str) -> tuple[Optional[str], list[tuple[str, str]]]:
    """Extract cluster name and member node addresses from corosync config text."""
    cluster_match = re.search(r"cluster_name:\s*(\S+)", config_text)
    cluster_name = cluster_match.group(1) if cluster_match else None

    nodes: list[tuple[str, str]] = []
    for block in re.findall(r"node\s*\{(.*?)\}", config_text, flags=re.DOTALL):
        name_match = re.search(r"name:\s*(\S+)", block)
        address_match = re.search(r"ring0_addr:\s*(\S+)", block)
        if not name_match or not address_match:
            continue
        nodes.append((name_match.group(1), address_match.group(1)))
    return cluster_name, nodes


def _get_bridge_prefix_length(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    bridge: str,
    dry_run: bool = False,
) -> str:
    result = _ssh_run(
        node_ip, user, ssh_opts,
        f"ip -o -f inet addr show dev {shlex.quote(bridge)} | awk 'NR==1 {{print $4}}'",
        dry_run=dry_run,
    )

    if dry_run:
        return "24"

    cidr = result.stdout.strip()
    if not cidr or "/" not in cidr:
        raise ProvisionError(f"Could not detect IPv4 prefix length for bridge {bridge}")

    prefix = cidr.split("/", 1)[1]
    print(f"  ✓ Detected bridge network: {cidr}")
    return prefix


def _get_host_gateway(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> str:
    """Get the default gateway from the Proxmox host."""
    result = _ssh_run(
        node_ip, user, ssh_opts,
        "ip route | awk '/default/ {print $3; exit}'",
        dry_run=dry_run,
    )

    if dry_run:
        return "10.0.0.1"

    gateway = result.stdout.strip()
    if not gateway:
        raise ProvisionError("Could not detect default gateway on Proxmox host")

    print(f"  ✓ Detected gateway: {gateway}")
    return gateway


def _get_guest_gateway(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    bridge: str,
    guest_interface: ipaddress.IPv4Interface,
    dry_run: bool = False,
) -> str:
    """Resolve the guest gateway from the selected bridge and guest subnet."""
    if dry_run:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    else:
        result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            (
                "ip -4 route show default; "
                f"ip -o -4 addr show dev {shlex.quote(bridge)}"
            ),
            dry_run=False,
        )
        if result.returncode != 0:
            raise ProvisionError(
                f"Could not inspect IPv4 routing for Proxmox bridge {bridge}"
            )

    network = guest_interface.network
    guest_ip = guest_interface.ip
    bridge_addresses: list[ipaddress.IPv4Address] = []
    for line in (result.stdout or "").splitlines():
        route_match = re.search(r"^default\s+via\s+(\S+)\s+dev\s+(\S+)", line)
        if route_match and route_match.group(2) == bridge:
            try:
                route_gateway = ipaddress.IPv4Address(route_match.group(1))
            except ipaddress.AddressValueError:
                continue
            if route_gateway in network and route_gateway != guest_ip:
                print(f"  ✓ Detected gateway on {bridge}: {route_gateway}")
                return str(route_gateway)

        address_match = re.search(r"\sinet\s+(\d+\.\d+\.\d+\.\d+)/\d+", line)
        if address_match:
            try:
                bridge_addresses.append(ipaddress.IPv4Address(address_match.group(1)))
            except ipaddress.AddressValueError:
                continue

    for bridge_address in bridge_addresses:
        if bridge_address in network and bridge_address != guest_ip:
            print(f"  ✓ Using {bridge} address as guest gateway: {bridge_address}")
            return str(bridge_address)

    for candidate in network.hosts():
        if candidate == guest_ip:
            continue
        print(
            f"  ⚠ No gateway route or bridge address found in {network}; "
            f"assuming {candidate}"
        )
        return str(candidate)

    raise ProvisionError(
        f"Could not infer a gateway for {guest_interface}; specify --gateway"
    )


def _is_usable_nameserver(addr: str) -> bool:
    """Return True for a non-local unicast nameserver address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return False
    return True


def _get_host_nameservers(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
    *,
    bridge: Optional[str] = None,
    fallback_gateway: Optional[str] = None,
) -> StrList:
    """Get upstream DNS from the Proxmox node, preferring the selected bridge."""
    commands: StrList = []
    if bridge:
        commands.append(
            f"resolvectl dns {shlex.quote(bridge)} 2>/dev/null | "
            "awk '{for (i=2;i<=NF;i++) print $i}'"
        )
    commands.extend(
        [
            "resolvectl dns 2>/dev/null | awk '{for (i=2;i<=NF;i++) print $i}'",
            "grep -oP '^nameserver\\s+\\K\\S+' /etc/resolv.conf",
        ]
    )
    result = _ssh_run(
        node_ip, user, ssh_opts,
        "; ".join(commands),
        dry_run=dry_run,
    )

    if dry_run:
        return [fallback_gateway or "1.1.1.1"]

    seen: set[str] = set()
    nameservers: StrList = []
    for ns in (result.stdout or "").split('\n'):
        ns = ns.strip()
        if not ns or ns in seen:
            continue
        if not _is_usable_nameserver(ns):
            continue
        seen.add(ns)
        nameservers.append(ns)
        if len(nameservers) >= 3:
            break

    if not nameservers:
        if fallback_gateway and _is_usable_nameserver(fallback_gateway):
            print(
                "  ⚠ No usable upstream DNS detected on the Proxmox node; "
                f"using gateway {fallback_gateway}"
            )
            return [fallback_gateway]
        print("  ⚠ No usable nameservers detected on host; using 1.1.1.1")
        return ["1.1.1.1"]

    print(f"  ✓ Detected nameservers: {', '.join(nameservers)}")
    return nameservers


def _get_next_vmid(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> int:
    """Get the next available VMID from the Proxmox cluster."""
    result = _ssh_run(
        node_ip, user, ssh_opts,
        "pvesh get /cluster/nextid",
        dry_run=dry_run,
    )

    if dry_run:
        return 100

    try:
        vmid = int(result.stdout.strip())
    except (ValueError, TypeError):
        raise ProvisionError(
            f"Could not determine next VMID: {result.stdout.strip()}"
        )

    print(f"  ✓ Next VMID: {vmid}")
    return vmid


def _resolve_storage_pool(
    pool_arg: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    content_filter: str,
    dry_run: bool = False,
    *,
    strict_content: bool = False,
) -> str:
    """Resolve a storage pool name."""
    if pool_arg != "auto":
        if not _storage_pool_supports_content(
            pool_arg,
            content_filter,
            node_ip,
            user,
            ssh_opts,
            dry_run=dry_run,
            strict=strict_content,
        ):
            raise ProvisionError(
                f"Storage pool '{pool_arg}' does not support content type '{content_filter}'"
            )
        print(f"  ✓ Using storage pool: {pool_arg} ({content_filter})")
        return pool_arg

    result = _ssh_run(
        node_ip, user, ssh_opts,
        f"pvesm status --content {shlex.quote(content_filter)}",
        dry_run=dry_run,
    )

    if dry_run:
        print("  [DRY-RUN] Would resolve storage pool")
        return (
            "local"
            if content_filter in {"import", "iso", "snippets"}
            else "local-lvm"
        )

    if result.returncode != 0:
        raise ProvisionError(
            f"Could not query storage pools for content type '{content_filter}'"
        )

    for line in result.stdout.strip().split('\n')[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "active":
            pool = parts[0]
            print(f"  ✓ Auto-selected storage pool: {pool}")
            return pool

    raise ProvisionError(
        "No suitable storage pool found. Specify one explicitly with --storage root POOL AMOUNT or --storage template POOL"
    )


def _list_storage_pools(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
) -> list[ProxmoxStoragePool]:
    """Return storage pools reported by ``pvesm status``."""
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        "pvesm status",
        dry_run=dry_run,
    )
    if dry_run:
        return [
            ProxmoxStoragePool(
                name="local",
                type="dir",
                status="active",
                content=["iso", "backup", "vztmpl"],
            ),
            ProxmoxStoragePool(
                name="local-lvm",
                type="lvmthin",
                status="active",
                content=["images", "rootdir"],
            ),
        ]
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ProvisionError(
            f"Could not query storage pools on {node_ip}: "
            f"{stderr or 'ssh command failed'}"
        )

    storage_pools: list[ProxmoxStoragePool] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        storage_pools.append(
            ProxmoxStoragePool(
                name=parts[0],
                type=parts[1],
                status=parts[2],
            )
        )
    return storage_pools


def _list_storage_names_for_content(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    content_type: str,
    dry_run: bool = False,
) -> set[str]:
    """Return pools supporting a specific content type."""
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        f"pvesm status --content {shlex.quote(content_type)}",
        dry_run=dry_run,
    )
    if dry_run:
        defaults = {
            "images": {"local-lvm"},
            "rootdir": {"local-lvm"},
            "vztmpl": {"local"},
        }
        return set(defaults.get(content_type, set()))
    if result.returncode != 0:
        return set()

    pools: set[str] = set()
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        if parts[2] == "active":
            pools.add(parts[0])
    return pools


def _choose_storage_pool(
    storage_pools: list[ProxmoxStoragePool],
    *,
    required_content: tuple[str, ...],
    preferred_names: tuple[str, ...],
) -> Optional[str]:
    """Pick a preferred active pool supporting at least one required content type."""
    active_pools = [
        pool for pool in storage_pools
        if (pool.status or "").lower() == "active"
    ]
    candidates = [
        pool for pool in active_pools
        if any(content in pool.content for content in required_content)
    ]
    if not candidates:
        return None

    for preferred in preferred_names:
        if not preferred:
            continue
        for pool in candidates:
            if pool.name == preferred:
                return pool.name
    return candidates[0].name


def probe_proxmox_host(
    node_ip: str,
    user: str = "root",
    hosted_key: Optional[str] = None,
    dry_run: bool = False,
) -> ProxmoxHostFacts:
    """Probe a Proxmox node for bridges, gateway, nameservers, and storage defaults."""
    ssh_opts = _ssh_opts(hosted_key)
    bridges = _list_proxmox_bridges(node_ip, user, ssh_opts, dry_run=dry_run)
    storage_pools = _list_storage_pools(node_ip, user, ssh_opts, dry_run=dry_run)

    images_pools = _list_storage_names_for_content(
        node_ip, user, ssh_opts, "images", dry_run=dry_run
    )
    rootdir_pools = _list_storage_names_for_content(
        node_ip, user, ssh_opts, "rootdir", dry_run=dry_run
    )
    vztmpl_pools = _list_storage_names_for_content(
        node_ip, user, ssh_opts, "vztmpl", dry_run=dry_run
    )
    for pool in storage_pools:
        content: list[str] = []
        if pool.name in images_pools:
            content.append("images")
        if pool.name in rootdir_pools:
            content.append("rootdir")
        if pool.name in vztmpl_pools:
            content.append("vztmpl")
        pool.content = content

    default_root_storage = _choose_storage_pool(
        storage_pools,
        required_content=("images", "rootdir"),
        preferred_names=("local-lvm", "local"),
    )
    default_template_storage = _choose_storage_pool(
        storage_pools,
        required_content=("vztmpl",),
        preferred_names=("local", default_root_storage or ""),
    )

    return ProxmoxHostFacts(
        node_name=_get_node_name(node_ip, user, ssh_opts, dry_run=dry_run),
        bridges=bridges,
        gateway=_get_host_gateway(node_ip, user, ssh_opts, dry_run=dry_run),
        nameservers=_get_host_nameservers(node_ip, user, ssh_opts, dry_run=dry_run),
        storage_pools=storage_pools,
        default_root_storage=default_root_storage,
        default_template_storage=default_template_storage,
        default_bridge=bridges[0] if bridges else None,
    )


def probe_proxmox_cluster(
    seed_address: str,
    user: str = "root",
    hosted_key: Optional[str] = None,
    *,
    tags: Optional[StrList] = None,
    dry_run: bool = False,
) -> list[ProxmoxHost]:
    """Probe every node in a Proxmox cluster from a single reachable seed node."""
    ssh_opts = _ssh_opts(hosted_key)
    _cluster_name, members = _parse_corosync_config(
        _get_corosync_config(seed_address, user, ssh_opts, dry_run=dry_run)
    )
    if not members:
        seed_name = _get_node_name(seed_address, user, ssh_opts, dry_run=dry_run)
        members = [(seed_name or seed_address, seed_address)]

    discovered_hosts: list[ProxmoxHost] = []
    seen_names: set[str] = set()
    seen_addresses: set[str] = set()
    for configured_name, address in members:
        facts = probe_proxmox_host(
            address,
            user=user,
            hosted_key=hosted_key,
            dry_run=dry_run,
        )
        host_name = facts.node_name or configured_name or address
        if host_name.lower() in seen_names or address in seen_addresses:
            continue
        seen_names.add(host_name.lower())
        seen_addresses.add(address)
        discovered_hosts.append(
            ProxmoxHost(
                name=host_name,
                address=address,
                user=user,
                ssh_key=hosted_key,
                default_storage=facts.default_root_storage,
                default_template_storage=facts.default_template_storage,
                default_bridge=facts.default_bridge,
                facts=facts,
                tags=list(tags or []),
            )
        )
    return discovered_hosts


def _build_guest_hostname(
    target_ip: str,
    friendly_name: Optional[str],
    *,
    default_prefix: str = "guest",
) -> str:
    """Derive a hostname for any Proxmox guest."""
    if friendly_name:
        hostname = re.sub(r'[^a-z0-9-]', '-', friendly_name.lower()).strip('-')
        hostname = re.sub(r'-+', '-', hostname)
        if hostname:
            return hostname[:63].rstrip('-') or hostname[:63]

    return (f"{default_prefix}-{target_ip.replace('.', '-')}")[:63]


def _resolve_public_key_path(ssh_key: Optional[str]) -> Optional[str]:
    """Return a readable public key matching a readable private key, if any."""
    if not ssh_key:
        return None
    expanded_key = os.path.abspath(os.path.expanduser(ssh_key))
    pub_path = expanded_key + ".pub"
    try:
        if (
            os.path.isfile(expanded_key)
            and os.access(expanded_key, os.R_OK)
            and os.path.getsize(expanded_key) > 0
            and os.path.isfile(pub_path)
            and os.access(pub_path, os.R_OK)
            and os.path.getsize(pub_path) > 0
        ):
            return pub_path
    except OSError:
        return None
    return None


def resolve_guest_ssh_key(
    *preferred_keys: Optional[str],
    home: Optional[str] = None,
) -> Optional[str]:
    """Find a private key and matching public key for VM cloud-init.

    Hosted VM setup needs the public half of an SSH identity in cloud-init,
    while the subsequent regular setup uses the private half.  Prefer keys
    already associated with the Proxmox connection, then follow the standard
    OpenSSH identity names used by regular setup.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for key in preferred_keys:
        if not key:
            continue
        candidate = os.path.abspath(os.path.expanduser(key))
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    ssh_dir = os.path.join(os.path.expanduser(home or "~"), ".ssh")
    for identity_name in ("id_ed25519", "id_ecdsa", "id_rsa"):
        candidate = os.path.join(ssh_dir, identity_name)
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    for candidate in candidates:
        try:
            if not os.path.isfile(candidate) or not os.access(candidate, os.R_OK):
                continue
        except OSError:
            continue
        if _resolve_public_key_path(candidate):
            return candidate

    return None


def ensure_guest_ipv4_route(
    target_interface: str,
    gateway: str,
    username: str,
    ssh_key: Optional[str],
    *,
    dry_run: bool = False,
) -> None:
    """Verify and repair a provisioned guest's live IPv4 default route.

    Proxmox stores the initial gateway in guest metadata, but cloud-init or a
    later network backend can still bring up the address without installing
    the corresponding route.  This runs after guest SSH is available and
    before package setup, so a hosted guest can recover without an operator
    logging in manually.  Persistent configuration is written by the regular
    static-network setup step later in the run.
    """
    if not validate_username(username):
        raise ProvisionError(f"Invalid guest SSH username: {username}")

    try:
        parsed_interface = ipaddress.ip_interface(target_interface)
        parsed_gateway = ipaddress.ip_address(gateway)
    except ValueError as exc:
        raise ProvisionError(
            f"Invalid guest IPv4 route settings: {target_interface}, {gateway}"
        ) from exc
    if not isinstance(parsed_interface, ipaddress.IPv4Interface):
        raise ProvisionError("Provisioned guest route requires an IPv4 interface")
    if not isinstance(parsed_gateway, ipaddress.IPv4Address):
        raise ProvisionError("Provisioned guest route requires an IPv4 gateway")
    if parsed_gateway not in parsed_interface.network:
        raise ProvisionError(
            f"Guest gateway {parsed_gateway} is not reachable on {parsed_interface.network}"
        )

    if dry_run:
        print(
            "  [DRY-RUN] Would verify and repair the guest IPv4 default route "
            f"via {parsed_gateway}"
        )
        return

    target_ip = str(parsed_interface.ip)
    gateway_ip = str(parsed_gateway)
    control_path = get_ssh_control_path(target_ip, username, ssh_key)
    if not ensure_remote_sudo(
        target_ip,
        username,
        ssh_key,
        control_path=control_path,
    ):
        raise ProvisionError(
            f"Remote sudo authentication was not available for {username}@{target_ip}"
        )
    route_replace = (
        'ip -4 route replace default via "$gateway" dev "$interface"'
        if username == "root"
        else 'sudo -n ip -4 route replace default via "$gateway" dev "$interface"'
    )
    remote_script = "\n".join(
        [
            "set -eu",
            f"target_ip={shlex.quote(target_ip)}",
            f"gateway={shlex.quote(gateway_ip)}",
            "interface=$(ip -o -4 addr show | awk -v target=\"$target_ip\" '$4 ~ (\"^\" target \"/\") {print $2; exit}')",
            'if [ -z "$interface" ]; then',
            '  echo "Could not find the guest interface carrying $target_ip" >&2',
            "  exit 1",
            "fi",
            'expected="$gateway $interface"',
            'current=$(ip -4 route show default | awk \'$1 == "default" {print $3 " " $5; exit}\')',
            'if [ "$current" = "$expected" ]; then',
            '  echo already',
            "  exit 0",
            "fi",
            route_replace,
            'current=$(ip -4 route show default | awk \'$1 == "default" {print $3 " " $5; exit}\')',
            'if [ "$current" != "$expected" ]; then',
            '  echo "Guest default route did not become $expected" >&2',
            "  exit 1",
            "fi",
            "echo repaired",
        ]
    )
    batch_mode = ssh_batch_mode()
    command = build_ssh_command(
        target_ip,
        username,
        ssh_key,
        remote_command=remote_script,
        batch_mode=batch_mode,
        connect_timeout=30,
        server_alive_interval=30,
        control_path=control_path,
    )
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=ssh_process_timeout(60, batch_mode=batch_mode),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown SSH error"
        raise ProvisionError(
            f"Could not verify or repair the guest IPv4 route as {username}: {detail}"
        )

    if "repaired" in (result.stdout or ""):
        print(f"  ✓ Repaired guest IPv4 default route via {gateway_ip}")
    else:
        print(f"  ✓ Guest IPv4 default route already uses {gateway_ip}")


def _wait_for_guest_ssh(
    target_ip: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    timeout: int = 90,
    dry_run: bool = False,
    *,
    label: str = "Guest",
) -> None:
    """Wait for sshd inside a newly provisioned guest to accept TCP connections."""
    if dry_run:
        return

    guest_label = label.lower()
    print(
        f"  Waiting for {guest_label} SSH at {target_ip}:22 "
        "(cloud-init may still be completing)..."
    )
    deadline = time.monotonic() + timeout
    attempts = 0
    progress_interval = max(1, 30 // 3)
    probe = (
        f"timeout 3 bash -c '</dev/tcp/{shlex.quote(target_ip)}/22' "
        f"&& echo READY"
    )
    last_err = ""
    while time.monotonic() < deadline:
        attempts += 1
        result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            probe,
            dry_run=False,
            quiet=True,
        )
        if result.returncode == 0 and "READY" in result.stdout:
            print(f"  ✓ {label} SSH is reachable at {target_ip}:22")
            sys.stdout.flush()
            return
        last_err = (result.stderr or result.stdout or "").strip()
        if attempts % progress_interval == 0:
            elapsed = min(timeout, int(timeout - max(0, deadline - time.monotonic())))
            print(
                f"  Still waiting for {guest_label} SSH "
                f"({elapsed}s elapsed; cloud-init may still be completing)..."
            )
        time.sleep(3)
    raise ProvisionError(
        f"Timed out after {timeout}s waiting for {guest_label} SSH on {target_ip}:22 "
        f"(last probe: {last_err or 'no response'})"
    )


def enroll_provisioned_guest_host_keys(
    target_ip: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    dry_run: bool = False,
) -> None:
    """Enroll a provisioned guest's SSH key through its trusted Proxmox node.

    Callers must first establish that the guest is either newly created or
    matches saved infra-tools provisioning metadata. Scanning from the already
    authenticated Proxmox node keeps the discovery on the guest's bridge and
    lets strict host-key checking remain enabled for both infra-tools and the
    invoking user's subsequent direct guest connections.
    """
    if dry_run:
        print(
            f"  [DRY-RUN] Would enroll the guest SSH host key for {target_ip}"
        )
        return

    scan_result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        f"ssh-keyscan -T 10 -t ed25519 {shlex.quote(target_ip)}",
        dry_run=False,
        quiet=True,
    )
    scan = (scan_result.stdout or "").strip()
    if scan_result.returncode != 0 or not scan:
        detail = (scan_result.stderr or "").strip() or "no ED25519 key was returned"
        raise ProvisionError(
            f"Could not read the guest SSH host key for {target_ip}: {detail}"
        )
    workspace_known_hosts = get_workspace_known_hosts_path()
    user_known_hosts = os.path.abspath(
        os.path.expanduser("~/.ssh/known_hosts")
    )
    known_hosts_paths = list(
        dict.fromkeys((workspace_known_hosts, user_known_hosts))
    )
    try:
        for known_hosts in known_hosts_paths:
            replace_scanned_host_keys(
                target_ip,
                scan,
                known_hosts_path=known_hosts,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProvisionError(
            f"Could not enroll the guest SSH host key for {target_ip}: {exc}"
        ) from exc
    print(
        "  ✓ Enrolled guest SSH host key in "
        + ", ".join(known_hosts_paths)
    )


def refresh_managed_guest_host_keys(
    target_ip: str,
    node_ip: str,
    user: str,
    hosted_key: Optional[str],
    *,
    dry_run: bool = False,
) -> None:
    """Refresh a saved guest's host key through its authenticated Proxmox node.

    The caller is responsible for matching the guest and node against saved
    provisioning metadata before using this helper. Unsaved or adopted guests
    must continue through explicit operator-confirmed host-key enrollment.
    """
    enroll_provisioned_guest_host_keys(
        target_ip,
        node_ip,
        user,
        _ssh_opts(hosted_key),
        dry_run=dry_run,
    )


__all__ = [
    "ProvisionError",
    "_build_guest_hostname",
    "_get_bridge_prefix_length",
    "_get_corosync_config",
    "_get_guest_gateway",
    "_get_host_gateway",
    "_get_host_nameservers",
    "_get_next_vmid",
    "_get_node_name",
    "_is_usable_nameserver",
    "_parse_corosync_config",
    "_list_proxmox_bridges",
    "_list_storage_names_for_content",
    "_list_storage_pools",
    "_resolve_public_key_path",
    "resolve_guest_ssh_key",
    "_resolve_storage_pool",
    "_ssh_opts",
    "_ssh_run",
    "_storage_pool_supports_content",
    "ensure_guest_ipv4_route",
    "enroll_provisioned_guest_host_keys",
    "refresh_managed_guest_host_keys",
    "_wait_for_guest_ssh",
    "auto_detect_bridge",
    "probe_proxmox_cluster",
    "probe_proxmox_host",
]
