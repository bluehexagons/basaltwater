from __future__ import annotations

import os
import shutil
import subprocess

from common.common_steps import install_or_update_uv
from lib.completions import run_completion_setup
from lib.orchestrator_bootstrap import LAUNCHER_NAME, install_launcher
from lib.system_utils import get_current_username
from lib.update_policy import uv_exclude_newer_args
from lib.validators import validate_username


def run_local_python_setup(
    shell: str,
    command_name: str = LAUNCHER_NAME,
    script_path: str | None = None,
) -> int:
    """Install local Python tooling and CLI completions for the current user."""
    username = get_current_username()
    if not validate_username(username):
        print(f"Error: Invalid username: {username}")
        return 1

    python3_path = shutil.which("python3")
    if not python3_path:
        print("Error: python3 is required but not found on PATH.")
        return 1

    user_home = os.path.expanduser("~")
    local_bin = os.path.join(user_home, ".local", "bin")
    os.makedirs(local_bin, exist_ok=True)

    python_alias = os.path.join(local_bin, "python")
    python_cmd = shutil.which("python")
    if python_cmd is None:
        if not os.path.exists(python_alias):
            try:
                os.symlink(python3_path, python_alias)
                print(f"✓ Added local python alias: {python_alias} -> {python3_path}")
            except FileExistsError:
                print("✓ local python alias already exists")
        else:
            print("✓ local python alias already exists")
    else:
        print("✓ python command already available on PATH")

    if not install_or_update_uv(user_home=user_home, username=None):
        print("Error: failed to install/update uv.")
        return 1

    uv_path = os.path.join(local_bin, "uv")
    argcomplete_result = subprocess.run(
        [uv_path, "tool", "install", "--upgrade", "argcomplete"] + uv_exclude_newer_args(),
        capture_output=True,
        text=True,
    )
    if argcomplete_result.returncode != 0:
        output = "\n".join(
            part for part in [argcomplete_result.stdout.strip(), argcomplete_result.stderr.strip()] if part
        )
        print(f"Error: failed to install argcomplete with uv.{f' Output: {output}' if output else ''}")
        return 1
    print("✓ argcomplete installed via uv")

    if script_path:
        try:
            launcher_path = install_launcher(script_path, target_dir=local_bin)
            print(f"✓ Installed user launcher: {launcher_path}")
        except (OSError, ValueError) as exc:
            print(f"Error: could not install user launcher in {local_bin}: {exc}")
            return 1

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([local_bin, old_path]) if old_path else local_bin
    try:
        result = run_completion_setup(shell=shell, global_install=False, command_name=command_name)
    finally:
        os.environ["PATH"] = old_path

    if result != 0:
        return result

    print("✓ Local Basaltwater Python install complete.")
    return 0
