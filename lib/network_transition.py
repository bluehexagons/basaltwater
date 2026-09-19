"""Controller-side verification for safe live network address transitions."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from lib.config import SetupConfig
from lib.proxmox_network_transition import (
    ProxmoxNetworkPlan,
    apply_proxmox_network_plan,
    prepare_proxmox_network_plan,
    rollback_proxmox_network_plan,
)
from lib.ssh_utils import build_ssh_command, chain_remote_commands, ssh_batch_mode


REMOTE_INSTALL_DIR = "/opt/basaltwater"
PENDING_TRANSITION_PATH = "/run/basaltwater-network-transition.json"
VERIFY_ATTEMPTS = 12
VERIFY_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class RemoteNetworkTransition:
    """Minimal transaction identity returned by an untrusted new endpoint."""

    transition_id: str
    state: str


def network_transition_targets(config: SetupConfig) -> list[str]:
    """Return normalized requested addresses in preferred SSH handoff order."""

    targets: list[str] = []
    for value in (config.static_ipv4, config.static_ipv6):
        if value:
            targets.append(str(ipaddress.ip_interface(value).ip))
    return targets


def _run_transition_ssh(
    config: SetupConfig,
    host: str,
    remote_command: str,
) -> subprocess.CompletedProcess[str]:
    command = build_ssh_command(
        host,
        "root",
        config.ssh_key,
        remote_command=remote_command,
        batch_mode=ssh_batch_mode(),
        connect_timeout=5,
        server_alive_interval=5,
    )
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 255, "", str(exc))


def _wait_for_transition_ssh(
    config: SetupConfig,
    host: str,
    remote_command: str,
    *,
    attempts: int = VERIFY_ATTEMPTS,
    interval: float = VERIFY_INTERVAL_SECONDS,
) -> subprocess.CompletedProcess[str]:
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(attempts):
        last_result = _run_transition_ssh(config, host, remote_command)
        if last_result.returncode == 0:
            return last_result
        if attempt + 1 < attempts:
            time.sleep(interval)
    assert last_result is not None
    return last_result


def _transition_probe_command() -> str:
    script = (
        "import json; "
        f"data=json.load(open({PENDING_TRANSITION_PATH!r}, encoding='utf-8')); "
        "print(data.get('transition_id', ''), data.get('state', ''))"
    )
    return chain_remote_commands([["python3", "-c", script]])


def _parse_transition_probe(output: str) -> Optional[RemoteNetworkTransition]:
    fields = output.strip().split()
    if (
        len(fields) != 2
        or not re.fullmatch(r"[0-9a-f]{64}", fields[0])
        or fields[1] not in {"prepared", "committed"}
    ):
        return None
    return RemoteNetworkTransition(fields[0], fields[1])


def _read_remote_transition(
    config: SetupConfig,
    host: str,
) -> Optional[RemoteNetworkTransition]:
    result = _run_transition_ssh(config, host, _transition_probe_command())
    if result.returncode != 0:
        return None
    return _parse_transition_probe(result.stdout or "")


def _wait_for_any_transition(
    config: SetupConfig,
    host: str,
    *,
    attempts: int = VERIFY_ATTEMPTS,
    interval: float = VERIFY_INTERVAL_SECONDS,
) -> Optional[RemoteNetworkTransition]:
    for attempt in range(attempts):
        transition = _read_remote_transition(config, host)
        if transition is not None:
            return transition
        if attempt + 1 < attempts:
            time.sleep(interval)
    return None


def _wait_for_transition_state(
    config: SetupConfig,
    host: str,
    transition_id: str,
    state: str,
    *,
    attempts: int = VERIFY_ATTEMPTS,
    interval: float = VERIFY_INTERVAL_SECONDS,
) -> subprocess.CompletedProcess[str]:
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(attempts):
        last_result = _run_transition_ssh(config, host, _transition_probe_command())
        observed = (
            _parse_transition_probe(last_result.stdout or "")
            if last_result.returncode == 0
            else None
        )
        if observed == RemoteNetworkTransition(transition_id, state):
            return last_result
        if last_result.returncode == 0:
            last_result = subprocess.CompletedProcess(
                last_result.args,
                1,
                last_result.stdout,
                "network transition identity or state mismatch",
            )
        if attempt + 1 < attempts:
            time.sleep(interval)
    assert last_result is not None
    return last_result


def _transition_action_command(action: str, transition_id: str) -> str:
    return chain_remote_commands(
        [
            ["cd", REMOTE_INSTALL_DIR],
            ["python3", "-m", "common.network_steps", action, transition_id],
        ]
    )


def _abort_transition(
    config: SetupConfig,
    transition_id: Optional[str] = None,
    fallback_hosts: Optional[list[str]] = None,
) -> None:
    if transition_id is None:
        transition = _wait_for_any_transition(
            config,
            config.host,
            attempts=3,
            interval=1.0,
        )
        if transition is None:
            return
        transition_id = transition.transition_id

    hosts = list(dict.fromkeys([config.host, *(fallback_hosts or [])]))
    abort_command = _transition_action_command("--abort-transition", transition_id)
    last_result: Optional[subprocess.CompletedProcess[str]] = None
    for host in hosts:
        last_result = _wait_for_transition_ssh(
            config,
            host,
            abort_command,
            attempts=3,
            interval=1.0,
        )
        if last_result.returncode == 0:
            return
    assert last_result is not None
    detail = (last_result.stderr or last_result.stdout or "SSH unavailable").strip()
    print(f"  ⚠ Could not roll back the network transition: {detail}")


def _rollback_handoff(
    config: SetupConfig,
    proxmox_plan: Optional[ProxmoxNetworkPlan],
    transition_id: str,
    verified_hosts: list[str],
) -> None:
    rollback_proxmox_network_plan(proxmox_plan)
    _abort_transition(config, transition_id, verified_hosts)


def finish_network_transition(config: SetupConfig, setup_returncode: int) -> int:
    """Verify requested SSH addresses and commit or abort pending persistence."""

    if not config.activate_network or config.dry_run:
        return setup_returncode

    if setup_returncode != 0:
        _abort_transition(config)
        return setup_returncode

    targets = network_transition_targets(config)
    if not targets:
        print("  ✗ Network activation requested without a target address")
        _abort_transition(config)
        return 1

    transition = _wait_for_any_transition(config, config.host)
    if transition is None or transition.state != "prepared":
        print("  ✗ Could not read a prepared network transaction from the original host")
        _abort_transition(config)
        return 1

    transition_id = transition.transition_id
    verified_hosts: list[str] = []
    print("\nVerifying SSH access on the requested network address(es)...")
    for target in targets:
        result = _wait_for_transition_state(
            config,
            target,
            transition_id,
            "prepared",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "SSH unavailable").strip()
            print(f"  ✗ SSH identity verification failed for {target}: {detail}")
            _abort_transition(config, transition_id, verified_hosts)
            return 1
        verified_hosts.append(target)
        print(f"  ✓ Verified the original host over SSH at {target}")

    proxmox_plan: Optional[ProxmoxNetworkPlan] = None
    try:
        proxmox_plan = prepare_proxmox_network_plan(config, config.host)
    except RuntimeError as exc:
        print(f"  ✗ Proxmox guest network preflight failed: {exc}")
        _abort_transition(config, transition_id, verified_hosts)
        return 1

    try:
        apply_proxmox_network_plan(proxmox_plan)
    except RuntimeError as exc:
        print(f"  ✗ Proxmox metadata update failed before guest persistence: {exc}")
        _rollback_handoff(config, proxmox_plan, transition_id, verified_hosts)
        return 1

    if proxmox_plan is not None and proxmox_plan.requested_value != proxmox_plan.previous_value:
        post_proxmox_hosts = list(dict.fromkeys([config.host, *targets]))
        for target in post_proxmox_hosts:
            result = _wait_for_transition_state(
                config,
                target,
                transition_id,
                "prepared",
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "SSH unavailable").strip()
                print(
                    f"  ✗ SSH identity check failed at {target} after the Proxmox "
                    f"metadata update: {detail}"
                )
                _rollback_handoff(config, proxmox_plan, transition_id, verified_hosts)
                return 1

    commit_command = _transition_action_command("--commit-transition", transition_id)
    commit_result = _wait_for_transition_ssh(config, targets[0], commit_command)
    if commit_result.returncode != 0:
        detail = (commit_result.stderr or commit_result.stdout or "unknown error").strip()
        print(f"  ✗ New address is reachable, but persistence failed: {detail}")
        _rollback_handoff(config, proxmox_plan, transition_id, verified_hosts)
        return 1

    post_commit_hosts = list(dict.fromkeys([config.host, *targets]))
    for target in post_commit_hosts:
        result = _wait_for_transition_state(
            config,
            target,
            transition_id,
            "committed",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "SSH unavailable").strip()
            print(f"  ✗ SSH became unavailable at {target} after persistence: {detail}")
            _rollback_handoff(config, proxmox_plan, transition_id, verified_hosts)
            return 1

    finalize_command = _transition_action_command("--finalize-transition", transition_id)
    # Finalize through the original, still-verified endpoint. This confirms the
    # old route was not interrupted and avoids trusting an ambiguous success
    # response from a route that has just moved.
    finalize_result = _wait_for_transition_ssh(config, config.host, finalize_command)
    if finalize_result.returncode != 0:
        detail = (
            finalize_result.stderr
            or finalize_result.stdout
            or "SSH unavailable"
        ).strip()
        print(f"  ✗ Could not finalize the verified network transition: {detail}")
        _rollback_handoff(config, proxmox_plan, transition_id, verified_hosts)
        return 1

    previous_host = config.host
    config.host = targets[0]
    if previous_host == config.host:
        print(f"  ✓ Network activation verified at {config.host}")
    else:
        print(
            f"  ✓ Network handoff complete: {previous_host} → {config.host}; "
            "the previous live address remains until reboot"
        )
    return 0
