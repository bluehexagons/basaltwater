"""Verified native Proxmox tasks shared by provisioning and management."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from typing import Protocol

from lib.validation import validate_positive_integer


class ProxmoxTaskError(Exception):
    """A native task failed, or its final outcome could not be established."""

    def __init__(self, message: str, *, uncertain: bool = True) -> None:
        super().__init__(message)
        self.uncertain = uncertain


class _TaskRunner(Protocol):
    def __call__(
        self, command: str, *, timeout: int
    ) -> subprocess.CompletedProcess[str]: ...


def vm_disk_resize_command(vmid: int, volume: str, size: str) -> str:
    """Build the native resize request for an absolute disk size."""
    vmid = validate_positive_integer(str(vmid), "VMID")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", volume):
        raise ValueError(f"Invalid disk name: {volume!r}")
    if not re.fullmatch(r"[0-9]+[KMGT]", size) or int(size[:-1]) == 0:
        raise ValueError(f"Invalid absolute disk size: {size!r}")
    # qm resize lacks the CLI task-exit callback, so a failed worker can exit
    # zero. pvesh exposes the same API's task ID for explicit verification.
    return shlex.join([
        "pvesh", "set", f"/nodes/localhost/qemu/{vmid}/resize",
        "--disk", volume, "--size", size, "--output-format", "json",
    ])


def resize_vm_disk(
    vmid: int, volume: str, size: str, run: _TaskRunner
) -> None:
    """Resize once and require a matching, stopped native task with status OK.

    Unknown outcomes must preserve the guest for inspection. Only a verified
    final task failure sets ``ProxmoxTaskError.uncertain`` to false.
    """
    command = vm_disk_resize_command(vmid, volume, size)
    label = f"qm resize {vmid} {volume} {size}"
    recovery = (
        "Inspect the Proxmox task log and disk state before retrying; "
        "the resize was not replayed."
    )
    # The watchdog starts after SSH authentication; transport timeouts also
    # exceed it in unattended sessions without changing interactive auth policy.
    try:
        result = run(f"timeout --kill-after=10s 3600s {command}", timeout=3660)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProxmoxTaskError(
            f"{label} completion is uncertain: {exc}. {recovery}"
        ) from exc
    if result.returncode != 0:
        raise ProxmoxTaskError(
            f"{label} failed; completion may be uncertain: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}. {recovery}"
        )
    try:
        # Synchronous worker logs precede pvesh's final JSON return value.
        upid = json.loads((result.stdout or "").strip().splitlines()[-1])
        if not isinstance(upid, str) or not re.fullmatch(
            rf"UPID:[^:\s/]+:[0-9A-Fa-f]+:[0-9A-Fa-f]+:[0-9A-Fa-f]+:resize:{vmid}:[^:\s/]+:",
            upid,
        ):
            raise ValueError("missing or invalid resize task ID")
    except (ValueError, IndexError) as exc:
        raise ProxmoxTaskError(
            f"{label} completion is uncertain: no valid native task ID. "
            f"{(result.stderr or '').strip()} {recovery}"
        ) from exc
    status_command = shlex.join([
        "pvesh", "get", f"/nodes/localhost/tasks/{upid}/status",
        "--output-format", "json",
    ])
    try:
        task_result = run(
            f"timeout --kill-after=10s 30s {status_command}", timeout=50
        )
        if task_result.returncode != 0:
            raise ValueError((task_result.stderr or task_result.stdout or "no output").strip())
        task = json.loads(task_result.stdout)
        if not isinstance(task, dict) or task.get("upid") != upid:
            raise ValueError("missing or mismatched task status")
        # pvesh runs this CLI worker synchronously. Running or malformed status
        # cannot establish completion and must never trigger another mutation.
        if (
            task.get("status") != "stopped"
            or not isinstance(task.get("exitstatus"), str)
            or not task["exitstatus"].strip()
        ):
            raise ValueError("native task has no final outcome")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise ProxmoxTaskError(
            f"{label} completion is uncertain for task {upid}: {exc}. {recovery}"
        ) from exc
    if task["exitstatus"] != "OK":
        raise ProxmoxTaskError(
            f"{label} failed: task {upid}: {task['exitstatus']}. {recovery}",
            uncertain=False,
        )
