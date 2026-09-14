#!/usr/bin/env python3
"""Proxmox LXC container provisioning via SSH.

Handles creating containers on a Proxmox host using pct/pveam CLI tools.
Designed to be called locally before remote_setup runs against the container.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import shlex
import time
from typing import Optional, cast

from lib.config import SetupConfig
from lib.proxmox_guest import (
    ProvisionError,
    _build_guest_hostname,
    _create_with_vmid_retry,
    _get_bridge_prefix_length,
    _get_guest_gateway,
    _get_host_nameservers,
    _get_next_vmid,
    _is_usable_nameserver,
    _resolve_public_key_path,
    _resolve_storage_pool,
    _ssh_opts,
    _ssh_run,
    _storage_pool_supports_content,
    _wait_for_guest_ssh,
    auto_detect_bridge,
    enroll_provisioned_guest_host_keys,
    guest_provisioning_locks,
)
from lib.types import NestedStrList, StrList
from lib.validation import parse_memory_mib


class ContainerAlreadyExists(Exception):
    """Raised when the target container already exists on the Proxmox node."""

def _build_container_hostname(target_ip: str, friendly_name: Optional[str]) -> str:
    """Derive a hostname for the container."""
    return _build_guest_hostname(target_ip, friendly_name, default_prefix="lxc")


def _wait_for_container_ssh(
    target_ip: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    timeout: int = 90,
    dry_run: bool = False,
) -> None:
    """Wait for sshd inside the new container to accept TCP connections."""
    _wait_for_guest_ssh(
        target_ip,
        node_ip,
        user,
        ssh_opts,
        timeout=timeout,
        dry_run=dry_run,
        label="Container",
    )


def _normalize_storage_specs(storage_specs: NestedStrList | None) -> list[StrList]:
    if not storage_specs:
        return []

    if isinstance(storage_specs[0], str):
        return [list(storage_specs)]  # type: ignore[list-item]

    return [list(spec) for spec in storage_specs]


def _get_storage_spec(storage_specs: NestedStrList | None, storage_type: str) -> Optional[StrList]:
    for spec in _normalize_storage_specs(storage_specs):
        if spec and spec[0] == storage_type:
            return spec
    return None


def check_container_exists(
    node_ip: str,
    target_ip: str,
    user: str = "root",
    hosted_key: Optional[str] = None,
    dry_run: bool = False
) -> bool:
    """Check if a container with the target IP already exists on the node.

    Checks all running/stopped containers' network config for the target IP.
    """
    opts = _ssh_opts(hosted_key)
    result = _ssh_run(
        node_ip, user, opts,
        "pct list | awk 'NR>1 {print $1}'",
        dry_run=dry_run
    )

    if dry_run:
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown error"
        raise ProvisionError(f"Failed to query containers on {node_ip}: {detail}")
    if not result.stdout.strip():
        return False

    for vmid in result.stdout.strip().split('\n'):
        vmid = vmid.strip()
        if not vmid:
            continue
        config_result = _ssh_run(
            node_ip, user, opts,
            f"pct config {vmid}",
            dry_run=dry_run
        )
        if config_result.returncode != 0:
            detail = (
                config_result.stderr or config_result.stdout or ""
            ).strip() or "unknown error"
            raise ProvisionError(
                f"Failed to inspect container {vmid} on {node_ip}: {detail}"
            )
        for line in config_result.stdout.split('\n'):
            if not line.startswith('net0:'):
                continue
            # net0 line example:
            #   net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1,type=veth
            match = re.search(r'(?:^|,)ip=([^,\s]+)', line)
            if not match:
                continue
            container_ip = match.group(1).split('/', 1)[0].strip()
            if container_ip == target_ip:
                print(f"  ✓ Container VMID {vmid} already exists with IP {target_ip}")
                return True

    return False

def _resolve_template_storage(
    root_pool: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False
) -> str:
    """Find a storage pool that supports vztmpl content.

    Prefer the root pool if it already supports templates; otherwise auto-select
    the first active template-capable pool.
    """
    if _storage_pool_supports_content(
        root_pool, "vztmpl", node_ip, user, ssh_opts, dry_run=dry_run
    ):
        print(f"  ✓ Using root storage pool for templates: {root_pool}")
        return root_pool

    result = _ssh_run(
        node_ip, user, ssh_opts,
        "pvesm status --content vztmpl 2>/dev/null",
        dry_run=dry_run
    )

    if dry_run:
        print(f"  [DRY-RUN] Would auto-select template storage pool")
        return root_pool

    for line in result.stdout.strip().split('\n')[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "active":
            pool = parts[0]
            print(f"  ✓ Template storage pool: {pool}")
            return pool

    raise ProvisionError("No template storage pool found on the Proxmox host")


def _parse_pveam_available(stdout: str, system: str) -> StrList:
    """Parse `pveam available` output and return matching template filenames.

    pveam available emits whitespace-separated columns like:
        section          template_name
        system           debian-13-standard_13.1-2_amd64.tar.zst
        system           ubuntu-24.04-standard_24.04-1_amd64.tar.zst

    Older or differently-formatted output may use `section/template`. We accept either.
    The match requires `<system>-<digits>` so `--base debian` doesn't pull in
    `debian-13-turnkey-*` images by accident.
    """
    system_lc = system.lower()
    pattern = re.compile(rf'^{re.escape(system_lc)}-\d')
    matches: StrList = []
    for raw in (stdout or "").split('\n'):
        line = raw.strip()
        if not line or line.startswith('---') or line.upper().startswith('NAME') or line.upper().startswith('SECTION'):
            continue
        # Pick the last whitespace-separated field, then strip any leading "section/" prefix.
        candidate = line.split()[-1]
        candidate = candidate.rsplit('/', 1)[-1]
        cand_lc = candidate.lower()
        if not pattern.match(cand_lc):
            continue
        # Exclude derivative distributions that share the base name but ship a
        # different OS (e.g. debian-12-turnkey-wordpress is a TurnKey appliance,
        # not a stock Debian image).
        if '-turnkey-' in cand_lc:
            continue
        matches.append(candidate)
    return matches


def _template_sort_key(name: str) -> tuple:
    """Extract a comparable version key from a template filename.

    e.g. debian-12-standard_12.7-1_amd64.tar.zst -> (12, (12, 7, 1))
    Templates that don't match fall back to lexical order at the end.
    """
    m = re.search(r'-(\d+)[-.][^_]*_([\d.]+)', name)
    if not m:
        return (0, (), name)
    major = int(m.group(1))
    sub = tuple(int(p) for p in m.group(2).split('.') if p.isdigit())
    return (major, sub, name)


def _resolve_template_name(
    base_arg: str,
    template_storage: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False
) -> str:
    """Resolve a base OS argument to a concrete template path.

    Queries pveam available on the host to find the latest matching template.
    """
    # Update template list
    _ssh_run(node_ip, user, ssh_opts, "pveam update", dry_run=dry_run)

    if dry_run:
        print(f"  [DRY-RUN] Would select the latest {base_arg} LXC template")
        return f"/var/lib/vz/template/cache/{base_arg}-standard_latest_amd64.tar.zst"

    # List available templates
    result = _ssh_run(
        node_ip, user, ssh_opts,
        "pveam available",
        dry_run=dry_run
    )

    system = base_arg.lower()
    candidates = _parse_pveam_available(result.stdout, system)

    if not candidates:
        # Also check what's already downloaded
        local_result = _ssh_run(
            node_ip, user, ssh_opts,
            f"pveam list {shlex.quote(template_storage)} 2>/dev/null",
            dry_run=dry_run
        )
        local_pattern = re.compile(rf'^{re.escape(system)}-\d')
        local_candidates: StrList = []
        for line in (local_result.stdout or "").strip().split('\n')[1:]:
            parts = line.split()
            if not parts:
                continue
            # pveam list rows typically show full storage path: local:vztmpl/<file>
            name = parts[0].rsplit('/', 1)[-1]
            if local_pattern.match(name.lower()):
                local_candidates.append(name)
        if local_candidates:
            local_candidates.sort(key=_template_sort_key)
            chosen = local_candidates[-1]
            template_path = f"/var/lib/vz/template/cache/{chosen}"
            if not template_storage.startswith("local"):
                template_path = f"{template_storage}:vztmpl/{chosen}"
            print(f"  ✓ Found downloaded template: {chosen}")
            return template_path

        raise ProvisionError(
            f"No template found matching '{base_arg}'. "
            f"Available templates can be listed with 'pveam available' on the Proxmox host"
        )

    # Pick the latest by version, not lexical order
    candidates.sort(key=_template_sort_key)
    template_name = candidates[-1]

    # Download if not already present
    print(f"  Downloading template: {template_name}")
    template_ref = f"{template_storage}:vztmpl/{template_name}"
    template_lock = hashlib.sha256(template_ref.encode("utf-8")).hexdigest()[:24]
    download_if_missing = (
        f"pveam list {shlex.quote(template_storage)} "
        "| awk 'NR > 1 {print $1}' "
        f"| grep -Fqx -- {shlex.quote(template_ref)} || "
        f"pveam download {shlex.quote(template_storage)} "
        f"{shlex.quote(template_name)}"
    )
    locked_download = shlex.join(
        [
            "flock",
            "--exclusive",
            f"/run/lock/infra-tools-template-{template_lock}.lock",
            "/bin/sh",
            "-c",
            download_if_missing,
        ]
    )
    result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        locked_download,
        dry_run=dry_run,
        timeout=1800,
    )
    if result.returncode != 0:
        raise ProvisionError(
            f"Template download failed for {template_name} on storage {template_storage}. "
            f"Error: {result.stderr.strip() or 'unknown error'}"
        )

    template_path = f"/var/lib/vz/template/cache/{template_name}"
    if not template_storage.startswith("local"):
        template_path = f"{template_storage}:vztmpl/{template_name}"

    print(f"  ✓ Template path: {template_path}")
    return template_path


def _upload_pubkey_to_host(
    pub_path: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool,
) -> Optional[str]:
    """Upload the local public key to a temp file on the Proxmox host.

    Returns the remote temp path so the caller can pass it to `pct create
    --ssh-public-keys`. Returns None on dry runs.
    """
    if dry_run:
        return "/tmp/infra_tools_pubkey.dryrun"

    try:
        with open(pub_path, "r", encoding="utf-8") as fh:
            pub_contents = fh.read()
    except OSError as exc:
        raise ProvisionError(f"Failed to read public key {pub_path}: {exc}")

    # mktemp on the host, then write the contents through ssh stdin.
    mk = _ssh_run(
        node_ip, user, ssh_opts,
        "mktemp /tmp/infra_tools_pubkey.XXXXXX",
        dry_run=False,
    )
    if mk.returncode != 0 or not mk.stdout.strip():
        raise ProvisionError(
            f"Failed to allocate temp file for SSH key on {node_ip}: "
            f"{(mk.stderr or '').strip() or 'mktemp failed'}"
        )
    remote_path = mk.stdout.strip()

    proc = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        f"cat > {shlex.quote(remote_path)} && chmod 600 {shlex.quote(remote_path)}",
        dry_run=False,
        input_data=pub_contents,
    )
    if proc.returncode != 0:
        raise ProvisionError(
            f"Failed to upload public key to {node_ip}: "
            f"{proc.stderr.strip() or 'unknown error'}"
        )
    return remote_path

def _create_container(
    vmid: int,
    target_ip: str,
    template_path: str,
    memory_mb: int,
    cores: int,
    root_pool: str,
    storage_amount: str,
    cidr_prefix: str,
    bridge: str,
    gateway: str,
    nameservers: StrList,
    hostname: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    privileged: bool = False,
    dry_run: bool = False,
    ssh_pubkey_remote_path: Optional[str] = None,
    ipv6_cidr: Optional[str] = None,
    gateway6: Optional[str] = None,
) -> None:
    """Create and start the LXC container on the Proxmox host."""
    unprivileged = "0" if privileged else "1"

    network_parts = [
        "name=eth0",
        f"bridge={bridge}",
        f"ip={target_ip}/{cidr_prefix}",
        f"gw={gateway}",
    ]
    if ipv6_cidr:
        network_parts.append(f"ip6={ipv6_cidr}")
        if gateway6:
            network_parts.append(f"gw6={gateway6}")
    network_parts.append("type=veth")

    cmd_parts = [
        f"pct create {vmid} {shlex.quote(template_path)}",
        f"--hostname {shlex.quote(hostname)}",
        f"--memory {memory_mb}",
        f"--cores {cores}",
        f"--rootfs {shlex.quote(root_pool)}:{shlex.quote(storage_amount)}",
        f"--net0 {shlex.quote(','.join(network_parts))}",
        f"--nameserver {shlex.quote(' '.join(nameservers))}",
        f"--unprivileged {unprivileged}",
        "--onboot 1",
        "--start 1",
    ]
    if ssh_pubkey_remote_path:
        cmd_parts.insert(-1, f"--ssh-public-keys {shlex.quote(ssh_pubkey_remote_path)}")

    cmd = " ".join(cmd_parts)

    result = _ssh_run(node_ip, user, ssh_opts, cmd, dry_run=dry_run)
    if result.returncode != 0:
        raise ProvisionError(
            f"Container creation failed for VMID {vmid}: {result.stderr.strip() or result.stdout.strip() or 'unknown error'}"
        )

    if not dry_run:
        # Verify it started
        status_result = _ssh_run(
            node_ip, user, ssh_opts,
            f"pct status {vmid}",
            dry_run=dry_run
        )
        if "running" not in status_result.stdout:
            raise ProvisionError(
                f"Container {vmid} was created but is not running. "
                f"Status: {status_result.stdout.strip()}"
            )

    print(f"  ✓ Container {vmid} created and started ({hostname}, {target_ip})")


def provision_container(config: SetupConfig) -> None:
    """Reserve this guest identity and provision an LXC container."""
    if config.dry_run:
        return _provision_container_locked(config)
    node_ip = cast(str, config.hosted_node)
    static_ipv4 = ipaddress.ip_interface(config.static_ipv4) if config.static_ipv4 else None
    target_ip = str(static_ipv4.ip) if static_ipv4 else config.host
    hostname = config.system_hostname or _build_container_hostname(
        target_ip, config.friendly_name
    )
    with guest_provisioning_locks(node_ip, target_ip, hostname):
        return _provision_container_locked(config)


def _provision_container_locked(config: SetupConfig) -> None:
    """Orchestrate LXC container provisioning on a Proxmox host.

    Args:
        config: SetupConfig with hosted_node, container_memory, container_storage, etc.

    Raises:
        ContainerAlreadyExists: If a container with the target IP already exists.
        ProvisionError: If provisioning fails at any step.
    """
    node_ip = cast(str, config.hosted_node)
    try:
        memory_mb = parse_memory_mib(
            cast(str, config.container_memory),
            "LXC memory",
        )
    except ValueError as exc:
        raise ProvisionError(str(exc)) from exc
    storage_specs = cast(NestedStrList, config.container_storage)
    user: str = config.hosted_user
    static_ipv4 = ipaddress.ip_interface(config.static_ipv4) if config.static_ipv4 else None
    if static_ipv4 is not None and not isinstance(static_ipv4, ipaddress.IPv4Interface):
        raise ProvisionError("LXC provisioning requires an IPv4 setup target")
    target_ip = str(static_ipv4.ip) if static_ipv4 else config.host
    ssh_opts = _ssh_opts(config.hosted_key)
    dry_run = config.dry_run

    if dry_run:
        hostname = config.system_hostname or _build_container_hostname(
            target_ip, config.friendly_name
        )
        root_spec = _get_storage_spec(storage_specs, "root")
        template_spec = _get_storage_spec(storage_specs, "template")
        privileged = config.machine_type == "privileged"

        print("[DRY RUN] Would provision LXC container:")
        print(f"  Proxmox node: {node_ip}")
        print(f"  Target IP: {target_ip}")
        if config.static_ipv6:
            print(f"  Static IPv6: {config.static_ipv6}")
        if config.network_gateway4:
            print(f"  IPv4 gateway: {config.network_gateway4}")
        else:
            print("  IPv4 gateway: auto-detect from selected Proxmox bridge")
        if config.network_gateway6:
            print(f"  IPv6 gateway: {config.network_gateway6}")
        if config.network_dns:
            print(f"  DNS servers: {', '.join(config.network_dns)}")
        else:
            print("  DNS servers: auto-detect from Proxmox node")
        print(f"  Hostname: {hostname}")
        print(f"  Memory: {memory_mb} MiB")
        print(f"  Cores: {config.container_cores}")
        print(f"  Container type: {'privileged' if privileged else 'unprivileged'}")
        print(f"  Root storage: {root_spec[1] if root_spec else 'N/A'} ({root_spec[2] if root_spec else 'N/A'})")
        if template_spec:
            print(f"  Template storage: {template_spec[1]}")
        else:
            print(f"  Template storage: auto-detect")
        print(f"  Base OS: {config.container_base}")
        print(f"  Storage specs: {storage_specs}")
        return

    # Resolve hostname
    hostname = config.system_hostname or _build_container_hostname(
        target_ip, config.friendly_name
    )
    print(f"  Hostname: {hostname}")

    # Auto-detect bridge
    bridge = auto_detect_bridge(
        node_ip,
        user,
        config.hosted_key,
        preferred_bridge=getattr(config, "hosted_bridge", None),
    )

    # Detect gateway and nameservers
    if static_ipv4 is None:
        raise ProvisionError("LXC provisioning requires an IPv4 setup target")
    gateway = config.network_gateway4 or _get_guest_gateway(
        node_ip,
        user,
        ssh_opts,
        bridge,
        static_ipv4,
    )
    nameservers = list(config.network_dns or _get_host_nameservers(
        node_ip,
        user,
        ssh_opts,
        bridge=bridge,
        fallback_gateway=gateway,
    ))
    config.hosted_bridge = bridge
    config.network_gateway4 = gateway
    config.network_dns = nameservers

    if check_container_exists(
        node_ip, target_ip, user, config.hosted_key, dry_run=dry_run
    ):
        raise ContainerAlreadyExists(
            f"Container with IP {target_ip} already exists on {node_ip}"
        )

    root_spec = _get_storage_spec(storage_specs, "root")
    if not root_spec:
        raise ProvisionError("Missing root storage specification")

    template_spec = _get_storage_spec(storage_specs, "template")

    root_pool_arg = root_spec[1]
    storage_amount = root_spec[2]

    root_pool = _resolve_storage_pool(
        root_pool_arg, node_ip, user, ssh_opts, "images,rootdir"
    )

    # Template storage
    if template_spec:
        template_pool_arg = template_spec[1]
        if template_pool_arg == "auto":
            template_storage = _resolve_storage_pool(
                "auto", node_ip, user, ssh_opts, "vztmpl"
            )
        else:
            template_storage = _resolve_storage_pool(
                template_pool_arg, node_ip, user, ssh_opts, "vztmpl"
            )
    else:
        template_storage = _resolve_template_storage(root_pool, node_ip, user, ssh_opts)

    # Resolve and download template
    template_path = _resolve_template_name(
        config.container_base, template_storage, node_ip, user, ssh_opts
    )

    cidr_prefix = (
        str(static_ipv4.network.prefixlen)
        if static_ipv4
        else _get_bridge_prefix_length(
            node_ip, user, ssh_opts, bridge, dry_run=dry_run
        )
    )

    # Get next VMID
    vmid = _get_next_vmid(node_ip, user, ssh_opts)

    # Determine privileged/unprivileged
    privileged = config.machine_type == "privileged"

    # Bootstrap SSH access into the new container so the subsequent remote_setup phase
    # can connect as root via the user's SSH key. Without this, pct create leaves root
    # with no usable credentials and the orchestration would hang on password auth.
    pub_path = _resolve_public_key_path(config.ssh_key)
    remote_pubkey_path: Optional[str] = None
    if pub_path:
        print(f"  Uploading public key for container access: {pub_path}")
        remote_pubkey_path = _upload_pubkey_to_host(
            pub_path, node_ip, user, ssh_opts, dry_run=dry_run
        )
    else:
        print(
            "  ⚠ No SSH public key found alongside --key; the new container will have "
            "no root credentials and remote setup will be unable to connect."
        )

    try:
        # pvesh's nextid endpoint reports an available ID but does not reserve
        # it. Other guest provisions may claim it before pct create does.
        vmid = _create_with_vmid_retry(
            _create_container,
            lambda: _get_next_vmid(node_ip, user, ssh_opts),
            {
                "vmid": vmid,
                "target_ip": target_ip,
                "template_path": template_path,
                "memory_mb": memory_mb,
                "cores": config.container_cores,
                "root_pool": root_pool,
                "storage_amount": storage_amount,
                "cidr_prefix": cidr_prefix,
                "bridge": bridge,
                "gateway": gateway,
                "nameservers": nameservers,
                "hostname": hostname,
                "node_ip": node_ip,
                "user": user,
                "ssh_opts": ssh_opts,
                "privileged": privileged,
                "dry_run": dry_run,
                "ssh_pubkey_remote_path": remote_pubkey_path,
                "ipv6_cidr": config.static_ipv6,
                "gateway6": config.network_gateway6,
            },
        )

        # Wait for sshd in the container to come up before handing off to remote_setup.
        if remote_pubkey_path:
            _wait_for_container_ssh(
                target_ip, node_ip, user, ssh_opts, dry_run=dry_run
            )
            enroll_provisioned_guest_host_keys(
                target_ip,
                node_ip,
                user,
                ssh_opts,
                dry_run=dry_run,
            )
    finally:
        if remote_pubkey_path and not dry_run:
            _ssh_run(
                node_ip, user, ssh_opts,
                f"rm -f {shlex.quote(remote_pubkey_path)}",
                dry_run=False,
            )
