from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from lib.config import SetupConfig
from lib.validation import validate_package_name


DATA_ANALYSIS_MARKER_PACKAGES = (
    "jupyterlab",
    "python3-pandas",
)
AV_TOOL_MARKER_PACKAGES = (
    "ffmpeg",
    "imagemagick",
    "libimage-exiftool-perl",
)
GL_TOOL_MARKER_PACKAGES = (
    "apitrace",
    "mesa-utils",
)


def check_command_exists(command: str) -> bool:
    """Check if a command exists in PATH."""
    try:
        subprocess.run(
            ["which", command],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_service_exists(service: str) -> bool:
    """Check if a systemd service exists."""
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", f"{service}.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return service in result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_directory_exists(path: str) -> bool:
    return os.path.isdir(path)


def check_file_exists(path: str) -> bool:
    return os.path.isfile(path)


def check_package_installed(package: str) -> bool:
    """Return whether a validated Debian package is fully installed."""

    try:
        safe_package = validate_package_name(package)
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", safe_package],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (ValueError, subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0 and "install ok installed" in result.stdout


def detect_go() -> bool:
    return check_command_exists("go")


def detect_node() -> bool:
    """Detect user-installed Node.js via nvm in home directory."""
    home_dir = os.path.expanduser("~")
    nvm_path = os.path.join(home_dir, ".nvm")
    return check_directory_exists(nvm_path) or check_command_exists("nvm")


def detect_python() -> bool:
    """Detect uv-managed Python tooling."""
    home_dir = os.path.expanduser("~")
    uv_path = os.path.join(home_dir, ".local", "bin", "uv")
    return check_file_exists(uv_path) or check_command_exists("uv")


def detect_data_analysis_tools() -> bool:
    """Detect the distinctive packages from the managed analysis bundle."""

    return all(
        check_package_installed(package)
        for package in DATA_ANALYSIS_MARKER_PACKAGES
    )


def detect_av_tools() -> bool:
    """Detect the packages from the managed AV tool bundle."""

    return all(check_package_installed(package) for package in AV_TOOL_MARKER_PACKAGES)


def detect_gl_tools() -> bool:
    """Detect the packages from the managed OpenGL tool bundle."""

    return all(check_package_installed(package) for package in GL_TOOL_MARKER_PACKAGES)


def detect_deployments() -> list[tuple[str, str]]:
    deployments: list[tuple[str, str]] = []
    deploy_base = "/opt/deployments"

    if not check_directory_exists(deploy_base):
        return deployments

    try:
        for item in os.listdir(deploy_base):
            item_path = os.path.join(deploy_base, item)
            if os.path.isdir(item_path):
                deployments.append((item, "unknown"))
    except (OSError, PermissionError):
        pass

    return deployments


def detect_samba() -> bool:
    return (
        check_service_exists("smbd")
        or check_service_exists("nmbd")
        or check_file_exists("/etc/samba/smb.conf")
    )


def detect_samba_shares() -> list[str]:
    shares: list[str] = []
    smb_conf = "/etc/samba/smb.conf"

    if not check_file_exists(smb_conf):
        return shares

    try:
        with open(smb_conf, "r", encoding="utf-8") as handle:
            content = handle.read()
        share_pattern = re.compile(r"^\[([^\]]+)\]", re.MULTILINE)
        for match in share_pattern.findall(content):
            if match not in ["global", "homes", "printers"]:
                shares.append(match)
    except (OSError, PermissionError):
        pass

    return shares


def detect_sync_operations() -> list[str]:
    operations: list[str] = []
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.split("\n"):
            if "sync" in line.lower() and ".timer" in line:
                operations.append(line.strip())
    except (subprocess.SubprocessError, OSError):
        pass

    return operations


def detect_scrub_operations() -> list[str]:
    operations: list[str] = []
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.split("\n"):
            if "scrub" in line.lower() and ".timer" in line:
                operations.append(line.strip())
    except (subprocess.SubprocessError, OSError):
        pass

    return operations


def detect_smb_mounts() -> list[str]:
    mounts: list[str] = []

    if check_file_exists("/etc/fstab"):
        try:
            with open("/etc/fstab", "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line and not line.startswith("#") and ("cifs" in line or "smb" in line):
                        mounts.append(line.split()[1] if len(line.split()) > 1 else line)
        except (OSError, PermissionError):
            pass

    try:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=mount", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.split("\n"):
            if "mnt" in line and ".mount" in line:
                mounts.append(line.split()[0])
    except (subprocess.SubprocessError, OSError):
        pass

    return mounts


def reconstruct_configuration(host: str = "localhost", username: str = "root") -> tuple[SetupConfig, dict[str, Any]]:
    """Reconstruct setup configuration by analyzing the server."""
    config_dict: dict[str, Any] = {
        "username": username,
        "install_go": detect_go(),
        "install_node": detect_node(),
        "install_python": detect_python(),
        "install_data_analysis_tools": detect_data_analysis_tools(),
        "install_av_tools": detect_av_tools(),
        "install_gl_tools": detect_gl_tools(),
        "install_musescore": check_package_installed("musescore3"),
        "enable_samba": detect_samba(),
    }

    deployments = detect_deployments()
    system_type = "server_web" if deployments else "server_dev"

    extras: dict[str, Any] = {}
    if detect_samba():
        shares = detect_samba_shares()
        if shares:
            extras["samba_shares"] = shares

    if deployments:
        extras["deploy"] = deployments

    sync_ops = detect_sync_operations()
    if sync_ops:
        extras["sync"] = sync_ops

    scrub_ops = detect_scrub_operations()
    if scrub_ops:
        extras["scrub"] = scrub_ops

    smb_mounts = detect_smb_mounts()
    if smb_mounts:
        extras["mount_smb"] = smb_mounts

    config = SetupConfig.from_dict(host, system_type, config_dict)
    return config, extras


def run_reconstruct_command(compact: bool) -> int:
    """Execute the local reconstruction command."""
    try:
        config, extras = reconstruct_configuration()
        output = {
            "install_go": config.install_go,
            "install_node": config.install_node,
            "install_python": config.install_python,
            "install_data_analysis_tools": config.install_data_analysis_tools,
            "install_av_tools": config.install_av_tools,
            "install_gl_tools": config.install_gl_tools,
            "install_musescore": config.install_musescore,
            "enable_samba": config.enable_samba,
        }
        output.update(extras)
        if compact:
            print(json.dumps(output))
        else:
            print(json.dumps(output, indent=2))
        return 0
    except Exception as exc:
        print(f"Error reconstructing configuration: {exc}")
        return 1
