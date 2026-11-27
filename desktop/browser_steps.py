"""Desktop and workstation setup steps."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from desktop.browser_extensions import install_chromium_blocker, install_firefox_blocker
from desktop.browser_session import FLATPAK_BROWSER_IDS
from desktop.home_setup import apply_home_settings
from lib.config import GRAPHICAL_SETUP_BROWSERS, SetupConfig
from lib.machine_state import is_container
from lib.remote_utils import (
    is_dry_run,
    is_package_installed,
    run,
)


FLATPAK_REMOTE = "flathub"
_apt_update_done = False
_EXTREPO_SOURCE_DIR = "/etc/apt/sources.list.d"
_MANAGED_EXTREPOS = ("librewolf", "vscode")
_EXTREPO_UPDATE_TIMEOUT_SECONDS = 30
_APT_UPDATE_COMMAND = (
    "apt-get -o DPkg::Lock::Timeout=120 "
    "-o Acquire::Retries=0 -o Acquire::http::Timeout=15 "
    "-o Acquire::https::Timeout=15 update -qq"
)
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


def is_flatpak_app_installed(app_id: str, *, system: bool = False) -> bool:
    """Check if a Flatpak application is installed."""
    result = subprocess.run(
        ["flatpak", "list", *(["--system"] if system else []), "--app", "--columns=application"],
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


def _install_browser_package(name: str, package: str) -> None:
    if is_package_installed(package):
        print(f"  ✓ {name} already installed")
        return
    print(f"  Installing {name}...")
    result = run(f"apt-get install -y -qq {package}", check=False)
    if result.returncode != 0 or not is_package_installed(package):
        raise RuntimeError(f"{name} installation failed")
    print(f"  ✓ {name} installed")


def install_single_browser(browser: str, use_flatpak: bool) -> None:
    """Install a single browser."""
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    app_id = FLATPAK_BROWSER_IDS.get(browser) if use_flatpak else None
    if app_id:
        name = "LibreWolf" if browser == "librewolf" else "Firefox"
        if is_flatpak_app_installed(app_id, system=True):
            print(f"  ✓ {name} already installed")
        else:
            print(f"  Installing {name}...")
            result = run(f"flatpak install --system -y {FLATPAK_REMOTE} {app_id}", check=False)
            if result.returncode != 0 or not is_flatpak_app_installed(app_id, system=True):
                raise RuntimeError(f"{name} Flatpak installation failed")
            print(f"  ✓ {name} installed")
        if browser == "firefox":
            install_firefox_blocker(flatpak=True)
        return

    if browser == "firefox":
        if not is_package_installed("firefox"):
            _install_browser_package("Firefox", "firefox-esr")
        install_firefox_blocker(flatpak=False)

    elif browser == "librewolf":
        if is_package_installed("librewolf"):
            _configure_librewolf_apparmor_profile()
            print("  ✓ LibreWolf browser already installed")
            return
        print("  Installing LibreWolf browser...")
        if not _install_via_extrepo("LibreWolf", "librewolf", "librewolf"):
            raise RuntimeError("LibreWolf installation failed")
        _configure_librewolf_apparmor_profile()
        print("  ✓ LibreWolf browser installed")

    elif browser in ("chromium", "lynx"):
        _install_browser_package(browser.capitalize(), browser)
        if browser == "chromium":
            install_chromium_blocker()
    else:
        raise ValueError(f"Unsupported browser: {browser}")


def install_browser(config: SetupConfig) -> None:
    """Install the specified browser(s)."""
    browsers = config.browsers or ([config.browser] if config.browser else [])
    if not browsers:
        return
    if config.dry_run or is_dry_run():
        print(f"  [DRY-RUN] Would install and verify: {', '.join(browsers)}")
        if "firefox" in browsers:
            print("  [DRY-RUN] Would set up uBlock Origin for Firefox")
        if "chromium" in browsers:
            print("  [DRY-RUN] Would register uBlock Origin Lite for Chromium")
        return
    # In containers, prefer apt over Flatpak since Flatpak often doesn't work
    use_flatpak = config.use_flatpak
    if use_flatpak and is_container():
        print("  ⚠ Container detected: using apt instead of Flatpak for browser")
        use_flatpak = False
    
    for browser in browsers:
        install_single_browser(browser, use_flatpak)


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

    if config.browser not in GRAPHICAL_SETUP_BROWSERS:
        print(f"  ✓ No default browser configuration needed for {config.browser}")
        return

    desktop_file = f"{config.browser}.desktop"
    flatpak_app_id = FLATPAK_BROWSER_IDS.get(config.browser)
    using_flatpak_browser = bool(
        config.use_flatpak
        and flatpak_app_id
        and not is_container()
        and is_flatpak_app_installed(flatpak_app_id, system=True)
    )
    if using_flatpak_browser:
        desktop_file = f"{flatpak_app_id}.desktop"
    elif (config.browser == "firefox"
          and not os.path.isfile("/usr/share/applications/firefox.desktop")
          and os.path.isfile("/usr/share/applications/firefox-esr.desktop")):
        desktop_file = "firefox-esr.desktop"
    
    mimeapps_content = f"""[Default Applications]
x-scheme-handler/http={desktop_file}
x-scheme-handler/https={desktop_file}
x-scheme-handler/about={desktop_file}
x-scheme-handler/unknown={desktop_file}
text/html={desktop_file}
application/xhtml+xml={desktop_file}
"""
    browser_command = config.browser
    if using_flatpak_browser:
        browser_command = f"flatpak run {flatpak_app_id}"

    edits = [
        ("write", ".config/mimeapps.list", mimeapps_content),
        ("write", f"{_XFCE_HELPER_SUBDIR}/{config.browser}.desktop",
         _xfce_browser_helper(config.browser, browser_command, flatpak=using_flatpak_browser)),
        ("write", ".config/xfce4/helpers.rc", f"WebBrowser={config.browser}\n"),
    ]
    for browser in GRAPHICAL_SETUP_BROWSERS:
        if browser == config.browser:
            continue
        path = f"{_XFCE_HELPER_SUBDIR}/{browser}.desktop"
        edits.append(("retire", path, _xfce_browser_helper(browser, browser, flatpak=False)))
        if browser in FLATPAK_BROWSER_IDS:
            edits.append(("retire", path, _xfce_browser_helper(
                browser, f"flatpak run {FLATPAK_BROWSER_IDS[browser]}", flatpak=True,
            )))
    # Retire only exact copies of helpers written by older setups.
    edits.append(("retire", f"{_XFCE_HELPER_SUBDIR}/helium.desktop",
                  _xfce_browser_helper("helium", "helium", flatpak=False)))
    for command, flatpak in (("brave-browser", False), ("flatpak run com.brave.Browser", True)):
        edits.append(("retire", f"{_XFCE_HELPER_SUBDIR}/brave.desktop",
                      _xfce_browser_helper("brave", command, flatpak=flatpak)))
    apply_home_settings(config.username, edits)
    
    print(f"  ✓ Default browser set to {config.browser.capitalize()}")
