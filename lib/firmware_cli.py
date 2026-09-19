"""Command-line interface for local firmware auditing and updates."""

from __future__ import annotations

import argparse
import json
import subprocess

from lib.firmware import (
    FWUPD_DEPENDENCY,
    FirmwareAuditReport,
    apply_firmware_updates,
    collect_firmware_audit,
    ensure_command_dependency,
    format_firmware_audit,
    inspect_running_proxmox_guests,
    validate_firmware_device_id,
)


def _add_dependency_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--install-dependencies",
        action="store_true",
        help="Install missing firmware command dependencies with APT without prompting",
    )


def add_firmware_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add local firmware audit and update commands."""

    parser = subparsers.add_parser(
        "firmware",
        help="Audit and deliberately update local device firmware",
        description=(
            "Inventory local firmware through fwupd and apply only explicitly "
            "requested updates. basaltwater never reboots the host automatically."
        ),
    )
    commands = parser.add_subparsers(
        dest="firmware_command", help="Firmware commands"
    )

    audit_parser = commands.add_parser(
        "audit", help="Inventory firmware devices and available updates"
    )
    audit_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use cached fwupd metadata instead of refreshing remotes",
    )
    audit_parser.add_argument(
        "--json", action="store_true", help="Output a stable JSON report"
    )
    _add_dependency_argument(audit_parser)

    update_parser = commands.add_parser(
        "update", help="Apply available fwupd firmware updates"
    )
    update_parser.add_argument(
        "device_id",
        nargs="?",
        help="Optional fwupd device ID or GUID; omit to update every eligible device",
    )
    update_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use cached fwupd metadata during the update preflight",
    )
    update_parser.add_argument(
        "--allow-running-guests",
        action="store_true",
        help="Allow an update while local Proxmox guests are running",
    )
    update_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Confirm the firmware update and fwupd prompts non-interactively",
    )
    _add_dependency_argument(update_parser)


def _ensure_fwupd(args: argparse.Namespace) -> bool:
    return ensure_command_dependency(
        FWUPD_DEPENDENCY,
        install_without_prompt=getattr(args, "install_dependencies", False),
    )


def _run_audit(args: argparse.Namespace) -> int:
    if not _ensure_fwupd(args):
        return 1
    report = collect_firmware_audit(refresh=not args.no_refresh)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_firmware_audit(report))
    return 0 if report.healthy else 1


def _find_requested_update(report: FirmwareAuditReport, device_id: str) -> bool:
    return any(
        device_id == update.device_id or device_id in update.guids
        for update in report.updates
    )


def _run_update(args: argparse.Namespace) -> int:
    if not _ensure_fwupd(args):
        return 1
    device_id = None
    if args.device_id is not None:
        try:
            device_id = validate_firmware_device_id(args.device_id)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1

    report = collect_firmware_audit(refresh=not args.no_refresh)
    print(format_firmware_audit(report))
    if not report.healthy:
        print("Error: firmware audit is incomplete; refusing to update.")
        return 1
    if not report.updates:
        print("No firmware updates are available through fwupd.")
        return 0
    if device_id is not None and not _find_requested_update(report, device_id):
        print(f"Error: no available fwupd update matches device '{device_id}'.")
        return 1

    running_guests, preflight_errors = inspect_running_proxmox_guests()
    if preflight_errors:
        for error in preflight_errors:
            print(f"Error: {error}")
        print("Refusing to update because the Proxmox guest preflight was incomplete.")
        return 1
    if running_guests and not args.allow_running_guests:
        guest_text = ", ".join(
            f"{guest.vmid} ({guest.guest_type})" for guest in running_guests
        )
        print(f"Error: local Proxmox guests are running: {guest_text}")
        print("Stop or migrate them, or explicitly pass --allow-running-guests.")
        return 1

    if not args.yes:
        target = f"device {device_id}" if device_id else "all eligible devices"
        try:
            response = input(
                f"Apply firmware updates to {target} now? The host may need a reboot. [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            print("Firmware update cancelled.")
            return 1
        if response.strip().lower() not in {"y", "yes"}:
            print("Firmware update cancelled.")
            return 0

    try:
        result = apply_firmware_updates(device_id=device_id, assume_yes=args.yes)
    except (OSError, PermissionError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Error: firmware update failed: {exc}")
        return 1
    if result.returncode != 0:
        print(f"Error: fwupdmgr exited with status {result.returncode}.")
        return 1
    print("✓ Firmware update command completed.")
    print("Review fwupd's output and reboot manually if the update requires it.")
    return 0


def run_firmware_command(args: argparse.Namespace) -> int:
    """Dispatch a local firmware command."""

    handlers = {
        "audit": _run_audit,
        "update": _run_update,
    }
    handler = handlers.get(getattr(args, "firmware_command", None))
    if handler is None:
        print("Error: firmware command required (audit, update)")
        return 1
    return handler(args)


__all__ = ["add_firmware_subparser", "run_firmware_command"]
