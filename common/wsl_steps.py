"""Ubuntu WSL preflight and small userspace package baseline."""

from __future__ import annotations

import os
import platform
import pwd
from pathlib import Path
import shutil

from lib.config import SetupConfig
from lib.machine_state import detect_machine_type
from lib.remote_utils import is_dry_run, run
from lib.validators import validate_username


def preflight_wsl(config: SetupConfig) -> None:
    if config.system_type != "server_wsl" or config.machine_type != "wsl":
        raise ValueError("server_wsl requires the wsl machine type")
    if not validate_username(config.username) or config.username == "root":
        raise ValueError("server_wsl requires an existing non-root Ubuntu user")
    if config.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("server_wsl setup runs inside the selected local distribution")
    if any((config.web_interfaces, config.t3code_desktop, config.enable_rdp,
            config.storage_mounts, config.swap_files, config.swap_devices,
            config.is_app_server, config.enable_ssl, config.enable_cloudflare)):
        raise ValueError("server_wsl does not support desktop, web, VM storage, or Linux swap options")
    if config.is_build_server or config.enable_cicd:
        raise ValueError("Use the Windows/WSL job runner; the Linux webhook profile is not qualified in WSL")
    if is_dry_run():
        return
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or detect_machine_type() != "wsl":
        raise ValueError("server_wsl requires Ubuntu running in WSL 2")
    if os.geteuid() != 0:
        raise ValueError("Run Ubuntu setup as root through wsl.exe --user root")
    try:
        pwd.getpwnam(config.username)
    except KeyError as exc:
        raise ValueError("Create the requested Ubuntu user before setup") from exc
    if Path("/proc/1/comm").read_text(encoding="utf-8").strip() != "systemd":
        raise ValueError("Enable systemd in /etc/wsl.conf and restart this distribution")


def install_wsl_base(config: SetupConfig) -> None:
    del config
    if is_dry_run():
        return
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "update", "-qq"])
    run(["apt-get", "upgrade", "-y", "-qq"])
    run(["apt-get", "install", "-y", "-qq", "ca-certificates", "curl", "git",
         "git-lfs", "python3", "python3-venv", "build-essential", "unzip", "ripgrep",
         "sudo"])


def report_wsl_readiness(config: SetupConfig) -> None:
    commands = ("git", "rg", "python3")
    missing = [name for name in commands if not shutil.which(name)]
    if missing and not is_dry_run():
        raise RuntimeError(f"Missing WSL tools: {', '.join(missing)}")
    print(f"  Ubuntu WSL tools ready for {config.username}")
