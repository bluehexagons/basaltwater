"""Disposable profiles for supervised native browser launches."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.validation import validate_network_ip, validate_no_control_characters
from lib.validators import validate_host


BROWSER_APPLICATIONS = {
    "firefox": ("firefox-esr", "firefox", "librewolf"),
    "chromium": ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"),
}
BROWSERS = tuple(name for names in BROWSER_APPLICATIONS.values() for name in names)
FLATPAK_BROWSER_IDS = {"firefox": "org.mozilla.firefox", "librewolf": "io.gitlab.librewolf-community"}


def supervised_browser_argv(browser: str, project: str, *, native: bool) -> list[str]:
    return ["basaltw", "desktop", *(["--native"] if native else []), "develop", "browser",
            "--browser", browser, "--project", project]


def validate_browser_url(url: str) -> None:
    if url == "about:blank":
        return
    if not isinstance(url, str) or not 1 <= len(url) <= 4096:
        raise ValueError("Browser URL must be about:blank or an HTTP(S) URL of at most 4096 characters")
    validate_no_control_characters(url, "Browser URL")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or "\\" in url or any(character.isspace() for character in url)):
        raise ValueError("Browser URL must be about:blank or a credential-free HTTP(S) URL")
    if not validate_host(parsed.hostname):
        validate_network_ip(parsed.hostname, "browser host")
    if parsed.port == 0:
        raise ValueError("Browser URL port must be positive")


def browser_command(browser: str, executable: str, url: str, profile: str) -> list[str]:
    if browser in BROWSER_APPLICATIONS["firefox"]:
        return [executable, "-no-remote", "-profile", profile, "-url", url]
    return [executable, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", url]


def create_browser_profile(browser: str, directory: Path) -> None:
    profile, downloads = directory / "profile", directory / "downloads"
    profile.mkdir(mode=0o700)
    downloads.mkdir(mode=0o700)
    if browser in BROWSER_APPLICATIONS["firefox"]:
        preferences = {
            "browser.download.folderList": 2,
            "browser.download.dir": str(downloads),
            "browser.download.useDownloadDir": True,
            "browser.shell.checkDefaultBrowser": False,
        }
        body = "".join(f"user_pref({json.dumps(key)}, {json.dumps(value)});\n"
                       for key, value in preferences.items())
        write_text_atomic(str(profile / "user.js"), body)
    else:
        default = profile / "Default"
        default.mkdir(mode=0o700)
        write_json_atomic(str(default / "Preferences"), {
            "download": {"default_directory": str(downloads), "prompt_for_download": False},
        })
