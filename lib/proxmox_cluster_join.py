"""Join an empty Proxmox node using a registered cluster member's root SSH trust."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
from dataclasses import dataclass, replace
from typing import Any

from lib.cache import get_proxmox_setup_ssh_identity, update_proxmox_setup_ssh_identity
from lib.proxmox_hosts import ProxmoxHost, update_proxmox_host_ssh_identity
from lib.proxmox_preflight import SUPPORTED_PVE_MAJOR
from lib.remote_utils import CommandTimeoutError, run
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_process_timeout
from lib.validation import validate_filesystem_path, validate_network_ip, validate_no_control_characters
from lib.validators import validate_host
from lib.workspace import get_known_hosts_path


class ProxmoxClusterJoinError(Exception):
    """Cluster join was refused, failed, or could not be verified."""


# These scripts run on Proxmox, which need not have Basaltwater installed.
_NODE_HELPERS = '''from __future__ import annotations
import hashlib
import json
import socket
import subprocess
from pathlib import Path

def api(path, *options):
    result = subprocess.run(["pvesh", "get", path, *options, "--output-format", "json"],
                            check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)

def snapshot():
    name = socket.gethostname().split(".", 1)[0]
    status = api("/cluster/status")
    clustered = any(row.get("type") == "cluster" for row in status)
    tasks = api(f"/nodes/{name}/tasks", "--source", "active", "--typefilter", "clusterjoin")
    if not isinstance(tasks, list) or not all(isinstance(row, dict) for row in tasks):
        raise RuntimeError("Invalid native cluster join task inventory")
    # The active-task file also contains completed tasks awaiting archival.
    tasks = [row for row in tasks if type(row.get("endtime")) is not int
             or row["endtime"] <= 0 or row.get("status") == "RUNNING"]
    ca = Path("/etc/pve/pve-root-ca.pem")
    return {
        "name": name,
        "version": subprocess.run(["pveversion"], check=True, capture_output=True,
                                  text=True, timeout=30).stdout.strip(),
        "status": status,
        "guests": api("/cluster/resources", "--type", "vm"),
        "tasks": tasks,
        "configured": Path("/etc/pve/corosync.conf").exists(),
        "ca": hashlib.sha256(ca.read_bytes()).hexdigest() if ca.exists() else None,
        "join_info": api("/cluster/config/join") if clustered else None,
    }
'''
_INSPECT_SCRIPT = _NODE_HELPERS + '\nprint(json.dumps(snapshot()))\n'
_TICKET_SCRIPT = (
    "use PVE::Cluster; use PVE::AccessControl; use PVE::Tools; use Digest::SHA qw(sha256_hex); "
    "PVE::Cluster::cfs_update(1); PVE::Cluster::check_cfs_quorum(); "
    "die qq(Cluster identity changed\\n) unless "
    "sha256_hex(PVE::Tools::file_get_contents('/etc/pve/pve-root-ca.pem')) eq $ARGV[0]; "
    "print PVE::AccessControl::assemble_ticket('root@pam'), qq(\\n);"
)
_JOIN_SCRIPT = _NODE_HELPERS + '''
import fcntl
import os
import sys

request = json.load(sys.stdin)
descriptor = os.open("/run/lock/basaltwater-setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
with os.fdopen(descriptor, "w") as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Another setup or maintenance operation holds the node lock")
    state = snapshot()
    if (state["name"] != request["node"] or state["ca"] != request["target_ca"]
            or not state["version"].startswith("pve-manager/" + str(request["major"]) + ".")
            or state["configured"] or any(row.get("type") == "cluster" for row in state["status"])
            or state["guests"] or state["tasks"]):
        sys.exit("Joining node state changed; inspect it before retrying")
    # Proxmox's synchronous CLI worker reads a non-terminal password from stdin.
    # Keep the ticket out of argv, environment variables, files, and task labels.
    result = subprocess.run(request["command"], input=request["ticket"] + "\\n", text=True, cwd="/", check=False)
    if result.returncode:
        sys.exit(result.returncode)
    state = snapshot()
    clusters = [row for row in state["status"] if row.get("type") == "cluster"]
    if (state["ca"] != request["cluster_ca"] or len(clusters) != 1
            or clusters[0].get("quorate") != 1):
        sys.exit("Native join completed but cluster identity or quorum could not be verified")
    print("Basaltwater: native cluster join and cluster CA verified")
'''
_TICKET_RE = re.compile(r"PVE:root@pam:[A-Fa-f0-9]{8}::[A-Za-z0-9+/=_-]+")
_FINGERPRINT_RE = re.compile(r"(?:[A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}")


def _redact(text: str) -> str:
    return re.sub(r"PVE:[^\s\"']+", "[redacted Proxmox ticket]", text)


def _execute(host: ProxmoxHost, command: list[str], *, input_data: str | None = None,
             timeout: int = 120, fresh: bool = False, private: bool = False,
             workspace: str | None = None) -> str:
    known_hosts = get_known_hosts_path(workspace)
    ssh = build_ssh_command(
        host.address, host.user, host.ssh_key, remote_command=shlex.join(command),
        known_hosts_path=known_hosts,
        control_path="none" if fresh else get_ssh_control_path(
            host.address, host.user, host.ssh_key, known_hosts_path=known_hosts),
    )
    # A forced PTY can echo stdin or keep JSON input waiting for terminal EOF.
    # The join needs neither the controller's forwarded agent nor port forwards.
    ssh[1:1] = ["-T", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes"]
    try:
        result = run(ssh, check=False, capture_output=True, input_data=input_data,
                     timeout=timeout if input_data is not None else ssh_process_timeout(timeout),
                     display_cmd=f"SSH Proxmox cluster operation on {host.name}")
    except (OSError, CommandTimeoutError):
        raise ProxmoxClusterJoinError(f"SSH operation on {host.name} did not complete") from None
    if result.returncode:
        detail = "" if private else _redact(result.stderr or result.stdout or "")[-1000:].strip()
        raise ProxmoxClusterJoinError(f"Proxmox operation on {host.name} failed" + (f": {detail}" if detail else ""))
    return result.stdout or ""


def _inspect(host: ProxmoxHost, *, fresh: bool = False, workspace: str | None = None) -> dict[str, Any]:
    output = _execute(host, ["python3", "-c", _INSPECT_SCRIPT], fresh=fresh, workspace=workspace)
    try:
        state = json.loads(output)
        if not isinstance(state, dict) or not validate_host(state.get("name")) or "." in state["name"]:
            raise ValueError("Invalid node name")
        if not isinstance(state.get("version"), str) or not re.match(
                rf"^pve-manager/{SUPPORTED_PVE_MAJOR}\.\d+\.\d+(?:/|\s|$)", state["version"]):
            raise ValueError(f"Requires Proxmox VE {SUPPORTED_PVE_MAJOR}.x on both nodes")
        for field in ("status", "guests", "tasks"):
            if not isinstance(state.get(field), list) or not all(isinstance(row, dict) for row in state[field]):
                raise ValueError(f"Invalid {field} inventory")
        for row in state["status"]:
            if row.get("type") == "node":
                if not validate_host(row.get("name")) or row.get("online") not in (0, 1):
                    raise ValueError("Invalid node membership inventory")
                if row.get("ip") is not None:
                    if not isinstance(row["ip"], str):
                        raise ValueError("Invalid node IP")
                    row["ip"] = validate_network_ip(row["ip"], "Proxmox node IP")
            elif row.get("type") != "cluster":
                raise ValueError("Invalid cluster status entry")
        if type(state.get("configured")) is not bool:
            raise ValueError("Invalid cluster configuration state")
        if state.get("ca") is not None and (not isinstance(state["ca"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", state["ca"])):
            raise ValueError("Invalid cluster CA digest")
        _local_node(state)
        _cluster(state)
        return state
    except (ValueError, TypeError, KeyError) as exc:
        raise ProxmoxClusterJoinError(f"Invalid Proxmox inspection on {host.name}: {exc}") from exc


def _local_node(state: dict[str, Any]) -> dict[str, Any]:
    nodes = [row for row in state["status"] if row.get("type") == "node" and row.get("local") == 1]
    if len(nodes) != 1 or nodes[0].get("name") != state["name"] or nodes[0].get("online") != 1:
        raise ProxmoxClusterJoinError("Unable to verify the local Proxmox node identity and online state")
    if not isinstance(nodes[0].get("ip"), str):
        raise ProxmoxClusterJoinError("Unable to verify the local Proxmox IP")
    validate_network_ip(nodes[0]["ip"], "Proxmox node IP")
    return nodes[0]


def _cluster(state: dict[str, Any]) -> dict[str, Any] | None:
    clusters = [row for row in state["status"] if row.get("type") == "cluster"]
    if not clusters:
        return None
    if (len(clusters) != 1 or not isinstance(clusters[0].get("name"), str)
            or not clusters[0]["name"] or clusters[0].get("quorate") != 1
            or not state["configured"] or not state["ca"]):
        raise ProxmoxClusterJoinError("Cluster identity or quorum could not be verified")
    validate_no_control_characters(clusters[0]["name"], "Cluster name")
    return clusters[0]


def _same_cluster(target: dict[str, Any], member: dict[str, Any]) -> bool:
    left, right = _cluster(target), _cluster(member)
    if not left or not right or left["name"] != right["name"] or target["ca"] != member["ca"]:
        return False
    for state, peer in ((target, member), (member, target)):
        peers = [row for row in state["status"] if row.get("type") == "node" and row.get("name") == peer["name"]]
        if len(peers) != 1 or peers[0].get("online") != 1 or peers[0].get("ip") != _local_node(peer)["ip"]:
            return False
    return True


def parse_cluster_links(values: list[str]) -> dict[int, str]:
    links = {}
    for value in values:
        index, separator, address = value.partition("=")
        if not separator or not re.fullmatch(r"[0-7]", index) or int(index) in links:
            raise ValueError("Each --link must be a unique INDEX=IP with an index from 0 to 7")
        address = validate_network_ip(address, "Corosync link address")
        parsed = ipaddress.ip_address(address)
        if parsed.is_loopback or parsed.is_multicast or parsed.is_unspecified:
            raise ValueError("Corosync links require a usable node IP")
        links[int(index)] = address
    return links


@dataclass(frozen=True)
class _JoinPlan:
    node: str
    node_ip: str
    member: str
    target_ca: str | None
    cluster: str
    cluster_ca: str
    command: tuple[str, ...]
    already_joined: bool


def _plan(target: dict[str, Any], member: dict[str, Any], address: str, links: dict[int, str]) -> _JoinPlan:
    cluster = _cluster(member)
    if not cluster:
        raise ProxmoxClusterJoinError("The --via node must already belong to a quorate cluster")
    if target["name"] == member["name"] or _local_node(target)["ip"] == _local_node(member)["ip"]:
        raise ProxmoxClusterJoinError("Joining node and cluster member must be different nodes")
    joined = _same_cluster(target, member)
    if target["tasks"]:
        raise ProxmoxClusterJoinError("A native cluster join is already active; inspect its task before retrying")
    if not joined:
        if target["configured"] or _cluster(target):
            raise ProxmoxClusterJoinError("Joining node already has cluster configuration; automatic rejoining is refused")
        if target["guests"]:
            raise ProxmoxClusterJoinError("Joining node contains guests; Proxmox requires an empty node")
        if any(row.get("name") == target["name"] for row in member["status"] if row.get("type") == "node"):
            raise ProxmoxClusterJoinError("Cluster already lists the joining node; inspect the previous join before retrying")
        if any(row.get("ip") == _local_node(target)["ip"] for row in member["status"] if row.get("type") == "node"):
            raise ProxmoxClusterJoinError("Joining node's IP is already used by a cluster member")
    info = member.get("join_info")
    if not isinstance(info, dict) or not isinstance(info.get("nodelist"), list):
        raise ProxmoxClusterJoinError("Cluster join information is unavailable")
    if not all(isinstance(row, dict) and validate_host(row.get("name")) for row in info["nodelist"]):
        raise ProxmoxClusterJoinError("Invalid configured cluster membership")
    if not joined and any(row["name"] == target["name"] for row in info["nodelist"]):
        raise ProxmoxClusterJoinError("Cluster already configures the joining node; inspect the previous join before retrying")
    peers = [row for row in info["nodelist"] if isinstance(row, dict) and row.get("name") == member["name"]]
    if len(peers) != 1 or not isinstance(peers[0].get("pve_fp"), str) or not _FINGERPRINT_RE.fullmatch(peers[0]["pve_fp"]):
        raise ProxmoxClusterJoinError("Unable to obtain the enrolled member's API certificate fingerprint")
    required_links = {int(key[4]) for key in peers[0] if re.fullmatch(r"ring[0-7]_addr", key)}
    if not joined:
        if not links and required_links == {0}:
            links = {0: validate_network_ip(_local_node(target)["ip"])}
        if not required_links or set(links) != required_links:
            raise ProxmoxClusterJoinError("Specify every cluster network link with --link INDEX=IP")
    command = ["pvecm", "add", address, "--fingerprint", peers[0]["pve_fp"]]
    for index, ip in sorted(links.items()):
        command += [f"--link{index}", ip]
    return _JoinPlan(target["name"], _local_node(target)["ip"], member["name"], target["ca"], cluster["name"], member["ca"],
                     tuple(command), joined)


def run_cluster_join(target: ProxmoxHost, member: ProxmoxHost, *, workspace: str | None = None,
                     links: list[str] | None = None, join_address: str | None = None,
                     timeout: int = 900, dry_run: bool = False, yes: bool = False) -> int:
    """Inspect, confirm, and run the native API join without collecting a password or OTP."""
    for host in (target, member):
        if host.user != "root" or not validate_host(host.address):
            raise ValueError("Cluster joining requires registered nodes with root SSH access")
        if host.ssh_key:
            validate_filesystem_path(host.ssh_key)
    if workspace:
        validate_filesystem_path(workspace)
    address = join_address or member.address
    if not validate_host(address):
        # Native pvecm also accepts IPv6 literals for the API endpoint.
        address = validate_network_ip(address, "cluster join address")
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise ValueError("Join timeout must be from 1 to 7200 seconds")
    parsed_links = parse_cluster_links(links or [])
    member_state = _inspect(member, workspace=workspace)
    try:
        target_state = _inspect(target, workspace=workspace)
    except ProxmoxClusterJoinError:
        if target.ssh_key == member.ssh_key:
            raise
        # A prior native join may have replaced standalone SSH authorization.
        target_state = _inspect(replace(target, ssh_key=member.ssh_key), workspace=workspace)
    plan = _plan(target_state, member_state, address, parsed_links)
    saved_identity = get_proxmox_setup_ssh_identity(target.address, workspace)
    print(f"Join {target.name} ({plan.node}) to cluster {plan.cluster} via {member.name}")
    print("  Authentication: temporary root API ticket obtained through enrolled SSH")
    print(f"  Native command: {shlex.join(plan.command)}")
    if dry_run:
        print("  Dry run: inspections only; no ticket issued, cluster changes, or registry writes")
        return 0
    if not plan.already_joined:
        print("  Joining replaces /etc/pve configuration and inherits the cluster's storage configuration")
        if not yes:
            try:
                answer = input("Type 'yes' to join this node: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer != "yes":
                print("Aborted.")
                return 1
        current = _plan(_inspect(target, workspace=workspace), _inspect(member, workspace=workspace), address, parsed_links)
        if current != plan:
            raise ProxmoxClusterJoinError("Node or cluster identity changed after inspection; retry the preview")
        if get_proxmox_setup_ssh_identity(target.address, workspace) != saved_identity:
            raise ProxmoxClusterJoinError("Saved setup SSH identity changed after inspection; retry the preview")
        ticket = _execute(member, ["perl", "-e", _TICKET_SCRIPT, plan.cluster_ca], private=True, workspace=workspace).strip()
        if not _TICKET_RE.fullmatch(ticket):
            raise ProxmoxClusterJoinError("Unable to obtain a valid root API ticket from the enrolled member")
        request = {"node": plan.node, "major": SUPPORTED_PVE_MAJOR, "target_ca": plan.target_ca,
                   "cluster_ca": plan.cluster_ca, "command": plan.command, "ticket": ticket}
        try:
            output = _execute(target, ["python3", "-c", _JOIN_SCRIPT],
                              input_data=json.dumps(request), timeout=timeout, workspace=workspace)
        except ProxmoxClusterJoinError as exc:
            raise ProxmoxClusterJoinError(
                f"{exc}. Join may have partly completed; inspect native Proxmox tasks and both nodes before retrying"
            ) from exc
        finally:
            del ticket, request
        if output.strip():
            print(_redact(output).strip())
    # Joining replaces the standalone authorized_keys database. Verify a fresh
    # connection using the enrolled cluster identity before saving that identity.
    cluster_identity = replace(target, ssh_key=member.ssh_key, facts=None)
    try:
        verified_target = _inspect(cluster_identity, fresh=True, workspace=workspace)
        verified_member = _inspect(member, workspace=workspace)
    except ProxmoxClusterJoinError as exc:
        raise ProxmoxClusterJoinError(f"Join access could not be verified; inspect native tasks and cluster SSH authorization: {exc}") from exc
    if (not _same_cluster(verified_target, verified_member) or verified_member["ca"] != plan.cluster_ca
            or verified_target["name"] != plan.node or _local_node(verified_target)["ip"] != plan.node_ip
            or verified_member["name"] != plan.member):
        raise ProxmoxClusterJoinError("Join outcome could not be verified on both nodes; inspect the native join task")
    update_proxmox_host_ssh_identity(target, cluster_identity.ssh_key, workspace)
    if saved_identity is not None:
        update_proxmox_setup_ssh_identity(target.address, saved_identity[1], cluster_identity.ssh_key, workspace)
    print(f"  ✓ {plan.node} is online in {plan.cluster}; registered SSH access verified")
    return 0
