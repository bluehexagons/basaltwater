"""Local firmware inventory, fwupd integration, and update safety checks."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from lib.proxmox_manage import ContainerInfo, _parse_pct_list, _parse_qm_list
from lib.validation import validate_apt_packages


@dataclass(frozen=True)
class CommandDependency:
    """One command-line dependency and its Debian package."""

    command: str
    package: str
    purpose: str


FWUPD_DEPENDENCY = CommandDependency(
    command="fwupdmgr",
    package="fwupd",
    purpose="query and apply vendor-supported firmware updates",
)

_DMI_FIELDS = {
    "system_vendor": "sys_vendor",
    "product_name": "product_name",
    "product_version": "product_version",
    "board_name": "board_name",
    "bios_vendor": "bios_vendor",
    "bios_version": "bios_version",
    "bios_date": "bios_date",
}
_PACKAGE_NAMES = (
    "fwupd",
    "intel-microcode",
    "amd64-microcode",
    "pve-firmware",
)
_DEVICE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_NO_UPDATE_MARKERS = (
    "no updatable devices",
    "no updates available",
    "no available firmware updates",
    "nothing to do",
)


@dataclass(frozen=True)
class FirmwareDevice:
    """Normalized subset of a device returned by fwupd."""

    device_id: str
    name: str
    version: Optional[str] = None
    vendor: Optional[str] = None
    guids: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    available_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable device record."""

        return asdict(self)


@dataclass
class FirmwareAuditReport:
    """Host identity and normalized fwupd firmware state."""

    kernel: str
    dmi: dict[str, str] = field(default_factory=dict)
    packages: dict[str, Optional[str]] = field(default_factory=dict)
    fwupd_version: Optional[str] = None
    devices: list[FirmwareDevice] = field(default_factory=list)
    updates: list[FirmwareDevice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Return whether the firmware backend completed without errors."""

        return not self.errors

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON representation for automation."""

        return {
            "operation": "firmware-audit",
            "healthy": self.healthy,
            "kernel": self.kernel,
            "dmi": dict(self.dmi),
            "packages": dict(self.packages),
            "fwupd_version": self.fwupd_version,
            "devices": [device.to_dict() for device in self.devices],
            "updates": [device.to_dict() for device in self.updates],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _run(
    command: Sequence[str],
    *,
    timeout: int = 120,
    capture_output: bool = True,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded local command without invoking a shell."""

    return subprocess.run(
        list(command),
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=timeout,
        env=env,
    )


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"


def _privileged_command(command: Sequence[str]) -> list[str]:
    """Prefix a mutating command with sudo when the caller is not root."""

    if os.geteuid() == 0:
        return list(command)
    sudo_path = shutil.which("sudo")
    if sudo_path:
        return [sudo_path, *command]
    raise PermissionError(
        "this operation requires root privileges; rerun basaltwater with sudo"
    )


def install_apt_dependency(dependency: CommandDependency) -> bool:
    """Install one validated command dependency through APT."""

    validate_apt_packages([dependency.package])
    apt_path = shutil.which("apt-get")
    if not apt_path:
        raise RuntimeError(
            "automatic dependency installation requires apt-get on a Debian-based host"
        )

    apt_environment = os.environ.copy()
    apt_environment["DEBIAN_FRONTEND"] = "noninteractive"
    apt_options = [
        apt_path,
        "-o",
        "DPkg::Lock::Timeout=120",
        "-o",
        "Dpkg::Use-Pty=0",
    ]
    commands = (
        [*apt_options, "update", "-q"],
        [*apt_options, "install", "-y", "-q", dependency.package],
    )
    for command in commands:
        result = _run(
            _privileged_command(command),
            timeout=900,
            capture_output=False,
            env=apt_environment,
        )
        if result.returncode != 0:
            return False
    return shutil.which(dependency.command) is not None


def ensure_command_dependency(
    dependency: CommandDependency,
    *,
    install_without_prompt: bool = False,
    prompt: Optional[Callable[[str], str]] = None,
) -> bool:
    """Ensure a command exists, offering to install its APT package."""

    if shutil.which(dependency.command):
        return True

    print(
        f"Dependency missing: {dependency.command} is needed to {dependency.purpose}."
    )
    should_install = install_without_prompt
    if not should_install:
        try:
            prompt_func = prompt or input
            response = prompt_func(
                f"Install the '{dependency.package}' package with APT now? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            print("Dependency installation cancelled.")
            return False
        should_install = response.strip().lower() in {"y", "yes"}
    if not should_install:
        print("Dependency installation declined; firmware command cancelled.")
        return False

    print(f"Installing firmware dependency: {dependency.package}", flush=True)
    try:
        installed = install_apt_dependency(dependency)
    except (
        OSError,
        PermissionError,
        RuntimeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Error: could not install {dependency.package}: {exc}")
        return False
    if not installed:
        print(f"Error: APT did not install a usable {dependency.command} command.")
        return False
    print(f"✓ Installed {dependency.package}")
    return True


def _read_dmi() -> dict[str, str]:
    dmi: dict[str, str] = {}
    base_path = Path("/sys/class/dmi/id")
    for label, filename in _DMI_FIELDS.items():
        try:
            value = (base_path / filename).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if value:
            dmi[label] = value
    return dmi


def _package_versions() -> dict[str, Optional[str]]:
    versions: dict[str, Optional[str]] = {}
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return {package: None for package in _PACKAGE_NAMES}
    for package in _PACKAGE_NAMES:
        result = _run(
            [dpkg_query, "-W", "-f=${db:Status-Abbrev}\t${Version}", package],
            timeout=30,
        )
        value: Optional[str] = None
        if result.returncode == 0:
            status, separator, version = (result.stdout or "").strip().partition("\t")
            if separator and status.startswith("ii") and version:
                value = version
        versions[package] = value
    return versions


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _normalize_devices(payload: object) -> list[FirmwareDevice]:
    if isinstance(payload, dict):
        raw_devices = payload.get("Devices", [])
    elif isinstance(payload, list):
        raw_devices = payload
    else:
        raw_devices = []
    if not isinstance(raw_devices, list):
        return []

    devices: list[FirmwareDevice] = []
    for raw_device in raw_devices:
        if not isinstance(raw_device, dict):
            continue
        guids = _string_list(raw_device.get("Guid") or raw_device.get("Guids"))
        device_id = str(raw_device.get("DeviceId") or "").strip()
        if not device_id and guids:
            device_id = guids[0]
        releases = raw_device.get("Releases")
        available_versions: list[str] = []
        if isinstance(releases, list):
            for release in releases:
                if not isinstance(release, dict):
                    continue
                version = str(release.get("Version") or "").strip()
                if version and version not in available_versions:
                    available_versions.append(version)
        devices.append(
            FirmwareDevice(
                device_id=device_id,
                name=str(raw_device.get("Name") or "Unknown device"),
                version=(
                    str(raw_device["Version"])
                    if raw_device.get("Version") is not None
                    else None
                ),
                vendor=(
                    str(raw_device["Vendor"])
                    if raw_device.get("Vendor") is not None
                    else None
                ),
                guids=guids,
                flags=_string_list(raw_device.get("Flags")),
                available_versions=available_versions,
            )
        )
    return devices


def _parse_fwupd_json(result: subprocess.CompletedProcess[str], operation: str) -> object:
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse fwupd {operation} output: {exc}") from exc


def _is_no_updates(result: subprocess.CompletedProcess[str]) -> bool:
    detail = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return any(marker in detail for marker in _NO_UPDATE_MARKERS)


def collect_firmware_audit(*, refresh: bool = True) -> FirmwareAuditReport:
    """Collect local DMI, package, device, and available-update information."""

    report = FirmwareAuditReport(
        kernel=platform.release(),
        dmi=_read_dmi(),
        packages=_package_versions(),
    )
    try:
        version_result = _run([FWUPD_DEPENDENCY.command, "--version"], timeout=30)
        if version_result.returncode == 0:
            version_lines = [
                line.strip() for line in (version_result.stdout or "").splitlines()
                if line.strip()
            ]
            report.fwupd_version = version_lines[0] if version_lines else "unknown"
        else:
            report.warnings.append(
                f"Could not read fwupd version: {_failure_detail(version_result)}"
            )

        if refresh:
            refresh_result = _run(
                [FWUPD_DEPENDENCY.command, "refresh", "--force"], timeout=300
            )
            if refresh_result.returncode != 0:
                report.warnings.append(
                    "Could not refresh firmware metadata: "
                    f"{_failure_detail(refresh_result)}"
                )

        device_result = _run(
            [FWUPD_DEPENDENCY.command, "get-devices", "--json"], timeout=120
        )
        if device_result.returncode == 0:
            report.devices = _normalize_devices(
                _parse_fwupd_json(device_result, "device inventory")
            )
        else:
            report.errors.append(
                f"Could not query firmware devices: {_failure_detail(device_result)}"
            )

        update_result = _run(
            [FWUPD_DEPENDENCY.command, "get-updates", "--json"], timeout=180
        )
        if update_result.returncode == 0:
            report.updates = _normalize_devices(
                _parse_fwupd_json(update_result, "update catalog")
            )
        elif not _is_no_updates(update_result):
            report.errors.append(
                f"Could not query firmware updates: {_failure_detail(update_result)}"
            )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        report.errors.append(str(exc))
    return report


def format_firmware_audit(report: FirmwareAuditReport) -> str:
    """Return a concise operator-facing audit report."""

    lines = [
        "Firmware audit",
        f"  result: {'READY' if report.healthy else 'INCOMPLETE'}",
        f"  kernel: {report.kernel}",
    ]
    for label in _DMI_FIELDS:
        if label in report.dmi:
            lines.append(f"  {label.replace('_', ' ')}: {report.dmi[label]}")
    lines.append(f"  fwupd: {report.fwupd_version or 'unknown'}")
    lines.append("  related packages:")
    for package, version in report.packages.items():
        lines.append(f"    {package}: {version or 'not installed'}")

    lines.append(f"  devices reported by fwupd: {len(report.devices)}")
    for device in report.devices:
        version = f" (current {device.version})" if device.version else ""
        lines.append(f"    - {device.name}{version}")
        if device.device_id:
            lines.append(f"      id: {device.device_id}")

    lines.append(f"  available updates: {len(report.updates)}")
    for device in report.updates:
        versions = ", ".join(device.available_versions) or "version not reported"
        lines.append(f"    - {device.name}: {device.version or 'unknown'} -> {versions}")
        if device.device_id:
            lines.append(f"      id: {device.device_id}")
    for warning in report.warnings:
        lines.append(f"  WARNING: {warning}")
    for error in report.errors:
        lines.append(f"  ERROR: {error}")
    lines.append(
        "  NOTE: fwupd only covers firmware published for supported devices; "
        "check the hardware vendor separately for legacy systems."
    )
    return "\n".join(lines)


def validate_firmware_device_id(device_id: str) -> str:
    """Validate a fwupd device ID or GUID accepted on the command line."""

    candidate = device_id.strip()
    if not _DEVICE_ID_PATTERN.fullmatch(candidate):
        raise ValueError(
            "firmware device ID must contain only letters, numbers, '.', '_', ':', or '-'"
        )
    return candidate


def inspect_running_proxmox_guests() -> tuple[list[ContainerInfo], list[str]]:
    """Return local running Proxmox guests and preflight errors, if applicable."""

    guests: list[ContainerInfo] = []
    errors: list[str] = []
    commands = (
        ("pct", _parse_pct_list, "LXC"),
        ("qm", _parse_qm_list, "VM"),
    )
    for command, parser, label in commands:
        command_path = shutil.which(command)
        if not command_path:
            continue
        try:
            result = _run([command_path, "list"], timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"Could not inspect {label} guests: {exc}")
            continue
        if result.returncode != 0:
            errors.append(
                f"Could not inspect {label} guests: {_failure_detail(result)}"
            )
            continue
        try:
            guests.extend(parser(result.stdout or ""))
        except (TypeError, ValueError) as exc:
            errors.append(f"Could not parse {label} guest state: {exc}")
    running = [guest for guest in guests if guest.status.lower() == "running"]
    running.sort(key=lambda guest: guest.vmid)
    return running, errors


def apply_firmware_updates(
    *,
    device_id: Optional[str] = None,
    assume_yes: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Apply all or one fwupd update without rebooting through basaltwater."""

    command = [FWUPD_DEPENDENCY.command, "update"]
    if device_id is not None:
        command.append(validate_firmware_device_id(device_id))
    if assume_yes:
        command.append("--assume-yes")
    command.append("--no-reboot-check")
    return _run(
        _privileged_command(command),
        timeout=1800,
        capture_output=False,
    )


__all__ = [
    "CommandDependency",
    "FWUPD_DEPENDENCY",
    "FirmwareAuditReport",
    "FirmwareDevice",
    "apply_firmware_updates",
    "collect_firmware_audit",
    "ensure_command_dependency",
    "format_firmware_audit",
    "inspect_running_proxmox_guests",
    "install_apt_dependency",
    "validate_firmware_device_id",
]
