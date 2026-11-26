"""Rolling update helpers for ordered multi-node maintenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Optional

from lib.atomic_io import write_json_atomic
from lib.cache import load_setup_command
from lib.config import SetupConfig
from lib.debian_rolling_update import DEBIAN_RELEASES, DEBIAN_UPDATE_BLOCKED, debian_update_command
from lib.operation_state import OperationStateStore
from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_maintenance import ProxmoxMaintenanceReport, collect_maintenance_report
from lib.proxmox_preflight import PROXMOX_PACKAGE_POLICY_COMMAND, parse_proxmox_package_policy
from lib.remote_utils import run
from lib.setup_common import prepare_validated_runtime_config
from lib.setup_reboot import _REMOTE_HOST_RESTART, _validated_boot_id
from lib.ssh_utils import build_ssh_command, ssh_batch_mode, ssh_process_timeout
from lib.workspace import get_workspace_dir, set_workspace_dir


_LOCALHOSTS = {"localhost", "127.0.0.1", "::1"}
_SSH_POLL_INTERVAL = 5
_DEBIAN_INSTALL_TIMEOUT = 3600
# Wait for systemd's initial transaction before looking for a new boot ID.
# The role-specific audit checks health separately, including degraded states.
_BOOT_ID_COMMAND = "systemctl is-system-running --wait >/dev/null; cat /proc/sys/kernel/random/boot_id"
_PROXMOX_RELEASE_CHECK = """import sys
sys.path.insert(0, '/opt/basaltwater')
from lib.proxmox_preflight import check_proxmox_installation, check_proxmox_package_state
try:
    check_proxmox_installation()
    check_proxmox_package_state()
except (OSError, RuntimeError, ValueError) as exc:
    sys.exit(str(exc))
"""


@dataclass
class ClusterUpdateResult:
    target: str
    host: Optional[str]
    status: str
    details: str = ""
    reboot_required: bool = False
    rebooted: bool = False


@dataclass(frozen=True)
class _UpdateTarget:
    name: str
    config: SetupConfig
    fingerprint: str
    debian_codename: str | None


def _ssh_result(
    config: SetupConfig,
    remote_command: str,
    *,
    connect_timeout: int = 5,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    batch_mode = ssh_batch_mode()
    process_timeout = timeout if timeout is not None else ssh_process_timeout(30, batch_mode=batch_mode)
    return run(
        build_ssh_command(
            config.host,
            "root",
            config.ssh_key,
            remote_command=remote_command,
            batch_mode=batch_mode,
            connect_timeout=connect_timeout,
            server_alive_interval=connect_timeout,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=process_timeout,
        preserve_terminal=not batch_mode,
    )


def _maintenance_report(target: str, config: SetupConfig) -> ProxmoxMaintenanceReport:
    """Collect the shared Proxmox safety report for a saved setup target."""
    return collect_maintenance_report(
        ProxmoxHost(
            name=target,
            address=config.host,
            user="root",
            ssh_key=config.ssh_key,
        )
    )


def _maintenance_errors(report: ProxmoxMaintenanceReport) -> str:
    return "; ".join(report.errors) or "unknown maintenance preflight failure"


def _command_error(result: subprocess.CompletedProcess[str], message: str) -> RuntimeError:
    # Keep SSH and package logs out of the durable controller checkpoint.
    if result.stderr:
        print(result.stderr.strip())
    return RuntimeError(f"{message} (exit {result.returncode})")


def _debian_probe(config: SetupConfig, action: str, codename: str = "") -> tuple[str, int | None, bool]:
    result = _ssh_result(config, debian_update_command(action, codename), timeout=90)
    if result.returncode:
        raise _command_error(result, "Debian health or package check failed")
    try:
        report = json.loads(result.stdout)
        release, packages, reboot_required = (report[key] for key in ("codename", "packages", "reboot_required"))
        if (release not in DEBIAN_RELEASES or (codename and codename != release)
                or type(reboot_required) is not bool
                or (action == "plan" and (type(packages) is not int or packages < 0))
                or (action == "health" and packages is not None)):
            raise ValueError("Unexpected Debian probe fields")
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("Invalid Debian probe response") from exc
    return release, packages, reboot_required


def _print_package_preview(target: str, config: SetupConfig) -> None:
    installed = candidate = None
    try:
        result = _ssh_result(config, PROXMOX_PACKAGE_POLICY_COMMAND)
    except (OSError, subprocess.SubprocessError):
        pass
    else:
        if result.returncode == 0:
            installed, candidate = parse_proxmox_package_policy(result.stdout or "")
    print(
        f"  {target} [{config.host}] pve-manager (cached APT) — "
        f"installed {installed or 'unavailable'}, candidate {candidate or 'unavailable'}"
    )


_POLICY_PROBE = """import json, os, re, stat
def read(path, required=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        if required:
            raise
        return ''
    with os.fdopen(fd) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('unsafe policy file')
        value = stream.read(1048577)
    if len(value) > 1048576:
        raise ValueError('policy file too large')
    return value
ha = read('/etc/pve/ha/resources.cfg')
storage = read('/etc/pve/storage.cfg', required=True)
ceph = read('/etc/pve/ceph.conf') or read('/etc/ceph/ceph.conf')
print(json.dumps({'ha': any(line.strip() and not line.lstrip().startswith('#') for line in ha.splitlines()),
                  'ceph': bool(ceph) or bool(re.search(r'(?m)^\\s*(?:rbd|cephfs)\\s*:', storage))}))
"""


def _rolling_policy(config: SetupConfig) -> None:
    """Reject topologies requiring operator-owned HA/Ceph maintenance."""
    result = _ssh_result(config, "python3 -c " + shlex.quote(_POLICY_PROBE))
    if result.returncode != 0:
        raise RuntimeError("Could not establish HA/Ceph maintenance policy")
    try:
        policy = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid HA/Ceph policy probe") from exc
    if not isinstance(policy, dict) or set(policy) != {"ha", "ceph"} or any(type(value) is not bool for value in policy.values()):
        raise RuntimeError("Invalid HA/Ceph policy probe")
    if policy["ha"] or policy["ceph"]:
        raise RuntimeError("HA/Ceph requires operator-managed maintenance; rolling-update does not evacuate or manage HA/Ceph")


def _preflight(target: str, config: SetupConfig, *, evacuated: bool = True) -> ProxmoxMaintenanceReport:
    report = _maintenance_report(target, config)
    if not report.healthy:
        raise RuntimeError("Maintenance preflight failed: " + _maintenance_errors(report))
    if evacuated and not report.reboot_safe:
        raise RuntimeError("Evacuate or shut down guests before updating: " + "; ".join(report.reboot_blockers()))
    _rolling_policy(config)
    if evacuated and report.clustered:
        result = _ssh_result(config, "LC_ALL=C pvecm status")
        if result.returncode:
            raise _command_error(result, "Could not confirm Proxmox reboot quorum")
        status = result.stdout or ""
        if not re.search(r"^Quorate:[ \t]+Yes[ \t]*$", status, re.M):
            raise RuntimeError("Proxmox cluster is no longer confirmed quorate")
        total = re.search(r"^Total votes:[ \t]+(\d+)[ \t]*$", status, re.M)
        quorum = re.search(r"^Quorum:[ \t]+(\d+)[ \t]*$", status, re.M)
        local = re.findall(r"^[ \t]*0x[0-9a-fA-F]+[ \t]+(\d+)[ \t]+.* \(local\)[ \t]*$", status, re.M)
        if not total or not quorum or len(local) != 1 or int(quorum[1]) < 1:
            raise RuntimeError("Could not confirm Proxmox reboot quorum from pvecm status")
        if int(total[1]) - int(local[0]) < int(quorum[1]):
            raise RuntimeError("Rebooting this Proxmox node would leave too few votes for quorum")
    return report


def _update_health(target: _UpdateTarget, *, evacuated: bool) -> bool:
    if target.debian_codename:
        return _debian_probe(target.config, "health", target.debian_codename)[2]
    return _preflight(target.name, target.config, evacuated=evacuated).reboot_required is True


def _read_boot_id(config: SetupConfig, timeout: float) -> str | None:
    result = _ssh_result(config, _BOOT_ID_COMMAND, timeout=timeout)
    return _validated_boot_id(result.stdout or "") if result.returncode == 0 else None


def _reboot_and_wait(config: SetupConfig, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    old_boot_id = _read_boot_id(config, min(15, timeout))
    if old_boot_id is None:
        raise RuntimeError(f"Could not read the boot ID on {config.host} before reboot")
    unit = f"basaltwater-rolling-reboot-{secrets.token_hex(6)}"
    reboot_command = shlex.join([
        "/usr/bin/systemd-run", "--quiet", "--unit", unit, "--on-active=2s",
        "/usr/bin/python3", "-IB", "-c", _REMOTE_HOST_RESTART,
    ])
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(f"Reboot deadline expired before requesting restart on {config.host}")
    requested = _ssh_result(config, reboot_command, connect_timeout=15, timeout=min(30, remaining))
    if requested.returncode:
        raise _command_error(requested, f"{config.host} rejected the reboot request")
    print(f"  Restart scheduled on {config.host} as {unit}.service", flush=True)

    while (remaining := deadline - time.monotonic()) > 0:
        try:
            new_boot_id = _read_boot_id(config, min(15, remaining))
        except (OSError, subprocess.SubprocessError):
            new_boot_id = None
        if new_boot_id and new_boot_id != old_boot_id:
            return
        time.sleep(min(_SSH_POLL_INTERVAL, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"{config.host} did not return with a new boot ID within {timeout} seconds. "
                       f"Inspect journalctl -u {unit}.service on the target")


def _print_summary(results: list[ClusterUpdateResult]) -> None:
    print()
    print("=" * 60)
    print("Rolling update summary")
    print("=" * 60)
    for result in results:
        extras: list[str] = []
        if result.reboot_required:
            extras.append("reboot-required")
        if result.rebooted:
            extras.append("rebooted")
        suffix = f" ({', '.join(extras)})" if extras else ""
        detail = f" - {result.details}" if result.details else ""
        host = result.host or "-"
        print(f"{result.status.upper():<9} {result.target} [{host}]{suffix}{detail}")
    print("=" * 60)


def run_cluster_update(
    targets: list[str], *, workspace: Optional[str] = None,
    dry_run: bool = False, reboot_timeout: int = 300, resume: bool = False,
    include_debian: bool = False,
) -> int:
    if workspace:
        set_workspace_dir(workspace)
    if type(reboot_timeout) is not int or reboot_timeout <= 0:
        raise ValueError("--reboot-timeout must be positive")
    if not targets or len(targets) != len(set(targets)) or len(targets) > 100:
        raise ValueError("Supply 1–100 unique rolling-update targets")
    if dry_run and resume:
        raise ValueError("--resume cannot be combined with --dry-run")

    prepared: list[_UpdateTarget] = []
    results = []
    for target in targets:
        config = None
        try:
            config = load_setup_command(target)
            if config is None:
                raise ValueError("No saved setup command found")
            if config.host in _LOCALHOSTS:
                raise ValueError("Rolling updates require remote targets")
            is_proxmox = config.system_type == "server_proxmox"
            if not is_proxmox and not include_debian:
                raise ValueError("Rolling updates require remote server_proxmox configurations")
            config.dry_run = dry_run
            fingerprint_data = json.dumps(config.to_dict(), sort_keys=True)
            debian_codename = None
            if is_proxmox:
                prepare_validated_runtime_config(config, workspace)
                if dry_run:
                    _print_package_preview(target, config)
                _preflight(target, config, evacuated=not resume)
                if include_debian:
                    command = shlex.join(["/usr/bin/python3", "-IB", "-c", _PROXMOX_RELEASE_CHECK])
                    probe = _ssh_result(config, command, timeout=90)
                    if probe.returncode:
                        raise _command_error(probe, "Proxmox release or package state check failed")
            else:
                debian_codename, packages, reboot = _debian_probe(config, "health" if resume else "plan")
                fingerprint_data += f"\nDebian {debian_codename}"
                if dry_run:
                    pending = ", reboot required" if reboot else ""
                    print(f"  {target} [{config.host}] Debian {DEBIAN_RELEASES[debian_codename]} ({debian_codename}), "
                          f"{packages} package(s) from cached APT{pending}")
            fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()
            prepared.append(_UpdateTarget(target, config, fingerprint, debian_codename))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            results.append(ClusterUpdateResult(target, config.host if config else None, "failed", str(exc)))
    if len(prepared) != len(targets):
        print("Preflight failed; no systems were changed.")
        _print_summary(results)
        return 1
    if len({target.config.host for target in prepared}) != len(targets):
        raise ValueError("Rolling-update targets must resolve to distinct hosts")

    from basaltwater import _execute_patch_config

    if dry_run:
        statuses = [_execute_patch_config(target.config) for target in prepared if not target.debian_codename]
        return int(any(statuses))

    store = OperationStateStore(os.path.join(get_workspace_dir(), "cluster-update.json"))
    nodes = [dict(target=target.name, host=target.config.host, fingerprint=target.fingerprint, phase="pending",
                  result=asdict(ClusterUpdateResult(target.name, target.config.host, "pending")))
             for target in prepared]
    try:
        previous = store.load()
        if resume:
            if previous is None or previous.operation_type != "cluster-update":
                raise ValueError("No rolling-update checkpoint to resume")
            saved = previous.context.get("nodes")
            if not isinstance(saved, list) or len(saved) != len(nodes):
                raise ValueError("Invalid rolling-update checkpoint")
            for expected, node in zip(nodes, saved):
                if not isinstance(node, dict) or any(node.get(key) != expected[key] for key in ("target", "host", "fingerprint")):
                    raise ValueError("Resume requires the original ordered targets and unchanged saved configurations")
                if node.get("phase") not in {"pending", "patch-failed", "patched", "rebooted", "complete"}:
                    raise ValueError("Interrupted mutation requires manual recovery before resume; inspect cluster-update.json")
                try:
                    result = ClusterUpdateResult(**node["result"])
                except (TypeError, KeyError) as exc:
                    raise ValueError("Invalid rolling-update result checkpoint") from exc
                if result.target != node["target"] or result.host != node["host"]:
                    raise ValueError("Invalid rolling-update result identity")
                if result.status not in {"pending", "failed", "skipped", "updated"} or not isinstance(result.details, str) or type(result.reboot_required) is not bool or type(result.rebooted) is not bool:
                    raise ValueError("Invalid rolling-update result fields")
                if node["phase"] == "complete" and result.status != "updated":
                    raise ValueError("Invalid completed rolling-update result")
            nodes = saved
            record = store.transition(previous.operation_id, "resuming", expected_record=previous)
        else:
            record = store.begin("cluster-update", "ordered-nodes", "prepared", context={"nodes": nodes})

        def checkpoint():
            store.transition(record.operation_id, "updating", context={"nodes": nodes})

        results = [ClusterUpdateResult(**node["result"]) for node in nodes]
        for index, (target, node, result) in enumerate(zip(prepared, nodes, results)):
            if node["phase"] == "complete":
                continue
            try:
                _update_health(target, evacuated=node["phase"] != "rebooted")
                if node["phase"] in {"pending", "patch-failed"}:
                    node["phase"] = "patching"
                    checkpoint()
                    print(f"Updating {target.name} [{target.config.host}]", flush=True)
                    if target.debian_codename:
                        command = debian_update_command("apply", target.debian_codename)
                        applied = _ssh_result(target.config, command, timeout=_DEBIAN_INSTALL_TIMEOUT)
                        if applied.returncode:
                            if applied.returncode == DEBIAN_UPDATE_BLOCKED:
                                node["phase"] = "patch-failed"
                            if applied.stdout:
                                print(applied.stdout.strip())
                            raise _command_error(applied, "Debian package update failed")
                    elif _execute_patch_config(target.config) != 0:
                        node["phase"] = "patch-failed"
                        raise RuntimeError("Patch run failed")
                    node["phase"] = "patched"
                    checkpoint()
                needs_reboot = _update_health(target, evacuated=node["phase"] != "rebooted")
                result.reboot_required |= needs_reboot
                if needs_reboot:
                    if node["phase"] == "rebooted":
                        raise RuntimeError("Reboot-required marker remains after reboot")
                    node["phase"] = "rebooting"
                    checkpoint()
                    _reboot_and_wait(target.config, reboot_timeout)
                    result.rebooted = True
                    node["phase"] = "rebooted"
                    node["result"] = asdict(result)
                    checkpoint()
                    if _update_health(target, evacuated=False):
                        raise RuntimeError("Reboot-required marker remains after reboot")
                result.status = "updated"
                result.details = "Rebooted, reconnected, and verified" if result.rebooted else "No reboot required"
                node["phase"] = "complete"
                node["result"] = asdict(result)
                checkpoint()
            except (Exception, KeyboardInterrupt) as exc:
                result.status = "failed"
                result.details = str(exc) or type(exc).__name__
                node["result"] = asdict(result)
                for later, skipped in zip(nodes[index + 1:], results[index + 1:]):
                    if later["phase"] != "complete":
                        skipped.status = "skipped"
                        skipped.details = f"Skipped after failure on {target.name}"
                        later["result"] = asdict(skipped)
                store.transition(record.operation_id, "stopped", status="recovery_required", context={"nodes": nodes})
                _print_summary(results)
                print(f"Checkpoint retained: {store.path}; inspect before using --resume.")
                return 1
        write_json_atomic(os.path.join(get_workspace_dir(), "cluster-update-last.json"), {"schema_version": 1, "operation_id": record.operation_id, "nodes": nodes})
        store.complete(record.operation_id)
        _print_summary(results)
        return 0
    finally:
        store.close()


__all__ = ["ClusterUpdateResult", "run_cluster_update"]
