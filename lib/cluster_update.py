"""Rolling update helpers for ordered multi-node maintenance."""

from __future__ import annotations

import subprocess
import hashlib
import json
import os
import shlex
import time
from dataclasses import asdict, dataclass
from typing import Optional

from lib.cache import load_setup_command
from lib.atomic_io import write_json_atomic
from lib.operation_state import OperationStateStore
from lib.remote_utils import CommandTimeoutError, run
from lib.config import SetupConfig
from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_maintenance import ProxmoxMaintenanceReport, collect_maintenance_report
from lib.setup_common import prepare_validated_runtime_config
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.workspace import get_workspace_dir, set_workspace_dir


_LOCALHOSTS = {"localhost", "127.0.0.1", "::1"}
_SSH_POLL_INTERVAL = 5
_REBOOT_SHUTDOWN_TIMEOUT = 90


@dataclass
class ClusterUpdateResult:
    target: str
    host: Optional[str]
    status: str
    details: str = ""
    reboot_required: bool = False
    rebooted: bool = False


def _ssh_result(
    config: SetupConfig,
    remote_command: str,
    *,
    connect_timeout: int = 5,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return run(
        build_ssh_command(
            config.host,
            "root",
            config.ssh_key,
            remote_command=remote_command,
            batch_mode=ssh_batch_mode(),
            connect_timeout=connect_timeout,
            server_alive_interval=connect_timeout,
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _ssh_available(config: SetupConfig, timeout: float) -> bool:
    try:
        return _ssh_result(config, "true", timeout=timeout).returncode == 0
    except CommandTimeoutError:
        return False


def _wait_for_ssh_state(
    config: SetupConfig,
    *,
    available: bool,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        if _ssh_available(config, min(15, remaining)) == available:
            return True
        time.sleep(min(_SSH_POLL_INTERVAL, max(0, deadline - time.monotonic())))
    return False


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
    return report


def _reboot_and_wait(config: SetupConfig, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    reboot_command = (
        "nohup sh -lc "
        "'sleep 1 && shutdown -r now \"infra_tools rolling update\"' "
        ">/dev/null 2>&1 </dev/null &"
    )
    if _ssh_result(config, reboot_command, connect_timeout=15, timeout=min(30, timeout)).returncode != 0:
        raise RuntimeError(f"{config.host} rejected the reboot request")

    shutdown_timeout = min(deadline - time.monotonic(),
        _REBOOT_SHUTDOWN_TIMEOUT,
        max(1, timeout // 3),
    )
    if not _wait_for_ssh_state(config, available=False, timeout=shutdown_timeout):
        raise RuntimeError(
            f"{config.host} never went offline after the reboot request"
        )

    startup_timeout = max(0, deadline - time.monotonic())
    if not _wait_for_ssh_state(config, available=True, timeout=startup_timeout):
        raise RuntimeError(f"{config.host} did not return over SSH after reboot")


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
) -> int:
    if workspace:
        set_workspace_dir(workspace)
    if type(reboot_timeout) is not int or reboot_timeout <= 0:
        raise ValueError("--reboot-timeout must be positive")
    if not targets or len(targets) != len(set(targets)) or len(targets) > 100:
        raise ValueError("Supply 1–100 unique rolling-update targets")
    if dry_run and resume:
        raise ValueError("--resume cannot be combined with --dry-run")

    prepared = []
    results = []
    for target in targets:
        config = load_setup_command(target)
        try:
            if config is None:
                raise ValueError("No saved setup command found")
            if config.host in _LOCALHOSTS or config.system_type != "server_proxmox":
                raise ValueError("Rolling updates require remote server_proxmox configurations")
            config.dry_run = dry_run
            fingerprint = hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True).encode()).hexdigest()
            prepare_validated_runtime_config(config, workspace)
            _preflight(target, config, evacuated=not resume)
            prepared.append((target, config, fingerprint))
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            results.append(ClusterUpdateResult(target, config.host if config else None, "failed", str(exc)))
    if len(prepared) != len(targets):
        print("Preflight failed; no systems were changed.")
        _print_summary(results)
        return 1
    if len({config.host for _, config, _ in prepared}) != len(targets):
        raise ValueError("Rolling-update targets must resolve to distinct hosts")

    from infra_tools import _execute_patch_config

    if dry_run:
        return 0 if all(_execute_patch_config(config) == 0 for _, config, _ in prepared) else 1

    store = OperationStateStore(os.path.join(get_workspace_dir(), "cluster-update.json"))
    nodes = [dict(target=target, host=config.host, fingerprint=fingerprint, phase="pending",
                  result=asdict(ClusterUpdateResult(target, config.host, "pending")))
             for target, config, fingerprint in prepared]
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
            record = store.transition(previous.operation_id, "resuming")
        else:
            record = store.begin("cluster-update", "ordered-nodes", "prepared", context={"nodes": nodes})

        def checkpoint():
            store.transition(record.operation_id, "updating", context={"nodes": nodes})

        results = [ClusterUpdateResult(**node["result"]) for node in nodes]
        for index, ((target, config, _), node, result) in enumerate(zip(prepared, nodes, results)):
            if node["phase"] == "complete":
                continue
            try:
                _preflight(target, config, evacuated=node["phase"] != "rebooted")
                if node["phase"] in {"pending", "patch-failed"}:
                    node["phase"] = "patching"
                    checkpoint()
                    if _execute_patch_config(config) != 0:
                        node["phase"] = "patch-failed"
                        raise RuntimeError("Patch run failed")
                    node["phase"] = "patched"
                    checkpoint()
                maintenance = _preflight(target, config, evacuated=node["phase"] != "rebooted")
                result.reboot_required = maintenance.reboot_required is True
                if result.reboot_required:
                    if node["phase"] == "rebooted":
                        raise RuntimeError("Reboot-required marker remains after reboot")
                    node["phase"] = "rebooting"
                    checkpoint()
                    _reboot_and_wait(config, reboot_timeout)
                    result.rebooted = True
                    node["phase"] = "rebooted"
                    node["result"] = asdict(result)
                    checkpoint()
                    maintenance = _preflight(target, config, evacuated=False)
                    if maintenance.reboot_required:
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
                        skipped.details = f"Skipped after failure on {target}"
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
