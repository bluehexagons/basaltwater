"""Desktop and workstation setup steps."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from typing import Optional

from lib.config import SetupConfig
from lib.machine_state import is_container
from lib.remote_utils import (
    get_user_home,
    is_dry_run,
    is_package_installed,
    run,
)
from lib.release_management import fetch_latest_github_release_asset


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
HELIUM_RELEASE_API = "https://api.github.com/repos/imputnet/helium-linux/releases/latest"
BROWSH_GITHUB_REPO = "browsh-org/browsh"
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
    "armel": "armv6",
    "armhf": "armv7",
    "i386": "386",
}


_FLATPAK_BROWSER_IDS = {
    "brave": "com.brave.Browser",
    "firefox": "org.mozilla.firefox",
    "librewolf": "io.gitlab.librewolf-community",
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


def _browsh_architecture() -> str:
    """Return the Browsh release architecture for the target Debian system."""
    result = run("dpkg --print-architecture", check=False, capture_output=True)
    dpkg_arch = (result.stdout or "").strip()
    try:
        return _BROWSH_ARCH_BY_DPKG[dpkg_arch]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported Browsh architecture: {dpkg_arch or 'unknown'}") from exc


def _browsh_asset_matches(tag_name: str, asset_name: str, arch: str) -> bool:
    """Return whether a release asset is the Debian package for ``arch``."""
    version = tag_name.removeprefix("v")
    return asset_name == f"browsh_{version}_linux_{arch}.deb"


def _resolve_browsh_deb() -> tuple[str, str]:
    """Return the newest stable Browsh release with a matching Debian asset."""
    arch = _browsh_architecture()
    return fetch_latest_github_release_asset(
        BROWSH_GITHUB_REPO,
        asset_matches=lambda tag_name, asset_name: _browsh_asset_matches(
            tag_name, asset_name, arch
        ),
        missing_asset_description=(
            f"No stable Browsh Debian package found for architecture '{arch}'"
        ),
    )


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
        run(f"apt-get install -y -qq {package_name}", check=False)
        if is_package_installed(package_name):
            return True
        print(f"  ✗ Failed to install {name} (package not found after install)")
    else:
        print(f"  ✗ Failed to enable {name} repository")
    return False


def _install_helium_browser() -> None:
    """Install Helium from the latest upstream Debian package release."""
    if is_package_installed("helium-bin") or os.path.exists("/usr/bin/helium"):
        print("  ✓ Helium browser already installed")
        return

    print("  Installing Helium browser...")
    url_script = f"""import json
import subprocess
import sys
import urllib.request

arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
if arch not in {{"amd64", "arm64"}}:
    sys.exit(f"unsupported architecture: {{arch}}")

with urllib.request.urlopen("{HELIUM_RELEASE_API}", timeout=30) as response:
    release = json.load(response)

suffix = f"_{{arch}}.deb"
for asset in release.get("assets", []):
    name = asset.get("name", "")
    if name.startswith("helium-bin_") and name.endswith(suffix):
        print(asset["browser_download_url"])
        break
else:
    sys.exit(f"no Helium Debian package found for {{arch}}")
"""
    result = run(
        f"python3 -c {shlex.quote(url_script)}",
        check=False,
        capture_output=True,
        display_cmd="python3 -c '[resolve latest Helium Debian package URL]'",
    )
    helium_url = result.stdout.strip() if result.returncode == 0 else ""
    if not helium_url:
        print("  ✗ Failed to resolve latest Helium package URL")
        if result.stderr:
            print(f"    {result.stderr.strip()[:200]}")
        return

    with tempfile.TemporaryDirectory(prefix="basaltwater-helium-") as temporary_dir:
        package_path = os.path.join(temporary_dir, "helium.deb")
        download_result = run(
            f"wget --https-only -qO {shlex.quote(package_path)} "
            f"{shlex.quote(helium_url)}",
            check=False,
        )
        if download_result.returncode != 0:
            print("  ✗ Failed to download Helium package")
            return
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run(f"apt-get install -y -qq {shlex.quote(package_path)}", check=False)
    if is_package_installed("helium-bin") or os.path.exists("/usr/bin/helium"):
        print("  ✓ Helium browser installed")


def install_single_browser(browser: str, use_flatpak: bool) -> None:
    """Install a single browser."""
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    if browser == "brave":
        if use_flatpak:
            if is_flatpak_app_installed("com.brave.Browser"):
                print("  ✓ Brave browser already installed")
                return
            print("  Installing Brave browser...")
            run(f"flatpak install -y {FLATPAK_REMOTE} com.brave.Browser", check=False)
            if is_flatpak_app_installed("com.brave.Browser"):
                print("  ✓ Brave browser installed")
                return
            print("  ⚠ Flatpak install failed, falling back to apt...")
        
        if is_package_installed("brave-browser"):
            print("  ✓ Brave browser already installed")
            return
        print("  Installing Brave browser...")
        if _install_via_extrepo("Brave", "brave", "brave-browser"):
            print("  ✓ Brave browser installed")
    
    elif browser == "firefox":
        if use_flatpak:
            if is_flatpak_app_installed("org.mozilla.firefox"):
                print("  ✓ Firefox already installed")
                return
            print("  Installing Firefox...")
            run(f"flatpak install -y {FLATPAK_REMOTE} org.mozilla.firefox", check=False)
            if is_flatpak_app_installed("org.mozilla.firefox"):
                print("  ✓ Firefox installed")
                return
        else:
            if is_package_installed("firefox") or is_package_installed("firefox-esr"):
                print("  ✓ Firefox already installed")
                return
            print("  Installing Firefox...")
            run("apt-get install -y -qq firefox-esr", check=False)
            if is_package_installed("firefox") or is_package_installed("firefox-esr"):
                print("  ✓ Firefox installed")
            else:
                return
        

    elif browser == "librewolf":
        if use_flatpak:
            if is_flatpak_app_installed("io.gitlab.librewolf-community"):
                print("  ✓ LibreWolf browser already installed")
                return
            print("  Installing LibreWolf browser...")
            run(f"flatpak install -y {FLATPAK_REMOTE} io.gitlab.librewolf-community", check=False)
            if is_flatpak_app_installed("io.gitlab.librewolf-community"):
                print("  ✓ LibreWolf browser installed")
                return
        else:
            if is_package_installed("librewolf"):
                _configure_librewolf_apparmor_profile()
                print("  ✓ LibreWolf browser already installed")
                return
            print("  Installing LibreWolf browser...")

            if _install_via_extrepo("LibreWolf", "librewolf", "librewolf"):
                _configure_librewolf_apparmor_profile()
                print("  ✓ LibreWolf browser installed")

    elif browser == "helium":
        _install_helium_browser()
    
    elif browser == "browsh":
        if is_package_installed("browsh") or shutil.which("browsh"):
            print("  ✓ Browsh already installed")
            return

        print("  Installing Browsh (requires Firefox)...")
        if not (is_package_installed("firefox") or is_package_installed("firefox-esr")):
            print("  Installing Firefox (required for Browsh)...")
            run("apt-get install -y -qq firefox-esr", check=False)
        
        try:
            tag_name, browsh_url = _resolve_browsh_deb()
        except RuntimeError as exc:
            print(f"  ✗ Failed to resolve current Browsh release: {exc}")
            return
        with tempfile.TemporaryDirectory(prefix="basaltwater-browsh-") as temporary_dir:
            package_path = os.path.join(temporary_dir, "browsh.deb")
            download_result = run(
                f"wget --https-only -qO {shlex.quote(package_path)} "
                f"{shlex.quote(browsh_url)}",
                check=False,
                display_cmd=(
                    f"wget --https-only -qO {package_path} "
                    f"<Browsh {tag_name} release URL>"
                ),
            )
            if download_result.returncode != 0:
                print("  ✗ Failed to download Browsh package")
                return
            run(f"apt-get install -y -qq {shlex.quote(package_path)}", check=False)
        if is_package_installed("browsh") or shutil.which("browsh"):
            print("  ✓ Browsh installed")
    
    elif browser == "lynx":
        if is_package_installed("lynx"):
            print("  ✓ Lynx already installed")
            return
        print("  Installing Lynx...")
        run("apt-get install -y -qq lynx", check=False)
        if is_package_installed("lynx"):
            print("  ✓ Lynx installed")


def install_browser(config: SetupConfig) -> None:
    """Install the specified browser(s)."""
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


def configure_default_browser(config: SetupConfig) -> None:
    if not config.browser:
        return

    if is_dry_run():
        print(f"  [DRY-RUN] Would set {config.browser} as the default browser")
        return

    safe_username = shlex.quote(config.username)
    home_dir = get_user_home(config.username)
    mimeapps_path = os.path.join(home_dir, ".config", "mimeapps.list")
    
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
    
    local_dir = os.path.join(home_dir, ".local")
    config_dir = os.path.join(home_dir, ".config")
    os.makedirs(config_dir, exist_ok=True)

    mimeapps_content = f"""[Default Applications]
x-scheme-handler/http={desktop_file}
x-scheme-handler/https={desktop_file}
text/html={desktop_file}
application/xhtml+xml={desktop_file}
"""
    # This file is managed by basaltwater. Recreate it instead of preserving
    # stale defaults from an older workstation setup.
    with open(mimeapps_path, "w", encoding="utf-8") as f:
        f.write(mimeapps_content)
    
    browser_command = {
        "brave": "brave-browser",
        "firefox": "firefox",
        "helium": "helium",
        "librewolf": "librewolf",
    }[config.browser]
    if using_flatpak_browser:
        browser_command = f"flatpak run {flatpak_app_id}"

    helper_dir = os.path.join(home_dir, _XFCE_HELPER_SUBDIR)
    os.makedirs(helper_dir, exist_ok=True)
    helper_path = os.path.join(helper_dir, f"{config.browser}.desktop")
    for managed_browser in ("brave", "firefox", "helium", "librewolf"):
        stale_helper = os.path.join(helper_dir, f"{managed_browser}.desktop")
        if stale_helper == helper_path:
            continue
        try:
            os.remove(stale_helper)
        except OSError:
            pass
    if using_flatpak_browser:
        helper_binaries = "flatpak;"
        helper_commands = browser_command
        helper_commands_with_parameter = f'{browser_command} "%s"'
    else:
        helper_binaries = f"{browser_command};"
        helper_commands = "%B"
        helper_commands_with_parameter = '%B "%s"'
    helper_content = f"""[Desktop Entry]
Version=1.0
Type=X-XFCE-Helper
Name={config.browser.capitalize()}
StartupNotify=true
X-XFCE-Binaries={helper_binaries}
X-XFCE-Category=WebBrowser
X-XFCE-Commands={helper_commands};
X-XFCE-CommandsWithParameter={helper_commands_with_parameter};
"""
    with open(helper_path, "w", encoding="utf-8") as f:
        f.write(helper_content)

    xfce_config_dir = os.path.join(config_dir, "xfce4")
    os.makedirs(xfce_config_dir, exist_ok=True)
    xfce_helpers_path = os.path.join(xfce_config_dir, "helpers.rc")
    with open(xfce_helpers_path, "w", encoding="utf-8") as f:
        f.write(f"WebBrowser={browser_command}\n")

    user_env = (
        f"runuser -u {safe_username} -- env"
        f" HOME={shlex.quote(home_dir)}"
        f" XDG_CONFIG_HOME={shlex.quote(config_dir)}"
    )
    for mime_type in (
        "x-scheme-handler/http",
        "x-scheme-handler/https",
        "text/html",
        "application/xhtml+xml",
    ):
        run(
            f"{user_env} xdg-mime default {desktop_file} {mime_type}",
            check=False,
        )
    run(f"{user_env} xdg-settings set default-web-browser {desktop_file}", check=False)
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(config_dir)}")
    run(f"chown -R {safe_username}:{safe_username} {shlex.quote(local_dir)}")
    
    print(f"  ✓ Default browser set to {config.browser.capitalize()}")
