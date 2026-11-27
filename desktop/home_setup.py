"""Apply workstation preferences after switching to the desktop account."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import stat
import sys

from desktop.xfce_config import clean_legacy_xfwm, remove_legacy_override, update_browser_defaults, update_channel
from lib.atomic_io import write_text_atomic
from lib.remote_utils import run
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


def apply_home_settings(username: str, edits: list[tuple[str, str, str]]) -> None:
    """Send the managed edits to a process running with the user's credentials."""
    if not validate_username(username) or pwd.getpwnam(username).pw_uid == 0:
        raise ValueError("Desktop settings require an existing non-root account")
    source = str(Path(__file__).resolve().parents[1])
    run(
        ["/usr/sbin/runuser", "-u", username, "--", "/usr/bin/python3", "-I", "-B",
         "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
         "from desktop.home_setup import main; main()", source, username],
        input_data=json.dumps(edits), cwd="/", timeout=60,
        display_cmd=f"Apply desktop settings as {username}",
    )


def _apply(home: Path, edits: list[tuple[str, str, str]]) -> None:
    for action, relative, content in edits:
        path = Path(relative)
        validate_filesystem_path(relative)
        if path.is_absolute() or ".." in path.parts or not path.name:
            raise ValueError(f"Invalid desktop settings path: {relative}")
        # Keep each operation on the opened directory, even if its pathname
        # is renamed. Refuse symlinked or non-directory parents.
        with directory_fd(str(home / path.parent), create=True) as directory:
            target = Path(f"/proc/self/fd/{directory}") / path.name
            try:
                info = target.lstat()
            except FileNotFoundError:
                if action in {"retire", "clean-xfwm"}:
                    continue
            else:
                if action == "retire" and not stat.S_ISREG(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"Refusing linked or non-regular desktop settings: {relative}")
            if action == "write":
                write_text_atomic(str(target), content, mode=0o644)
            elif action == "merge-browser-defaults":
                update_browser_defaults(str(target), content)
            elif action == "merge-xfce":
                update_channel(str(target), content)
            elif action == "retire":
                remove_legacy_override(str(target), content)
            elif action == "clean-xfwm":
                clean_legacy_xfwm(str(target))
            else:
                raise ValueError(f"Unknown desktop settings operation: {action}")


def main() -> None:
    account = pwd.getpwnam(sys.argv[2])
    if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid or account.pw_uid == 0:
        raise ValueError("Desktop settings process must run as the selected non-root account")
    home = Path(account.pw_dir)
    with directory_fd(str(home)):
        pass
    os.umask(0o022)
    _apply(home, json.load(sys.stdin))
