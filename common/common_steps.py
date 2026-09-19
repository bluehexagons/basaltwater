"""Common setup steps for all system types."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import tempfile
from typing import Optional

from lib.atomic_io import write_text_atomic
from lib.maintenance_systemd import configure_maintenance_timer
from lib.kernel_restart import newer_installed_kernel
from lib.apt_sources import ensure_debian_package_sources
from lib.config import SetupConfig
from lib.maintenance_defaults import APT_LOCK_OPTIONS, APT_UPDATE_OPTIONS
from lib.machine_state import can_manage_time_sync, can_modify_kernel
from lib.remote_utils import (
    file_contains,
    install_package,
    is_dry_run,
    is_package_installed,
    get_user_home,
    run,
)
from lib.update_policy import ECOSYSTEM_AUTO_UPGRADE_ENV, npm_freshness_args
from lib.validation import validate_filesystem_path
from lib.validators import validate_username
from lib.vendor_installer import MAX_INSTALLER_BYTES, installer_command, record_installer


_GO_ARCH_BY_MACHINE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv6l": "armv6l",
    "armv7l": "armv6l",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
    "riscv64": "riscv64",
    "loongarch64": "loong64",
}
_APT_DPKG_NONINTERACTIVE_OPTIONS = (
    "-o Dpkg::Options::=--force-confdef "
    "-o Dpkg::Options::=--force-confold"
)
_APT_GET = f"apt-get {shlex.join(APT_LOCK_OPTIONS)}"
_USER_COMMAND_SYSTEM_PATH = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
_USER_TOOL_SHELL_ENV_RELATIVE_PATH = (
    ".local",
    "share",
    "basaltwater",
    "shell-env.sh",
)
_USER_TOOL_SHELL_MARKER = "# basaltwater user tool environment"
_USER_TOOL_SHELL_ENV = '''# Managed by basaltwater for interactive and agent shells.
case ":$PATH:" in
    *":$HOME/.opencode/bin:"*) ;;
    *) PATH="$HOME/.opencode/bin:$PATH" ;;
esac
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) PATH="$HOME/.local/bin:$PATH" ;;
esac
export PATH

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ] && ! command -v nvm >/dev/null 2>&1; then
    . "$NVM_DIR/nvm.sh"
fi
'''
PACKAGE_UPDATE_MARKER = "/var/lib/basaltwater/state/package-update-complete"
VM_SETUP_SUDOERS_DIR = "/etc/sudoers.d"
CLI_TOOL_PACKAGES = (
    "bc",
    "btop",
    "curl",
    "file",
    "git",
    "htop",
    "jq",
    "less",
    "make",
    "neovim",
    "patch",
    "pv",
    "ripgrep",
    "rsync",
    "sqlite3",
    "tmux",
    "tree",
    "unzip",
    "wget",
    "xdg-utils",
    "zip",
)
DATA_ANALYSIS_PACKAGES = (
    "csvkit",
    "jupyterlab",
    "python3-matplotlib",
    "python3-numpy",
    "python3-pandas",
    "python3-scipy",
)
AV_TOOL_PACKAGES = (
    "ffmpeg",
    "imagemagick",
    "libimage-exiftool-perl",
)
GL_TOOL_PACKAGES = (
    "apitrace",
    "mesa-utils",
)


def _go_release_arch(machine: Optional[str] = None) -> Optional[str]:
    """Return the Go download architecture for a Linux machine name."""
    machine_name = (machine or platform.machine()).strip().lower()
    return _GO_ARCH_BY_MACHINE.get(machine_name)


def _select_go_download(
    payload: object,
    architecture: str,
    requested_version: Optional[str],
) -> tuple[str, str, str]:
    """Return (version, archive, sha256) from the official Go release feed."""
    if not isinstance(payload, list):
        raise RuntimeError("Go release feed did not return an array")
    requested_parts = None
    if requested_version:
        try:
            requested_parts = tuple(int(part) for part in requested_version.split("."))
        except ValueError as exc:
            raise RuntimeError(f"Invalid requested Go version: {requested_version}") from exc
        if len(requested_parts) != 3:
            raise RuntimeError(f"Invalid requested Go version: {requested_version}")

    candidates: list[tuple[tuple[int, int, int], str, str, str]] = []
    for release in payload:
        if not isinstance(release, dict) or not release.get("stable"):
            continue
        version = release.get("version")
        if not isinstance(version, str) or not version.startswith("go"):
            continue
        try:
            version_parts = tuple(int(part) for part in version[2:].split("."))
        except ValueError:
            continue
        if len(version_parts) != 3:
            continue
        if requested_parts and version_parts[:2] != requested_parts[:2]:
            continue
        if requested_parts and version_parts < requested_parts:
            continue
        for file_info in release.get("files", []):
            if not isinstance(file_info, dict):
                continue
            if (
                file_info.get("os") == "linux"
                and file_info.get("arch") == architecture
                and file_info.get("kind") == "archive"
                and isinstance(file_info.get("filename"), str)
                and isinstance(file_info.get("sha256"), str)
            ):
                filename = file_info["filename"]
                checksum = file_info["sha256"].lower()
                if os.path.basename(filename) != filename:
                    continue
                if len(checksum) != 64 or any(
                    character not in "0123456789abcdef" for character in checksum
                ):
                    continue
                candidates.append(
                    (version_parts, version, filename, checksum)
                )
    if not candidates:
        requested = f" for Go {requested_version}" if requested_version else ""
        raise RuntimeError(f"No supported Linux/{architecture} Go archive found{requested}")
    _parts, version, filename, checksum = max(candidates)
    return version, filename, checksum


def set_user_password(username: str, password: str) -> bool:
    process = run(
        "chpasswd",
        check=False,
        capture_output=True,
        input_data=f"{username}:{password}\n",
    )
    if process.returncode != 0:
        print(f"  Warning: Failed to set password: {process.stderr}")
        return False
    return True


def update_and_upgrade_packages(config: SetupConfig) -> None:
    check_debian_package_sources(config)
    if not config.refresh_packages and os.path.exists(PACKAGE_UPDATE_MARKER):
        print(
            "  ✓ System packages already reconciled; skipping APT update/upgrade "
            "(use --refresh-packages to force it)"
        )
        return

    print("  Updating package lists (APT may wait for another package operation)...")
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    update_result = run(
        f"{_APT_GET} {shlex.join(APT_UPDATE_OPTIONS)} -o Dpkg::Use-Pty=0 update -q",
        check=False,
    )
    if update_result.returncode != 0:
        details = getattr(update_result, "stderr", "") or "check network connectivity and APT sources"
        raise RuntimeError(f"APT package list update failed: {str(details).strip()[:300]}")
    print("  Upgrading packages...")
    run(f"{_APT_GET} upgrade -y -qq {_APT_DPKG_NONINTERACTIVE_OPTIONS}")
    run(f"{_APT_GET} autoremove -y -qq {_APT_DPKG_NONINTERACTIVE_OPTIONS}")

    try:
        marker_parent = os.path.dirname(PACKAGE_UPDATE_MARKER)
        os.makedirs(marker_parent, mode=0o755, exist_ok=True)
        with open(PACKAGE_UPDATE_MARKER, "w", encoding="utf-8") as marker:
            marker.write("basaltwater package reconciliation complete\n")
        os.chmod(PACKAGE_UPDATE_MARKER, 0o644)
    except OSError as exc:
        raise RuntimeError(f"Could not record package reconciliation state: {exc}") from exc
    
    print("  ✓ System packages updated and upgraded")


def check_debian_package_sources(config: SetupConfig) -> None:
    """Check and repair Debian APT sources before package operations."""

    del config
    if is_dry_run():
        print("  [DRY-RUN] Would verify Debian APT sources and archive keyring")
        return
    ensure_debian_package_sources()


def ensure_sudo_installed(config: SetupConfig) -> None:
    install_package("sudo", "sudo", f"{_APT_GET} install -y -qq sudo")


def configure_ipv4_preference(config: SetupConfig) -> None:
    """Prefer IPv4 over IPv6 to avoid hangs when IPv6 is present but non-functional."""
    import re
    gai_conf = "/etc/gai.conf"
    marker = "precedence ::ffff:0:0/96  100"

    if is_dry_run():
        print("  [DRY-RUN] Would configure IPv4 preference in /etc/gai.conf")
        return

    if os.path.exists(gai_conf):
        with open(gai_conf, "r") as f:
            content = f.read()
        if re.search(r"^\s*precedence\s+::ffff:0:0/96\s+100", content, re.MULTILINE):
            print("  ✓ IPv4 preference already configured")
            return
        with open(gai_conf, "a") as f:
            f.write(f"\n# Prefer IPv4 to avoid timeouts when IPv6 is non-functional\n{marker}\n")
    else:
        with open(gai_conf, "w") as f:
            f.write(f"# Prefer IPv4 to avoid timeouts when IPv6 is non-functional\n{marker}\n")

    print("  ✓ Configured IPv4 preference in /etc/gai.conf")


def configure_locale(config: SetupConfig) -> None:
    def locale_configured() -> bool:
        return file_contains("/etc/environment", "LANG=en_US.UTF-8")
    
    if locale_configured():
        print("  ✓ UTF-8 locale already configured")
        return

    if is_dry_run():
        print("  [DRY-RUN] Would configure the UTF-8 locale")
        return
    
    install_package("locales", "locales", f"{_APT_GET} install -y -qq locales")
    run("sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen")
    locale_gen_result = run("locale-gen", check=False)
    run("update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8", check=False)
    
    os.environ["LANG"] = "en_US.UTF-8"
    os.environ["LC_ALL"] = "en_US.UTF-8"
    
    with open("/etc/environment", "a+") as f:
        f.seek(0)
        existing = f.read()
        if "LANG=en_US.UTF-8" not in existing:
            f.write('LANG=en_US.UTF-8\nLC_ALL=en_US.UTF-8\n')
    
    if locale_gen_result.returncode == 0:
        print("  ✓ UTF-8 locale configured (en_US.UTF-8)")
    else:
        print("  ⚠ locale-gen failed; locale may not be fully configured")


def setup_user(config: SetupConfig) -> None:
    safe_username = shlex.quote(config.username)
    
    result = run(f"id {safe_username}", check=False)
    user_exists = result.returncode == 0
    
    if not user_exists:
        run(f"useradd -m -s /bin/bash {safe_username}")
        print(f"  Created new user: {config.username}")
        if config.password:
            if set_user_password(config.username, config.password):
                print("  Password set")
        else:
            print("  No password configured; relying on SSH key authentication")
    else:
        print(f"  User already exists: {config.username}")
        if config.password:
            if set_user_password(config.username, config.password):
                print("  Password updated")
    
    if config.harden_agent or config.privilege_broker_port is not None:
        run(f"gpasswd -d {safe_username} sudo", check=False)
        sudo_message = "without sudo privileges"
    else:
        run(f"usermod -aG sudo {safe_username}", check=False)
        sudo_message = (
            "with passwordless sudo privileges"
            if config.nopasswd and config.machine_type == "vm"
            else "with password-protected sudo privileges"
        )
    
    result = run("getent group remoteusers", check=False)
    if result.returncode == 0:
        run(f"usermod -aG remoteusers {safe_username}", check=False)
        user_groups_message = (
            f"  ✓ User configured {sudo_message} and remoteusers group"
        )
    else:
        user_groups_message = f"  ✓ User configured {sudo_message}"

    print(user_groups_message)
    _ensure_vm_setup_user_sudoers(config)


def _ensure_vm_setup_user_sudoers(config: SetupConfig) -> None:
    """Reconcile the VM setup user's optional passwordless sudo rule.

    ``--nopasswd`` requests the capable compatibility behavior. Keeping the
    rule under an basaltwater-owned filename lets root-driven setup reruns add,
    repair, or remove it without changing other policy.
    """
    if config.username == "root":
        return
    if not validate_username(config.username):
        raise ValueError(f"Invalid setup username: {config.username}")
    sudoers_path = os.path.join(
        VM_SETUP_SUDOERS_DIR,
        f"basaltwater-{config.username}",
    )
    sudoers_content = f"{config.username} ALL=(ALL) NOPASSWD:ALL\n"

    if not config.nopasswd or config.machine_type != "vm":
        if is_dry_run():
            print("  [DRY-RUN] Would remove the managed passwordless sudo rule")
            return
        if not os.path.lexists(sudoers_path):
            print("  ✓ Passwordless sudo is not enabled")
            return
        if os.path.islink(sudoers_path) or not os.path.isfile(sudoers_path):
            raise RuntimeError(
                f"Refusing unsafe managed sudoers path: {sudoers_path}"
            )
        with open(sudoers_path, "r", encoding="utf-8") as file_obj:
            if file_obj.read() != sudoers_content:
                raise RuntimeError(
                    f"Refusing to remove modified sudoers policy: {sudoers_path}"
                )
        os.unlink(sudoers_path)
        print("  ✓ Removed managed passwordless sudo rule")
        return

    if is_dry_run():
        print(
            "  [DRY-RUN] Would ensure VM setup sudoers drop-in has mode 0440"
        )
        return

    try:
        existing = os.stat(sudoers_path, follow_symlinks=False)
        if (
            stat.S_ISREG(existing.st_mode)
            and stat.S_IMODE(existing.st_mode) == 0o440
            and existing.st_uid == 0
            and existing.st_gid == 0
        ):
            with open(sudoers_path, "r", encoding="utf-8") as file_obj:
                if file_obj.read() == sudoers_content:
                    print("  ✓ VM setup sudoers already configured (0440)")
                    return
    except (FileNotFoundError, OSError):
        pass

    os.makedirs(VM_SETUP_SUDOERS_DIR, mode=0o755, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=VM_SETUP_SUDOERS_DIR,
        prefix=f".basaltwater-{config.username}-",
        text=True,
    )
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            descriptor_open = False
            file_obj.write(sudoers_content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.chown(temporary_path, 0, 0)
        os.chmod(temporary_path, 0o440)

        validation = run(
            f"visudo -cf {shlex.quote(temporary_path)}",
            check=False,
        )
        if validation.returncode != 0:
            raise RuntimeError(
                f"Generated VM setup sudoers file failed validation: {sudoers_path}"
            )

        os.replace(temporary_path, sudoers_path)
        os.chown(sudoers_path, 0, 0)
        os.chmod(sudoers_path, 0o440)
        print("  ✓ VM setup sudoers configured (root:root, mode 0440)")
    finally:
        if descriptor_open:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def generate_ssh_key(config: SetupConfig) -> None:
    """Generate SSH key pair for user using default algorithm."""
    safe_username = shlex.quote(config.username)
    user_home = f"/home/{config.username}"
    ssh_dir = f"{user_home}/.ssh"
    private_key = f"{ssh_dir}/id_ed25519"
    public_key = f"{private_key}.pub"
    
    if os.path.exists(private_key):
        print(f"  ✓ SSH key already exists for {config.username}")
        return
    
    run(f"mkdir -p {shlex.quote(ssh_dir)}")
    run(f"chmod 700 {shlex.quote(ssh_dir)}")
    
    safe_private_key = shlex.quote(private_key)
    safe_comment = shlex.quote(f"{config.username}@workstation")
    run(f"runuser -u {safe_username} -- ssh-keygen -t ed25519 -f {safe_private_key} -N '' -C {safe_comment}")
    
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(ssh_dir)}")
    run(f"chmod 600 {shlex.quote(private_key)}")
    run(f"chmod 644 {shlex.quote(public_key)}", check=False)
    
    print(f"  ✓ SSH key generated for {config.username} (~/.ssh/id_ed25519)")


def copy_ssh_keys_to_user(config: SetupConfig) -> None:
    safe_username = shlex.quote(config.username)
    user_home = get_user_home(config.username)
    ssh_dir = f"{user_home}/.ssh"
    authorized_keys = f"{ssh_dir}/authorized_keys"
    
    if not os.path.exists("/root/.ssh/authorized_keys"):
        print("  ℹ No SSH keys found in /root/.ssh/authorized_keys to copy")
        return
    if os.path.islink("/root/.ssh/authorized_keys") or not os.path.isfile(
        "/root/.ssh/authorized_keys"
    ):
        raise RuntimeError("Refusing non-regular root authorized_keys path")

    if os.path.abspath(authorized_keys) == "/root/.ssh/authorized_keys":
        run("chmod 700 /root/.ssh", check=False)
        run("chmod 600 /root/.ssh/authorized_keys", check=False)
        print("  ✓ Root SSH keys already available")
        return
    
    if os.path.lexists(ssh_dir) and (
        os.path.islink(ssh_dir) or not os.path.isdir(ssh_dir)
    ):
        raise RuntimeError(f"Refusing non-directory SSH path: {ssh_dir}")
    if os.path.lexists(authorized_keys) and (
        os.path.islink(authorized_keys) or not os.path.isfile(authorized_keys)
    ):
        raise RuntimeError(f"Refusing non-regular authorized_keys path: {authorized_keys}")

    run(f"mkdir -p {shlex.quote(ssh_dir)}")
    run(f"chmod 700 {shlex.quote(ssh_dir)}")

    existing_lines: list[str] = []
    if os.path.exists(authorized_keys):
        with open(authorized_keys, "r", encoding="utf-8") as file_obj:
            existing_lines = file_obj.read().splitlines()
    with open("/root/.ssh/authorized_keys", "r", encoding="utf-8") as file_obj:
        root_lines = file_obj.read().splitlines()

    merged_lines: list[str] = []
    seen: set[str] = set()
    for line in existing_lines + root_lines:
        if line in seen:
            continue
        seen.add(line)
        merged_lines.append(line)
    content = "\n".join(merged_lines)
    if merged_lines:
        content += "\n"
    write_text_atomic(authorized_keys, content, mode=0o600)
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(ssh_dir)}")
    run(f"chmod 600 {shlex.quote(authorized_keys)}")
    
    print(f"  ✓ SSH keys merged for {config.username}")


def configure_time_sync(config: SetupConfig) -> None:
    tz = config.timezone if config.timezone else "UTC"

    if not can_manage_time_sync(config.machine_type):
        for package, service in (
            ("chrony", "chrony"),
            ("systemd-timesyncd", "systemd-timesyncd"),
        ):
            if is_package_installed(package):
                run(f"systemctl disable --now {service}", check=False)
        run(f"timedatectl set-timezone {shlex.quote(tz)}", check=False)
        print(
            "  ✓ Time synchronization managed by container host "
            f"(guest daemons disabled, timezone: {tz})"
        )
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    
    if is_package_installed("systemd-timesyncd"):
        print("  Migrating from systemd-timesyncd to chrony...")
        run("systemctl stop systemd-timesyncd", check=False)
        run("systemctl disable systemd-timesyncd", check=False)
        run(f"{_APT_GET} remove -y -qq systemd-timesyncd", check=False)
        print("  ✓ systemd-timesyncd removed")
    
    install_package("chrony", "chrony", f"{_APT_GET} install -y -qq chrony")
    
    run("systemctl enable chrony", check=False)
    run("systemctl start chrony", check=False)
    
    run(f"timedatectl set-timezone {shlex.quote(tz)}", check=False)
    print(f"  ✓ Time synchronization configured (chrony, timezone: {tz})")


def install_cli_tools(config: SetupConfig) -> None:
    """Install the small command-line baseline shared by development profiles."""

    del config
    missing = [
        package for package in CLI_TOOL_PACKAGES if not is_package_installed(package)
    ]
    if not missing:
        print("  ✓ CLI tools already installed")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    package_args = " ".join(shlex.quote(package) for package in missing)
    result = run(f"{_APT_GET} install -y -qq {package_args}", check=False)
    remaining = [
        package for package in missing if not is_package_installed(package)
    ]
    if result.returncode != 0 or remaining:
        failed = ", ".join(remaining or missing)
        raise RuntimeError(f"CLI tool installation failed: {failed}")

    print(f"  ✓ Installed CLI tools: {', '.join(missing)}")


def ensure_python_alias(config: SetupConfig) -> str | None:
    """Expose ``python3`` as ``python`` when the conventional alias is absent."""
    del config
    if is_dry_run():
        print("  [DRY-RUN] Would ensure the python command alias")
        return None

    python3_path = shutil.which("python3")
    if not python3_path:
        print("  ℹ python3 is unavailable; skipping the python command alias")
        return None

    python_path = shutil.which("python")
    if python_path:
        print("  ✓ python command already available")
        return python3_path

    alias_path = "/usr/local/bin/python"
    if os.path.lexists(alias_path):
        raise RuntimeError(
            f"Cannot create python alias; an unmanaged path already exists: {alias_path}"
        )
    if not os.path.isfile(python3_path):
        raise RuntimeError(f"python3 path is not a regular file: {python3_path}")

    run(f"ln -s {shlex.quote(python3_path)} {shlex.quote(alias_path)}")
    if not os.path.isfile(alias_path):
        raise RuntimeError("python alias could not be verified after creation")
    print("  ✓ Added python alias to python3")
    return python3_path


def install_data_analysis_tools(config: SetupConfig) -> None:
    """Install the opt-in Python data-analysis and notebook bundle."""

    del config
    missing = [
        package
        for package in DATA_ANALYSIS_PACKAGES
        if not is_package_installed(package)
    ]
    if not missing:
        print("  ✓ Data-analysis tools already installed")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    package_args = " ".join(shlex.quote(package) for package in missing)
    result = run(f"{_APT_GET} install -y -qq {package_args}", check=False)
    remaining = [
        package for package in missing if not is_package_installed(package)
    ]
    if result.returncode != 0 or remaining:
        failed = ", ".join(remaining or missing)
        raise RuntimeError(f"Data-analysis tool installation failed: {failed}")

    print(f"  ✓ Installed data-analysis tools: {', '.join(missing)}")


def install_av_tools(config: SetupConfig) -> None:
    """Install the opt-in image, audio, and video processing bundle."""

    del config
    missing = [
        package for package in AV_TOOL_PACKAGES if not is_package_installed(package)
    ]
    if not missing:
        print("  ✓ AV tools already installed")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    package_args = " ".join(shlex.quote(package) for package in missing)
    result = run(f"{_APT_GET} install -y -qq {package_args}", check=False)
    remaining = [package for package in missing if not is_package_installed(package)]
    if result.returncode != 0 or remaining:
        failed = ", ".join(remaining or missing)
        raise RuntimeError(f"AV tool installation failed: {failed}")

    print(f"  ✓ Installed AV tools: {', '.join(missing)}")


def install_gl_tools(config: SetupConfig) -> None:
    """Install the opt-in OpenGL inspection and debugging bundle."""

    del config
    missing = [
        package for package in GL_TOOL_PACKAGES if not is_package_installed(package)
    ]
    if not missing:
        print("  ✓ OpenGL tools already installed")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    package_args = " ".join(shlex.quote(package) for package in missing)
    result = run(f"{_APT_GET} install -y -qq {package_args}", check=False)
    remaining = [package for package in missing if not is_package_installed(package)]
    if result.returncode != 0 or remaining:
        failed = ", ".join(remaining or missing)
        raise RuntimeError(f"OpenGL tool installation failed: {failed}")

    print(f"  ✓ Installed OpenGL tools: {', '.join(missing)}")


CONTROL_PLANE_PACKAGES = (
    "acl",
    "bash-completion",
    "bc",
    "btop",
    "ca-certificates",
    "bind9-dnsutils",
    "fd-find",
    "file",
    "findutils",
    "fzf",
    "git-lfs",
    "iproute2",
    "iputils-ping",
    "jq",
    "less",
    "lsof",
    "netcat-openbsd",
    "neovim",
    "openssh-client",
    "procps",
    "psmisc",
    "ripgrep",
    "rsync",
    "shellcheck",
    "tmux",
    "tree",
    "unzip",
    "wget",
    "zip",
)


def install_control_plane_tools(config: SetupConfig) -> None:
    """Install common Debian administrator and Linux control-plane tools."""
    if is_dry_run():
        print(
            "  [DRY-RUN] Would install control-plane tools: "
            f"{', '.join(CONTROL_PLANE_PACKAGES)}"
        )
        return

    missing = [
        package
        for package in CONTROL_PLANE_PACKAGES
        if not is_package_installed(package)
    ]
    if not missing:
        print("  ✓ Control-plane administrator tools already installed")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    package_args = " ".join(shlex.quote(package) for package in missing)
    result = run(f"{_APT_GET} install -y -qq {package_args}", check=False)
    remaining = [
        package
        for package in missing
        if not is_package_installed(package)
    ]
    if result.returncode != 0 or remaining:
        failed = ", ".join(remaining or missing)
        raise RuntimeError(f"Control-plane tool installation failed: {failed}")

    print(f"  ✓ Installed control-plane tools: {', '.join(missing)}")


def check_restart_required(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would inspect restart markers and installed kernels")
        return

    if os.path.exists("/var/run/reboot-required"):
        print("  ⚠ System restart recommended (kernel/system updates)")
        print("  Run 'sudo reboot' when convenient")
        return
    pending_kernel = newer_installed_kernel() if can_modify_kernel() else None
    if pending_kernel:
        print(
            f"  ⚠ Newer kernel installed: {pending_kernel}; "
            "review boot selection and kernel pins, then schedule a restart"
        )
        return
    print("  ✓ No restart requirement detected")


def _run_as_login_user(
    username: str,
    user_home: str,
    command: str,
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command in the target user's home and systemd session."""
    safe_username = shlex.quote(username)
    safe_home = shlex.quote(user_home)
    safe_path = shlex.quote(
        os.pathsep.join(
            (
                os.path.join(user_home, ".local", "bin"),
                os.path.join(user_home, ".opencode", "bin"),
                _USER_COMMAND_SYSTEM_PATH,
            )
        )
    )
    shell_script = (
        'export XDG_RUNTIME_DIR="/run/user/$(id -u)" '
        'DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" && '
        f"cd {safe_home} && {command}"
    )
    return run(
        f"runuser -u {safe_username} -- env "
        f"HOME={safe_home} USER={safe_username} LOGNAME={safe_username} "
        f"PATH={safe_path} "
        f"bash -lc {shlex.quote(shell_script)}",
        check=check,
        capture_output=capture_output,
    )


def _chown_existing_paths(username: str, paths: list[str]) -> None:
    """Ensure existing user-tool paths belong to the target login user."""
    safe_username = shlex.quote(username)
    existing_paths = [path for path in paths if os.path.exists(path)]
    for path in existing_paths:
        run(f"chown -R {safe_username}:{safe_username} {shlex.quote(path)}", check=False)


def _user_tool_paths(user_home: str) -> list[str]:
    """Return per-user tool paths that should never be root-owned."""
    return [
        os.path.join(user_home, ".nvm"),
        os.path.join(user_home, ".npm"),
        os.path.join(user_home, ".cache"),
        os.path.join(user_home, ".config"),
        os.path.join(user_home, ".local"),
    ]


def _user_tool_shell_env_path(user_home: str) -> str:
    """Return the managed environment file used by target-user Bash shells."""
    return os.path.join(user_home, *_USER_TOOL_SHELL_ENV_RELATIVE_PATH)


def _ensure_directory_chain(user_home: str, directory: str) -> list[str]:
    """Create a home-relative directory without traversing symlinked components."""
    relative = os.path.relpath(directory, user_home)
    if relative == os.pardir or relative.startswith(f"{os.pardir}{os.path.sep}"):
        raise RuntimeError(f"User tool environment is outside the user home: {directory}")

    current = user_home
    directories = []
    for component in relative.split(os.path.sep):
        current = os.path.join(current, component)
        directories.append(current)
        if os.path.lexists(current):
            if os.path.islink(current) or not os.path.isdir(current):
                raise RuntimeError(f"Refusing unsafe user tool directory: {current}")
            continue
        os.mkdir(current, mode=0o755)
    return directories


def _shell_startup_content(path: str) -> tuple[str, int]:
    """Read one regular shell startup file and retain its permissions."""
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            raise RuntimeError(f"Refusing unsafe shell startup file: {path}")
        with open(path, "r", encoding="utf-8") as file_obj:
            return file_obj.read(), stat.S_IMODE(os.stat(path).st_mode)

    skeleton = os.path.join("/etc/skel", os.path.basename(path))
    if os.path.isfile(skeleton) and not os.path.islink(skeleton):
        with open(skeleton, "r", encoding="utf-8") as file_obj:
            return file_obj.read(), stat.S_IMODE(os.stat(skeleton).st_mode)
    return "", 0o644


def _ensure_shell_startup_block(path: str, *, prepend: bool) -> bool:
    """Source the managed user-tool environment from one Bash startup file."""
    content, mode = _shell_startup_content(path)
    if _USER_TOOL_SHELL_MARKER in content:
        return False

    block = (
        f"{_USER_TOOL_SHELL_MARKER}\n"
        'export BASH_ENV="$HOME/.local/share/basaltwater/shell-env.sh"\n'
        '[ -r "$BASH_ENV" ] && . "$BASH_ENV"\n'
    )
    if prepend:
        updated = block + ("\n" if content else "") + content
    else:
        updated = content
        if updated and not updated.endswith("\n"):
            updated += "\n"
        if updated:
            updated += "\n"
        updated += block
    write_text_atomic(path, updated, mode=mode)
    return True


def _ensure_user_tool_shell_environment(username: str, user_home: str) -> None:
    """Expose user-scoped developer tools to interactive and agent Bash shells."""
    if not validate_username(username):
        raise ValueError(f"Invalid shell environment username: {username}")
    validate_filesystem_path(user_home, must_exist=True)
    if is_dry_run():
        print("  [DRY-RUN] Would update the user's tool shell environment")
        return

    environment_path = _user_tool_shell_env_path(user_home)
    environment_dir = os.path.dirname(environment_path)
    environment_dirs = _ensure_directory_chain(user_home, environment_dir)
    if os.path.lexists(environment_path) and (
        os.path.islink(environment_path) or not os.path.isfile(environment_path)
    ):
        raise RuntimeError(f"Refusing unsafe user tool environment: {environment_path}")
    existing_environment = ""
    if os.path.exists(environment_path):
        with open(environment_path, "r", encoding="utf-8") as file_obj:
            existing_environment = file_obj.read()
    if existing_environment != _USER_TOOL_SHELL_ENV:
        write_text_atomic(environment_path, _USER_TOOL_SHELL_ENV, mode=0o644)
    else:
        os.chmod(environment_path, 0o644)

    bashrc_path = os.path.join(user_home, ".bashrc")
    _ensure_shell_startup_block(bashrc_path, prepend=True)

    login_path = os.path.join(user_home, ".profile")
    for name in (".bash_profile", ".bash_login"):
        candidate = os.path.join(user_home, name)
        if os.path.lexists(candidate):
            login_path = candidate
            break
    _ensure_shell_startup_block(login_path, prepend=False)

    safe_username = shlex.quote(username)
    owned_paths = (*environment_dirs, environment_path, bashrc_path, login_path)
    run(
        f"chown {safe_username}:{safe_username} "
        + " ".join(shlex.quote(path) for path in owned_paths)
    )


def _ensure_nvm_shell_init(username: str, user_home: str) -> None:
    """Make nvm available to interactive, login, and non-interactive Bash."""
    _ensure_user_tool_shell_environment(username, user_home)


def install_go(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would install Go")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(f"{_APT_GET} install -y -qq curl wget")
    requested_version = os.environ.get("BASALTWATER_GO_VERSION") or None
    go_binary = "/usr/local/go/bin/go"
    if not os.path.exists(go_binary):
        found_go = shutil.which("go")
        if found_go:
            go_binary = found_go

    if os.path.exists(go_binary) and not config.refresh_packages and not requested_version:
        installed_result = run(
            f"{shlex.quote(go_binary)} version",
            check=False,
            capture_output=True,
        )
        if installed_result.returncode == 0:
            version_parts = installed_result.stdout.strip().split()
            installed_version = version_parts[2] if len(version_parts) >= 3 else "unknown"
            print(
                f"  ✓ Go already installed ({installed_version}); skipping release check "
                "(use --refresh-packages to check for updates)"
            )
            return

    go_arch = _go_release_arch()
    if go_arch is None:
        raise RuntimeError(f"Unsupported Go architecture: {platform.machine()}")
    result = run(
        "curl -fsSL 'https://go.dev/dl/?mode=json&include=all'",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Failed to retrieve the official Go release feed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Official Go release feed returned invalid JSON") from exc
    go_version, go_archive, expected_checksum = _select_go_download(
        payload,
        go_arch,
        requested_version,
    )

    if os.path.exists(go_binary):
        installed_result = run(f"{shlex.quote(go_binary)} version", check=False, capture_output=True)
        if installed_result.returncode == 0:
            version_parts = installed_result.stdout.strip().split()
            installed_version = version_parts[2] if len(version_parts) >= 3 else "unknown"
            if installed_version == go_version:
                print(f"  ✓ Go already up to date ({go_version})")
                return
            print(f"  Updating Go from {installed_version} to {go_version}...")
        else:
            print("  ⚠ Existing Go binary could not report a version; reinstalling")
    
    download_url = f"https://go.dev/dl/{go_archive}"
    with tempfile.TemporaryDirectory(prefix="basaltwater-go-release-") as temporary_dir:
        archive_path = os.path.join(temporary_dir, go_archive)
        run(
            f"wget -q --https-only {shlex.quote(download_url)} "
            f"-O {shlex.quote(archive_path)}"
        )
        checksum_result = run(
            f"sha256sum {shlex.quote(archive_path)}",
            check=False,
            capture_output=True,
        )
        actual_checksum = (
            checksum_result.stdout.strip().split()[0]
            if checksum_result.returncode == 0
            else ""
        )
        if actual_checksum != expected_checksum:
            raise RuntimeError(f"Checksum verification failed for {go_archive}")
        run("rm -rf /usr/local/go")
        run(f"tar -C /usr/local -xzf {shlex.quote(archive_path)}")
    
    profile_d_path = "/etc/profile.d/go.sh"
    with open(profile_d_path, "w") as f:
        f.write('export PATH=$PATH:/usr/local/go/bin\n')
    run(f"chmod +x {profile_d_path}")
    
    print(f"  ✓ Go {go_version} installed")


def _node_baseline_is_usable(
    username: str,
    user_home: str,
    nvm_env: str,
) -> bool:
    """Return whether the nvm default Node, npm, and pnpm commands run."""
    result = _run_as_login_user(
        username,
        user_home,
        f"{nvm_env} && node --version >/dev/null 2>&1 && "
        "npm --version >/dev/null 2>&1 && pnpm --version >/dev/null 2>&1",
        check=False,
    )
    return result.returncode == 0


def install_node_for_user(
    username: str,
    user_home: str,
    *,
    refresh: bool = False,
) -> None:
    """Install nvm-managed Node.js for a specific login/build user."""
    if is_dry_run():
        print("  [DRY-RUN] Would install Node.js for the setup user")
        return

    nvm_dir = f"{user_home}/.nvm"
    safe_nvm_dir = shlex.quote(nvm_dir)
    nvm_sh = os.path.join(nvm_dir, "nvm.sh")

    _chown_existing_paths(username, _user_tool_paths(user_home))
    
    if os.path.exists(nvm_sh):
        _ensure_nvm_shell_init(username, user_home)
        nvm_env = (
            f"export NVM_DIR={safe_nvm_dir} && "
            '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'
        )
        verify_result = _run_as_login_user(
            username,
            user_home,
            f"{nvm_env} && nvm --version",
            check=False,
        )
        _chown_existing_paths(username, _user_tool_paths(user_home))
        if verify_result.returncode == 0:
            print("  ✓ nvm already installed")
        else:
            raise RuntimeError("Existing nvm installation could not be loaded for user")

        if not refresh:
            if _node_baseline_is_usable(username, user_home, nvm_env):
                return

            print("  Repairing Node.js LTS, npm, and pnpm baseline")
            _run_as_login_user(
                username,
                user_home,
                f"{nvm_env} && nvm install --lts && nvm alias default 'lts/*'",
            )
            npm_freshness = shlex.join(npm_freshness_args())
            npm_freshness_suffix = f" {npm_freshness}" if npm_freshness else ""
            _run_as_login_user(
                username,
                user_home,
                f"{nvm_env} && npm install -g pnpm{npm_freshness_suffix}",
            )
            _chown_existing_paths(username, _user_tool_paths(user_home))
            if not _node_baseline_is_usable(username, user_home, nvm_env):
                raise RuntimeError(
                    "Node.js setup could not restore the node, npm, and pnpm baseline"
                )
            print("  ✓ Node.js LTS + NPM + PNPM baseline repaired for user")
            return
        print("  Refreshing Node.js LTS, npm, and pnpm")
        _run_as_login_user(
            username,
            user_home,
            f"{nvm_env} && nvm install --lts && nvm alias default 'lts/*'",
        )
        npm_freshness = shlex.join(npm_freshness_args())
        npm_freshness_suffix = f" {npm_freshness}" if npm_freshness else ""
        _run_as_login_user(
            username,
            user_home,
            f"{nvm_env} && npm install -g npm@latest{npm_freshness_suffix}",
        )
        _run_as_login_user(
            username,
            user_home,
            f"{nvm_env} && npm install -g pnpm{npm_freshness_suffix}",
        )
        _chown_existing_paths(username, _user_tool_paths(user_home))
        if not _node_baseline_is_usable(username, user_home, nvm_env):
            raise RuntimeError(
                "Node.js setup could not verify the refreshed node, npm, and pnpm baseline"
            )
        print("  ✓ Node.js LTS + NPM (latest) + PNPM refreshed for user")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(f"{_APT_GET} install -y -qq curl")

    # Install nvm as the user, explicitly setting NVM_DIR to avoid picking up
    # any system-wide NVM_DIR (e.g. /opt/nvm) from the environment
    result = _run_as_login_user(
        username,
        user_home,
        f"export NVM_DIR={safe_nvm_dir} && "
        + installer_command("nvm"),
        check=False
    )
    _chown_existing_paths(username, _user_tool_paths(user_home))
    if result.returncode != 0 or not os.path.exists(nvm_sh):
        raise RuntimeError("nvm installation failed")
    
    # Install Node.js LTS
    nvm_env = f"export NVM_DIR={safe_nvm_dir} && [ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\""
    _run_as_login_user(
        username,
        user_home,
        f"{nvm_env} && nvm install --lts && nvm alias default 'lts/*'",
    )
    
    # Update npm and install pnpm
    npm_freshness = shlex.join(npm_freshness_args())
    npm_freshness_suffix = f" {npm_freshness}" if npm_freshness else ""
    _run_as_login_user(
        username,
        user_home,
        f"{nvm_env} && npm install -g npm@latest{npm_freshness_suffix}",
    )
    _run_as_login_user(
        username,
        user_home,
        f"{nvm_env} && npm install -g pnpm{npm_freshness_suffix}",
    )
    
    _ensure_nvm_shell_init(username, user_home)
    _chown_existing_paths(username, _user_tool_paths(user_home))
    if not _node_baseline_is_usable(username, user_home, nvm_env):
        raise RuntimeError(
            "Node.js setup could not verify the installed node, npm, and pnpm baseline"
        )
    
    print("  ✓ nvm + Node.js LTS + NPM (latest) + PNPM installed for user")


def install_node(config: SetupConfig) -> None:
    install_node_for_user(
        config.username,
        get_user_home(config.username),
        refresh=config.refresh_packages,
    )


def _validate_uv_install_script(script_path: str) -> bool:
    """Basic validation for uv installer script content before execution."""
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False

    stripped_content = content.lstrip()
    if not stripped_content.startswith("#!/bin/sh") and not stripped_content.startswith("#!/usr/bin/env sh"):
        return False
    if "astral.sh/uv" not in content and "github.com/astral-sh/uv" not in content:
        return False
    if "uv" not in content:
        return False
    suspicious_patterns = ["rm -rf /", "chmod -R 777 /", "mkfs.", "dd if="]
    if any(pattern in content for pattern in suspicious_patterns):
        return False
    return True


def install_or_update_uv(
    user_home: str,
    username: Optional[str] = None,
    *,
    update: bool = True,
) -> bool:
    """Install or update uv for a user. Returns True if uv is available afterwards."""
    uv_path = os.path.join(user_home, ".local", "bin", "uv")
    safe_home = shlex.quote(user_home)
    if username:
        _chown_existing_paths(username, _user_tool_paths(user_home))

    if is_dry_run():
        print("  [DRY-RUN] Skipping uv install/update")
        return True

    if not os.path.exists(uv_path):
        fd, installer_path = tempfile.mkstemp(prefix="basaltwater_uv_install_", suffix=".sh")
        os.close(fd)
        safe_installer = shlex.quote(installer_path)

        try:
            download_result = run(
                [
                    "/usr/bin/prlimit",
                    f"--fsize={MAX_INSTALLER_BYTES}",
                    "--",
                    "curl",
                    "-fsSL",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--tlsv1.2",
                    "--max-time",
                    "120",
                    "--max-filesize",
                    str(MAX_INSTALLER_BYTES),
                    "https://astral.sh/uv/install.sh",
                    "-o",
                    installer_path,
                ],
                check=False
            )
            if download_result.returncode != 0:
                return False

            file_mode = os.stat(installer_path).st_mode & 0o777
            if (file_mode & 0o033) != 0:
                print("  ✗ Downloaded uv installer file permissions are too broad")
                return False

            if not _validate_uv_install_script(installer_path):
                print("  ✗ Downloaded uv installer failed validation")
                return False

            record_installer("uv", installer_path)

            # mkstemp creates a root-only file. The validated installer must
            # be readable by the unprivileged account that executes it.
            if username:
                os.chmod(installer_path, 0o644)

            if username:
                install_result = _run_as_login_user(
                    username,
                    user_home,
                    f"sh {safe_installer}",
                    check=False
                )
            else:
                install_result = run(f"env HOME={safe_home} sh {safe_installer}", check=False)

            if install_result.returncode != 0 or not os.path.exists(uv_path):
                return False
        finally:
            try:
                os.unlink(installer_path)
            except OSError:
                pass

    safe_uv_path = shlex.quote(uv_path)
    if not update and os.path.exists(uv_path):
        print("  ✓ uv already installed; skipping update")
        return True

    if username:
        update_result = _run_as_login_user(
            username,
            user_home,
            f"{safe_uv_path} self update",
            check=False
        )
        _chown_existing_paths(username, _user_tool_paths(user_home))
    else:
        update_result = run(f"env HOME={safe_home} {safe_uv_path} self update", check=False)

    if update_result.returncode == 0:
        print("  ✓ uv updated")
    else:
        print("  ⚠ uv update failed")

    return os.path.exists(uv_path)


def install_python(config: SetupConfig) -> None:
    """Install Python tooling (aliases and uv)."""
    if is_dry_run():
        print("  [DRY-RUN] Would install Python tooling")
        return

    user_home = f"/home/{config.username}"

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(f"{_APT_GET} install -y -qq python3 python3-venv curl")

    python3_path = ensure_python_alias(config)
    if python3_path:
        print("  ✓ python3 command already available")
    else:
        raise RuntimeError("python3 command unavailable after package installation")

    uv_preexisting = os.path.exists(f"{user_home}/.local/bin/uv")
    if install_or_update_uv(
        user_home=user_home,
        username=config.username,
        update=not uv_preexisting or config.refresh_packages,
    ):
        if uv_preexisting:
            print("  ✓ uv already installed")
        else:
            print("  ✓ uv installed")
    else:
        raise RuntimeError("uv installation failed")

    print("  ℹ Remote systems skip shell autocompletion setup")


def configure_auto_update_uv(config: SetupConfig) -> None:
    """Configure automatic updates for uv."""
    user_home = f"/home/{config.username}"
    uv_path = f"{user_home}/.local/bin/uv"

    configure_maintenance_timer(
        service_name="auto-update-uv",
        service_desc="Auto-update uv package manager",
        timer_desc="Auto-update uv weekly",
        script_path="/opt/basaltwater/common/service_tools/auto_update_uv.py",
        schedule="Sun *-*-* 05:00:00",
        check_path=uv_path,
        check_name="uv",
        user=config.username,
        environment={ECOSYSTEM_AUTO_UPGRADE_ENV: "0"},
        purpose="auto-update",
    )


def configure_auto_update_gogs(config: SetupConfig) -> None:
    """Configure automatic updates for Gogs."""
    configure_maintenance_timer(
        service_name="auto-update-gogs",
        service_desc="Auto-update Gogs service",
        timer_desc="Auto-update Gogs weekly",
        script_path="/opt/basaltwater/common/service_tools/auto_update_gogs.py",
        schedule="Sun *-*-* 05:30:00",
        check_path="/usr/local/bin/gogs",
        check_name="Gogs",
        purpose="auto-update",
    )


def configure_auto_update_homebox(config: SetupConfig) -> None:
    """Configure automatic HomeBox updates after a successful service setup."""
    del config
    configured = configure_maintenance_timer(
        service_name="auto-update-homebox",
        service_desc="Auto-update HomeBox inventory service",
        timer_desc="Auto-update HomeBox weekly",
        script_path="/opt/basaltwater/common/service_tools/auto_update_homebox.py",
        schedule="Sun *-*-* 06:00:00",
        check_path="/opt/homebox/current/homebox",
        check_name="HomeBox",
        purpose="auto-update",
    )
    if not configured:
        raise RuntimeError("HomeBox auto-update timer failed verification")


def install_mail_utils(config: SetupConfig) -> None:
    """Install mail utilities for email notifications."""
    # Only install if mailbox notifications are configured
    if not config.notify_specs:
        return
    
    has_mailbox = any(spec[0] == 'mailbox' for spec in config.notify_specs)
    if not has_mailbox:
        print("  ℹ No mailbox notifications configured, skipping mail utilities")
        return
    
    if is_package_installed("bsd-mailx"):
        print("  ✓ Mail utilities already installed")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(f"{_APT_GET} install -y -qq bsd-mailx")
    
    print("  ✓ Mail utilities installed")


def install_apt_packages(config: SetupConfig) -> None:
    """Install additional packages via apt."""
    if not config.apt_packages:
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    print("  Installing custom apt packages...")
    for package in config.apt_packages:
        if is_package_installed(package):
            print(f"  ✓ {package} already installed")
        else:
            print(f"  Installing {package}...")
            run(f"{_APT_GET} install -y -qq {shlex.quote(package)}", check=False)
            if is_package_installed(package):
                print(f"  ✓ {package} installed")
            else:
                print(f"  ⚠ Failed to install {package}")


def install_flatpak_packages(config: SetupConfig) -> None:
    """Install additional packages via flatpak."""
    if not config.flatpak_packages:
        return
    
    from lib.machine_state import is_container
    from desktop.apps_steps import is_flatpak_installed, install_flatpak_if_needed, is_flatpak_app_installed
    
    # In containers, flatpak often doesn't work
    if is_container():
        print("  ⚠ Container detected: skipping flatpak package installation")
        return
    
    # Ensure flatpak is installed
    install_flatpak_if_needed()
    
    if not is_flatpak_installed():
        print("  ⚠ Flatpak not available, skipping flatpak package installation")
        return
    
    print("  Installing custom flatpak packages...")
    FLATPAK_REMOTE = "flathub"
    
    for package in config.flatpak_packages:
        if is_flatpak_app_installed(package):
            print(f"  ✓ {package} already installed")
        else:
            print(f"  Installing {package}...")
            run(f"flatpak install -y {FLATPAK_REMOTE} {shlex.quote(package)}", check=False)
            # Verify installation
            if is_flatpak_app_installed(package):
                print(f"  ✓ {package} installed")
            else:
                print(f"  ⚠ Failed to install {package}")
