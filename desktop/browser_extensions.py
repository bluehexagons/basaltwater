"""Install distribution and store defaults for workstation content blockers."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import is_package_installed, run
from lib.setup_directories import directory_fd


FIREFOX_BLOCKER_PACKAGE = "webext-ublock-origin-firefox"
_FLATPAK_SYSTEMCONFIG = Path("/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig")
_FIREFOX_EXTENSION = {
    "uBlock0@raymondhill.net": {
        "installation_mode": "normal_installed",
        "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
    },
}
# Debian's launcher passes /usr/share/chromium/extensions/* as unpacked
# extensions. Chromium reads external-install JSON beside its binary instead.
_CHROMIUM_ENTRY = Path("/usr/lib/chromium/extensions/ddkjiahejlhfcafbddmgiahcphecmpfh.json")
_CHROMIUM_SETTINGS = {"external_update_url": "https://clients2.google.com/service/update2/crx"}


def _seed_extension_file(path: Path, settings: dict, name: str) -> None:
    """Publish a complete default once. An existing administrator file wins."""
    with directory_fd(str(path.parent), create=True, exact_create_mode=True) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError(f"Unsafe browser configuration directory: {path.parent}")
        directory = Path(f"/proc/self/fd/{parent}")
        if not os.path.lexists(directory / path.name):
            with tempfile.TemporaryDirectory(dir=directory) as stage:
                source = Path(stage) / "default.json"
                write_json_atomic(str(source), settings, mode=0o644)
                try:
                    os.link(source, path.name, dst_dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    pass
        try:
            current = read_json_file(str(directory / path.name))
        except (OSError, ValueError):
            current = None
        if current != settings:
            print(f"  Preserving existing browser configuration at {path}. Check {name} in the browser.")
            return
    print(f"  ✓ {name} registered for installation at browser startup")


def install_firefox_blocker(*, flatpak: bool) -> None:
    """APT supplies the native add-on. The sandbox uses Mozilla's signed XPI."""
    if flatpak:
        result = run(["flatpak", "info", "--system", "--show-ref", "org.mozilla.firefox"],
                     capture_output=True)
        ref = re.fullmatch(r"app/org\.mozilla\.firefox/([A-Za-z0-9_]+)/([A-Za-z0-9][A-Za-z0-9._-]*)",
                           result.stdout.strip())
        if not ref:
            raise RuntimeError("Could not identify the installed Firefox Flatpak architecture and branch")
        path = _FLATPAK_SYSTEMCONFIG.joinpath(*ref.groups(), "policies", "policies.json")
        _seed_extension_file(path, {"policies": {"ExtensionSettings": _FIREFOX_EXTENSION}}, "uBlock Origin")
    elif not is_package_installed(FIREFOX_BLOCKER_PACKAGE):
        run(["apt-get", "install", "--no-install-recommends", "-y", "-qq", FIREFOX_BLOCKER_PACKAGE])
        if not is_package_installed(FIREFOX_BLOCKER_PACKAGE):
            raise RuntimeError(f"uBlock Origin installation failed: missing {FIREFOX_BLOCKER_PACKAGE}")


def install_chromium_blocker() -> None:
    """Register the publisher's Chrome Web Store ID for uBlock Origin Lite."""
    _seed_extension_file(_CHROMIUM_ENTRY, _CHROMIUM_SETTINGS, "uBlock Origin Lite")
