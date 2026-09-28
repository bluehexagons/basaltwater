"""Prepare a selected Ubuntu distribution and stage its pinned setup source."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.validation import validate_filesystem_path
from lib.validators import validate_username


CONF = Path("/etc/wsl.conf")
RELEASES = Path("/opt/basaltwater/releases")


def _set_ini_value(lines: list[str], section: str, key: str, value: str) -> bool:
    """Update one INI key while retaining unrelated WSL configuration lines."""
    header = re.compile(r"^\s*\[([^]]+)\]\s*$")
    setting = re.compile(r"^\s*([^=;#\s]+)\s*=")
    positions = [index for index, line in enumerate(lines)
                 if (match := header.match(line)) and match.group(1).lower() == section]
    if len(positions) > 1:
        raise ValueError(f"Duplicate [{section}] sections in /etc/wsl.conf")
    if not positions:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend((f"[{section}]\n", f"{key}={value}\n"))
        return True
    start = positions[0]
    end = next((index for index in range(start + 1, len(lines))
                if header.match(lines[index])), len(lines))
    keys = [index for index in range(start + 1, end)
            if (match := setting.match(lines[index])) and match.group(1).lower() == key]
    if len(keys) > 1:
        raise ValueError(f"Duplicate {key} entries in [{section}]")
    if keys:
        replacement = f"{key}={value}\n"
        if lines[keys[0]] == replacement:
            return False
        lines[keys[0]] = replacement
    else:
        lines.insert(end, f"{key}={value}\n")
    return True


def configure(username: str) -> None:
    if os.geteuid() != 0 or platform.freedesktop_os_release().get("ID") != "ubuntu":
        raise ValueError("Run preparation as root in the selected Ubuntu distribution")
    if not validate_username(username) or username == "root":
        raise ValueError("Invalid non-root Ubuntu username")
    try:
        pwd.getpwnam(username)
    except KeyError:
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", username], check=True)
        print(f"Set the password for Ubuntu user {username}:")
        subprocess.run(["passwd", username], check=True)
    subprocess.run(["usermod", "-aG", "sudo", username], check=True)
    if CONF.is_symlink():
        raise ValueError("/etc/wsl.conf must not be a symlink")
    lines = CONF.read_text(encoding="utf-8").splitlines(keepends=True) if CONF.exists() else []
    changed = _set_ini_value(lines, "boot", "systemd", "true")
    changed = _set_ini_value(lines, "user", "default", username) or changed
    if changed:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=CONF.parent, delete=False) as output:
            output.writelines(lines)
            temporary = Path(output.name)
        temporary.chmod(0o644)
        os.replace(temporary, CONF)
    print("CHANGED" if changed else "UNCHANGED")


def stage(source: str, revision: str) -> None:
    if os.geteuid() != 0:
        raise ValueError("Source staging requires root")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full source commit SHA")
    validate_filesystem_path(source, must_exist=True)
    root = Path(source)
    if not root.is_dir() or not (root / "plugins/wsl.py").is_file():
        raise ValueError("Invalid Basaltwater source directory")
    destination = RELEASES / revision
    if destination.exists():
        if (destination / ".basaltwater-source").read_text(encoding="ascii").strip() != revision:
            raise ValueError("Existing source release has an invalid marker")
        print(destination)
        return
    RELEASES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=RELEASES) as directory:
        temporary = Path(directory) / "source"
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Source contains a symbolic link: {path}")
        shutil.copytree(root, temporary)
        (temporary / ".basaltwater-source").write_text(revision + "\n", encoding="ascii")
        os.replace(temporary, destination)
    print(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    configure_parser = commands.add_parser("configure")
    configure_parser.add_argument("--user", required=True)
    stage_parser = commands.add_parser("stage")
    stage_parser.add_argument("--source", required=True)
    stage_parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.command == "configure":
        configure(args.user)
    else:
        stage(args.source, args.revision)


if __name__ == "__main__":
    main()
