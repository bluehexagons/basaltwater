"""Desktop and workstation setup steps."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Optional

from desktop.home_setup import apply_home_settings
from lib.config import SetupConfig
from lib.machine_state import is_container
from lib.remote_utils import (
    is_dry_run,
    is_package_installed,
    run,
)


FLATPAK_REMOTE = "flathub"
_apt_update_done = False
_EXTREPO_SOURCE_DIR = "/etc/apt/sources.list.d"
_MANAGED_EXTREPOS = ("brave", "librewolf", "vscode")
_EXTREPO_UPDATE_TIMEOUT_SECONDS = 30
_APT_UPDATE_COMMAND = (
    "apt-get -o DPkg::Lock::Timeout=120 "
    "-o Acquire::Retries=0 -o Acquire::http::Timeout=15 "
    "-o Acquire::https::Timeout=15 update -qq"
)
_HELIUM_VERSION = "0.21.3.1"
_HELIUM_SHA256 = {
    "amd64": "98f940589caa46357218ee6c6b95a6b5d6681b4f33c72e4189c62d155a38145b",
    "arm64": "be6e69a99491d21d88edb6cf61b3ab0f207c7d7ea4c4d2cc0aa75060542435e5",
}
_BROWSH_VERSION = "1.8.2"
_BROWSH_SHA256 = {
    "amd64": "9333a32ce4adf3d5436e599c1ec7b4375d862e9d72cb93f600f586111775416b",
    "arm64": "086ecc93cb0238a8f390128443a2d66a3a3be302f4a1b3bb9942f3d2c162273a",
    "armv7": "46bb529e989eb0ac232eab9d85d8456d582128b4d2b833dbeffc14c20a196c05",
    "386": "328e151a6bddf9f194b6dd4c2a0d1424c472ff596ba2ababf9a8b7cb0a4d34af",
}
_LIBREWOLF_APPARMOR_PROFILE = "/etc/apparmor.d/librewolf"
_XFCE_HELPER_SUBDIR = os.path.join(".local", "share", "xfce4", "helpers")
_LIBREWOLF_APPARMOR_PROFILE_CONTENT = """# Managed by basaltwater.
abi <abi/4.0>,
include <tunables/global>

profile librewolf /usr/share/librewolf/{librewolf,librewolf-bin} flags=(unconfined) {
        userns,
        include if exists <local/librewolf>
}
"""
_BROWSH_ARCH_BY_DPKG = {
    "amd64": "amd64",
    "arm64": "arm64",
    "armhf": "armv7",
    "i386": "386",
}


_FLATPAK_BROWSER_IDS = {
    "brave": "com.brave.Browser",
    "firefox": "org.mozilla.firefox",
    "librewolf": "io.gitlab.librewolf-community",
}
_NATIVE_BROWSER_COMMANDS = {
    "brave": "brave-browser",
    "firefox": "firefox",
    "helium": "helium",
    "librewolf": "librewolf",
}


def _configure_librewolf_apparmor_profile() -> None:
    """Write and load the managed LibreWolf AppArmor compatibility profile.

    LibreWolf's own browser sandbox remains responsible for confinement while
    the AppArmor label avoids blocking its dynamic loader and user namespaces.
    Workstation setup owns this small package-profile replacement; older
    workstation state is intentionally outside the supported upgrade path.
    """
    if not os.path.isfile(_LIBREWOLF_APPARMOR_PROFILE):
        return
    if not shutil.which("aa-enabled") or not shutil.which("apparmor_parser"):
        print(
            "  ⚠ AppArmor tooling is unavailable; "
            "skipping LibreWolf profile reload"
        )
        return
    if run("aa-enabled -q", check=False).returncode != 0:
        return
    run(
        f"apparmor_parser -R {shlex.quote(_LIBREWOLF_APPARMOR_PROFILE)}",
        check=False,
    )
    try:
        with open(_LIBREWOLF_APPARMOR_PROFILE, "w", encoding="utf-8") as f:
            f.write(_LIBREWOLF_APPARMOR_PROFILE_CONTENT)
    except OSError:
        print("  ⚠ Could not write LibreWolf's managed AppArmor profile")
        return
    result = run(
        f"apparmor_parser -r -W {shlex.quote(_LIBREWOLF_APPARMOR_PROFILE)}",
        check=False,
    )
    if result.returncode != 0:
        print("  ⚠ Could not reload LibreWolf's unconfined AppArmor profile")


def _browser_architecture(name: str) -> str:
    result = run("dpkg --print-architecture", check=False, capture_output=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"{name} installation failed: could not determine Debian architecture")
    return result.stdout.strip()


def _installed_browser_version(package: str) -> str:
    result = run(
        f"dpkg-query -W -f='${{Version}}' {package}", check=False, capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Could not determine the installed {package} version")
    return result.stdout.strip()


def _install_pinned_browser(
    name: str, package: str, version: str, architecture: str, url: str, expected_hash: str,
) -> None:
    """Check the complete package before APT can start an install or upgrade."""
    if is_package_installed(package):
        installed = _installed_browser_version(package)
        comparison = run(
            f"dpkg --compare-versions {shlex.quote(installed)} ge {shlex.quote(version)}",
            check=False,
        )
        if comparison.returncode == 0:
            print(f"  ✓ {name} {installed} already installed")
            return
        if comparison.returncode != 1:
            raise RuntimeError(f"{name} installation failed: could not compare package versions")

    print(f"  Installing {name} {version}...")
    with tempfile.TemporaryDirectory(prefix=f"basaltwater-{package}-") as temporary_dir:
        package_path = Path(temporary_dir, f"{package}.deb")
        destination = shlex.quote(str(package_path))
        result = run(
            f"curl --disable --fail --silent --show-error --location "
            f"--proto '=https' --proto-redir '=https' --max-time 300 "
            f"--output {destination} {shlex.quote(url)}",
            check=False,
        )
        if result.returncode != 0 or not package_path.is_file():
            raise RuntimeError(f"{name} installation failed: package download failed")
        with package_path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != expected_hash:
                raise RuntimeError(f"{name} installation failed: package SHA-256 mismatch")
        fields = run(
            f"dpkg-deb --field {destination} Package Version Architecture",
            check=False, capture_output=True,
        )
        expected = [f"Package: {package}", f"Version: {version}", f"Architecture: {architecture}"]
        if fields.returncode != 0 or (fields.stdout or "").splitlines() != expected:
            raise RuntimeError(f"{name} installation failed: package metadata does not match the approved release")
        result = run(f"apt-get install -y -qq {destination}", check=False)
    if result.returncode != 0 or not is_package_installed(package) or _installed_browser_version(package) != version:
        raise RuntimeError(f"{name} installation failed: package command failed or expected version is missing")
    print(f"  ✓ {name} {version} installed")


def is_flatpak_app_installed(app_id: str) -> bool:
    """Check if a Flatpak application is installed."""
    result = subprocess.run(
        ["flatpak", "list", "--app", "--columns=application"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return app_id in result.stdout.splitlines()


def _refresh_existing_extrepo_sources() -> bool:
    """Refresh extrepo source definitions that are already enabled locally.

    extrepo source definitions are versioned independently from the installed
    packages.  A workstation created with older extrepo metadata can
    otherwise retain a stale LibreWolf definition and make an unrelated APT
    refresh fail.  Only the source definitions owned by extrepo are refreshed;
    manually managed APT source files are deliberately left untouched.
    """
    refreshed_any = False
    for extrepo_name in _MANAGED_EXTREPOS:
        source_path = os.path.join(
            _EXTREPO_SOURCE_DIR, f"extrepo_{extrepo_name}.sources"
        )
        if not os.path.isfile(source_path):
            continue
        refreshed_any = True
        result = run(
            f"timeout --kill-after=5s {_EXTREPO_UPDATE_TIMEOUT_SECONDS}s "
            f"extrepo update {shlex.quote(extrepo_name)}",
            check=False,
        )
        if result.returncode in (124, 137):
            print(
                f"  ⚠ Timed out refreshing the extrepo {extrepo_name} "
                f"source definition after {_EXTREPO_UPDATE_TIMEOUT_SECONDS}s"
            )
            continue
        if result.returncode != 0:
            print(
                f"  ⚠ Could not refresh the extrepo {extrepo_name} "
                "source definition; continuing with the existing source"
            )
    return refreshed_any


def _update_apt_metadata() -> subprocess.CompletedProcess[str]:
    """Refresh APT metadata with bounded network and lock waits."""
    return run(_APT_UPDATE_COMMAND, check=False)


def _ensure_extrepo_and_update() -> None:
    """Install extrepo if needed and run apt-get update only once."""
    global _apt_update_done
    if not is_package_installed("extrepo"):
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq extrepo", check=False)
    if not _apt_update_done:
        update_result = _update_apt_metadata()
        if update_result.returncode != 0:
            print("  ⚠ APT metadata refresh failed; checking extrepo definitions")
            if _refresh_existing_extrepo_sources():
                retry_result = _update_apt_metadata()
                if retry_result.returncode != 0:
                    print(
                        "  ⚠ APT metadata refresh still reports an error; "
                        "continuing so the requested package can be verified"
                    )
            else:
                print(
                    "  ⚠ No managed extrepo definitions were found; "
                    "continuing so the requested package can be verified"
                )
        _apt_update_done = True


def _install_via_extrepo(name: str, extrepo_name: str, package_name: str) -> bool:
    """Install a package via extrepo. Returns True if successful."""
    _ensure_extrepo_and_update()
    run(f"extrepo enable {extrepo_name}", check=False)
    _update_apt_metadata()
    sources_path = f"/etc/apt/sources.list.d/extrepo_{extrepo_name}.sources"
    if os.path.exists(sources_path):
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        result = run(f"apt-get install -y -qq {package_name}", check=False)
        if result.returncode == 0 and is_package_installed(package_name):
            return True
        print(f"  ✗ Failed to install {name} (package not found after install)")
    else:
        print(f"  ✗ Failed to enable {name} repository")
    return False


def _install_helium_browser() -> None:
    arch = _browser_architecture("Helium")
    if arch not in _HELIUM_SHA256:
        raise RuntimeError(f"Helium installation failed: no approved package for {arch}")
    version = f"{_HELIUM_VERSION}-1"
    url = f"https://github.com/imputnet/helium-linux/releases/download/{_HELIUM_VERSION}/helium-bin_{version}_{arch}.deb"
    _install_pinned_browser("Helium", "helium-bin", version, arch, url, _HELIUM_SHA256[arch])


def install_single_browser(browser: str, use_flatpak: bool) -> None:
    """Install a single browser."""
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    if browser == "brave":
        if use_flatpak:
            if is_flatpak_app_installed("com.brave.Browser"):
                print("  ✓ Brave browser already installed")
                return
            print("  Installing Brave browser...")
            result = run(f"flatpak install -y {FLATPAK_REMOTE} com.brave.Browser", check=False)
            if result.returncode == 0 and is_flatpak_app_installed("com.brave.Browser"):
                print("  ✓ Brave browser installed")
                return
            print("  ⚠ Flatpak install failed, falling back to apt...")
        
        if is_package_installed("brave-browser"):
            print("  ✓ Brave browser already installed")
            return
        print("  Installing Brave browser...")
        if _install_via_extrepo("Brave", "brave", "brave-browser"):
            print("  ✓ Brave browser installed")
        else:
            raise RuntimeError("Brave installation failed")
    
    elif browser == "firefox":
        if use_flatpak:
            if is_flatpak_app_installed("org.mozilla.firefox"):
                print("  ✓ Firefox already installed")
                return
            print("  Installing Firefox...")
            result = run(f"flatpak install -y {FLATPAK_REMOTE} org.mozilla.firefox", check=False)
            if result.returncode == 0 and is_flatpak_app_installed("org.mozilla.firefox"):
                print("  ✓ Firefox installed")
                return
            raise RuntimeError("Firefox Flatpak installation failed")
        else:
            if is_package_installed("firefox") or is_package_installed("firefox-esr"):
                print("  ✓ Firefox already installed")
                return
            print("  Installing Firefox...")
            result = run("apt-get install -y -qq firefox-esr", check=False)
            if result.returncode == 0 and (is_package_installed("firefox") or is_package_installed("firefox-esr")):
                print("  ✓ Firefox installed")
            else:
                raise RuntimeError("Firefox installation failed")
        

    elif browser == "librewolf":
        if use_flatpak:
            if is_flatpak_app_installed("io.gitlab.librewolf-community"):
                print("  ✓ LibreWolf browser already installed")
                return
            print("  Installing LibreWolf browser...")
            result = run(f"flatpak install -y {FLATPAK_REMOTE} io.gitlab.librewolf-community", check=False)
            if result.returncode == 0 and is_flatpak_app_installed("io.gitlab.librewolf-community"):
                print("  ✓ LibreWolf browser installed")
                return
            raise RuntimeError("LibreWolf Flatpak installation failed")
        else:
            if is_package_installed("librewolf"):
                _configure_librewolf_apparmor_profile()
                print("  ✓ LibreWolf browser already installed")
                return
            print("  Installing LibreWolf browser...")

            if _install_via_extrepo("LibreWolf", "librewolf", "librewolf"):
                _configure_librewolf_apparmor_profile()
                print("  ✓ LibreWolf browser installed")
            else:
                raise RuntimeError("LibreWolf installation failed")

    elif browser == "helium":
        _install_helium_browser()
    
    elif browser == "browsh":
        if not (is_package_installed("firefox") or is_package_installed("firefox-esr")):
            print("  Installing Firefox (required for Browsh)...")
            result = run("apt-get install -y -qq firefox-esr", check=False)
            if result.returncode != 0 or not (is_package_installed("firefox") or is_package_installed("firefox-esr")):
                raise RuntimeError("Browsh installation failed: Firefox dependency is unavailable")
        dpkg_arch = _browser_architecture("Browsh")
        if dpkg_arch not in _BROWSH_ARCH_BY_DPKG:
            raise RuntimeError(f"Browsh installation failed: no approved package for {dpkg_arch}")
        arch = _BROWSH_ARCH_BY_DPKG[dpkg_arch]
        url = f"https://github.com/browsh-org/browsh/releases/download/v{_BROWSH_VERSION}/browsh_{_BROWSH_VERSION}_linux_{arch}.deb"
        _install_pinned_browser("Browsh", "browsh", _BROWSH_VERSION, dpkg_arch, url, _BROWSH_SHA256[arch])
    
    elif browser == "lynx":
        if is_package_installed("lynx"):
            print("  ✓ Lynx already installed")
            return
        print("  Installing Lynx...")
        result = run("apt-get install -y -qq lynx", check=False)
        if result.returncode == 0 and is_package_installed("lynx"):
            print("  ✓ Lynx installed")
        else:
            raise RuntimeError("Lynx installation failed")
    else:
        raise ValueError(f"Unsupported browser: {browser}")


def install_browser(config: SetupConfig) -> None:
    """Install the specified browser(s)."""
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would install the selected browsers and verify installation")
        return
    # In containers, prefer apt over Flatpak since Flatpak often doesn't work
    use_flatpak = config.use_flatpak
    if use_flatpak and is_container():
        print("  ⚠ Container detected: using apt instead of Flatpak for browser")
        use_flatpak = False
    
    # Install multiple browsers if specified
    if config.browsers:
        for browser in config.browsers:
            install_single_browser(browser, use_flatpak)
    elif config.browser:
        install_single_browser(config.browser, use_flatpak)


def _xfce_browser_helper(browser: str, command: str, *, flatpak: bool) -> str:
    binaries = "flatpak" if flatpak else command
    executable = command if flatpak else "%B"
    return f"""[Desktop Entry]
Version=1.0
Type=X-XFCE-Helper
Name={browser.capitalize()}
StartupNotify=true
X-XFCE-Binaries={binaries};
X-XFCE-Category=WebBrowser
X-XFCE-Commands={executable};
X-XFCE-CommandsWithParameter={executable} "%s";
"""


def configure_default_browser(config: SetupConfig) -> None:
    if not config.browser:
        return

    if is_dry_run():
        print(f"  [DRY-RUN] Would set {config.browser} as the default browser")
        return

    browser_desktops: dict[str, Optional[str]] = {
        "brave": "brave-browser.desktop",
        "firefox": "firefox.desktop",
        "helium": "helium.desktop",
        "librewolf": "librewolf.desktop",
        "lynx": None,
        "browsh": None
    }
    
    desktop_file = browser_desktops.get(config.browser)
    if not desktop_file:
        print(f"  ✓ No default browser configuration needed for {config.browser}")
        return

    flatpak_app_id = _FLATPAK_BROWSER_IDS.get(config.browser)
    using_flatpak_browser = bool(
        config.use_flatpak
        and flatpak_app_id
        and is_flatpak_app_installed(flatpak_app_id)
    )
    if using_flatpak_browser:
        desktop_file = f"{flatpak_app_id}.desktop"
    
    mimeapps_content = f"""[Default Applications]
x-scheme-handler/http={desktop_file}
x-scheme-handler/https={desktop_file}
x-scheme-handler/about={desktop_file}
x-scheme-handler/unknown={desktop_file}
text/html={desktop_file}
application/xhtml+xml={desktop_file}
"""
    browser_command = _NATIVE_BROWSER_COMMANDS[config.browser]
    if using_flatpak_browser:
        browser_command = f"flatpak run {flatpak_app_id}"

    edits = [
        ("write", ".config/mimeapps.list", mimeapps_content),
        ("write", f"{_XFCE_HELPER_SUBDIR}/{config.browser}.desktop",
         _xfce_browser_helper(config.browser, browser_command, flatpak=using_flatpak_browser)),
        ("write", ".config/xfce4/helpers.rc", f"WebBrowser={browser_command}\n"),
    ]
    for browser, command in _NATIVE_BROWSER_COMMANDS.items():
        if browser == config.browser:
            continue
        path = f"{_XFCE_HELPER_SUBDIR}/{browser}.desktop"
        edits.append(("retire", path, _xfce_browser_helper(browser, command, flatpak=False)))
        if browser in _FLATPAK_BROWSER_IDS:
            edits.append(("retire", path, _xfce_browser_helper(
                browser, f"flatpak run {_FLATPAK_BROWSER_IDS[browser]}", flatpak=True,
            )))
    apply_home_settings(config.username, edits)
    
    print(f"  ✓ Default browser set to {config.browser.capitalize()}")
