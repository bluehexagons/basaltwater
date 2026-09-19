"""Desktop and workstation setup steps."""

from __future__ import annotations

import os
import shlex
import tempfile

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.machine_state import is_container
from lib.remote_utils import install_package, is_package_installed, run
from lib.validation import validate_filesystem_path
from desktop.browser_steps import is_flatpak_app_installed


FLATPAK_REMOTE = "flathub"
MICROSOFT_KEY_URL = "https://packages.microsoft.com/keys/microsoft.asc"
MICROSOFT_KEY_FINGERPRINT = "BC528686B50D79E339D3721CEB3E94ADBE1229CF"
VSCODE_KEYRING = "/usr/share/keyrings/basaltwater-microsoft.gpg"
VSCODE_SOURCES = "/etc/apt/sources.list.d/basaltwater-vscode.sources"
VSCODE_SOURCE_CONTENT = f"""Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64 arm64 armhf
Signed-By: {VSCODE_KEYRING}
"""


def is_flatpak_installed() -> bool:
    """Check if flatpak is installed."""
    result = run("which flatpak", check=False)
    return result.returncode == 0


def install_flatpak_if_needed() -> bool:
    """Install flatpak if not already installed.
    
    Returns:
        True if flatpak is available, False if installation failed or not recommended.
    """
    if is_container():
        print("  ⚠ Warning: Flatpak typically does not work well in unprivileged containers")
        print("    Consider using --machine vm or --machine hardware if Flatpak is needed")
    
    if is_flatpak_installed():
        return True

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    result = run("apt-get install -y -qq flatpak", check=False)
    if result.returncode != 0:
        print("  ⚠ Failed to install Flatpak")
        return False
    
    run(f"flatpak remote-add --if-not-exists {FLATPAK_REMOTE} https://flathub.org/repo/flathub.flatpakrepo", check=False)
    return True



def install_remmina(config: SetupConfig) -> None:
    """Install Remmina RDP client."""
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq remmina remmina-plugin-rdp remmina-plugin-vnc", check=False)
    if is_package_installed("remmina"):
        print("  ✓ Remmina installed/updated")


def install_office_apps(config: SetupConfig) -> None:
    """Install office suite (LibreOffice)."""
    if not config.install_office:
        return

    if config.use_flatpak:
        if not install_flatpak_if_needed():
            print("  Falling back to apt for LibreOffice installation")
            config.use_flatpak = False
        elif is_flatpak_app_installed("org.libreoffice.LibreOffice"):
            print("  ✓ LibreOffice already installed via Flatpak")
            return
        else:
            print("  Installing LibreOffice via Flatpak...")
            run(f"flatpak install -y {FLATPAK_REMOTE} org.libreoffice.LibreOffice", check=False)
            if is_flatpak_app_installed("org.libreoffice.LibreOffice"):
                print("  ✓ LibreOffice installed via Flatpak")
            return
    
    if is_package_installed("libreoffice"):
        print("  ✓ LibreOffice already installed")
        return
    print("  Installing LibreOffice...")
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq libreoffice", check=False)
    if is_package_installed("libreoffice"):
        print("  ✓ LibreOffice installed")


def _microsoft_key_fingerprints(output: str) -> set[str]:
    """Extract normalized fingerprints from GnuPG colon output."""

    fingerprints: set[str] = set()
    for line in output.splitlines():
        fields = line.split(":")
        if fields[0] == "fpr" and len(fields) > 9 and fields[9]:
            fingerprints.add(fields[9].upper())
    return fingerprints


def _install_vscode() -> None:
    """Install VS Code from Microsoft's explicitly scoped signed APT source."""

    if is_package_installed("code") or os.path.exists("/usr/bin/code"):
        print("  ✓ Visual Studio Code already installed")
        return

    for path in (VSCODE_KEYRING, VSCODE_SOURCES):
        validate_filesystem_path(path)
        if os.path.islink(path):
            raise RuntimeError(
                f"refusing symlinked VS Code configuration path: {path}"
            )

    dependencies = run(
        "apt-get install -y -qq ca-certificates wget gpg",
        check=False,
    )
    if dependencies.returncode != 0:
        raise RuntimeError("Visual Studio Code repository dependencies failed")

    with tempfile.TemporaryDirectory(prefix="basaltwater-vscode-") as temporary_dir:
        key_path = os.path.join(temporary_dir, "microsoft.asc")
        dearmored_path = os.path.join(temporary_dir, "microsoft.gpg")
        download = run(
            f"wget --https-only -qO {shlex.quote(key_path)} "
            f"{shlex.quote(MICROSOFT_KEY_URL)}",
            check=False,
        )
        if download.returncode != 0:
            raise RuntimeError("could not download the Microsoft repository key")

        inspection = run(
            f"gpg --batch --with-colons --show-keys {shlex.quote(key_path)}",
            check=False,
            capture_output=True,
        )
        if (
            inspection.returncode != 0
            or MICROSOFT_KEY_FINGERPRINT
            not in _microsoft_key_fingerprints(inspection.stdout or "")
        ):
            raise RuntimeError("Microsoft repository key fingerprint did not match")

        dearmor = run(
            "gpg --batch --yes --dearmor "
            f"--output {shlex.quote(dearmored_path)} {shlex.quote(key_path)}",
            check=False,
        )
        if dearmor.returncode != 0:
            raise RuntimeError("could not prepare the Microsoft repository key")

        install_key = run(
            "install -o root -g root -m 0644 "
            f"{shlex.quote(dearmored_path)} {shlex.quote(VSCODE_KEYRING)}",
            check=False,
        )
        if install_key.returncode != 0:
            raise RuntimeError("could not install the Microsoft repository key")

    write_text_atomic(VSCODE_SOURCES, VSCODE_SOURCE_CONTENT, mode=0o644)
    update = run("apt-get update -qq", check=False)
    if update.returncode != 0:
        raise RuntimeError("could not refresh the Visual Studio Code repository")

    install = run("apt-get install -y -qq code", check=False)
    if install.returncode != 0 or not (
        is_package_installed("code") or os.path.exists("/usr/bin/code")
    ):
        raise RuntimeError("Visual Studio Code installation failed")
    print("  ✓ Visual Studio Code installed")


def install_editor(config: SetupConfig) -> None:
    """Install the explicitly selected graphical editor."""

    if config.editor == "geany":
        if not install_package(
            "Geany",
            "geany",
            "apt-get install -y -qq geany",
        ):
            raise RuntimeError("Geany installation failed")
        return
    if config.editor == "vscode":
        _install_vscode()
        return
    raise RuntimeError("No supported graphical editor was selected")
