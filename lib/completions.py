from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

INFRA_TOOLS_COMMAND = "basaltw"
LEGACY_INFRA_TOOLS_COMMANDS = ("infra_tools", "infra_tools.py")
LEGACY_COMPLETION_MARKER = "# infra_tools shell completions"


def _retire_legacy_completion_files(completions_dir: Path, names: tuple[str, ...]) -> None:
    """Remove legacy generated completion files without touching directories."""
    for name in names:
        path = completions_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()


def _retire_legacy_shell_registrations(config_file: Path) -> None:
    """Remove generated registrations for the old command from one shell config."""
    if not config_file.is_file():
        return

    legacy_registration_lines = {
        f'eval "$(register-python-argcomplete {name})"'
        for name in LEGACY_INFRA_TOOLS_COMMANDS
    }
    original_lines = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    cleaned_lines = [
        line
        for line in original_lines
        if line.strip() != LEGACY_COMPLETION_MARKER
        and line.strip() not in legacy_registration_lines
    ]
    if cleaned_lines != original_lines:
        config_file.write_text("".join(cleaned_lines), encoding="utf-8")


def _retire_legacy_user_completions(config_file: Path) -> None:
    """Remove old generated registrations when installing the new command."""
    _retire_legacy_shell_registrations(config_file)


def detect_shell() -> str:
    """Detect the current shell from environment."""
    shell = os.environ.get("SHELL", "")
    if "bash" in shell:
        return "bash"
    if "zsh" in shell:
        return "zsh"
    if "fish" in shell:
        return "fish"
    if "tcsh" in shell:
        return "tcsh"
    return "bash"


def get_bash_config_file() -> Path:
    """Get the appropriate bash config file."""
    home = Path.home()
    for filename in [".bashrc", ".bash_profile", ".profile"]:
        path = home / filename
        if path.exists():
            return path
    return home / ".bashrc"


def get_zsh_config_file() -> Path:
    """Get the appropriate zsh config file."""
    home = Path.home()
    zshrc = home / ".zshrc"
    if zshrc.exists():
        return zshrc
    return home / ".zshrc"


def get_fish_config_dir() -> Path:
    """Get fish configuration directory."""
    return Path.home() / ".config" / "fish"


def _find_register_argcomplete() -> str | None:
    return shutil.which("register-python-argcomplete")


def setup_bash_completions(command_name: str = INFRA_TOOLS_COMMAND, global_install: bool = False) -> bool:
    """Setup bash completions for basaltw."""
    register_cmd = _find_register_argcomplete()
    if register_cmd is None:
        print("Error: register-python-argcomplete not found in PATH")
        print("Make sure argcomplete is installed: uv tool install --upgrade argcomplete")
        return False

    try:
        if global_install:
            completions_dir = Path("/etc/bash_completion.d")
            if not completions_dir.exists():
                print(f"Error: {completions_dir} does not exist")
                print("System-wide installation requires bash-completion package")
                return False

            completion_file = completions_dir / command_name
            result = subprocess.run(
                [register_cmd, command_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                print(f"Error creating completion for {command_name}: {result.stderr}")
                return False

            completion_file.write_text(result.stdout)
            if command_name == INFRA_TOOLS_COMMAND:
                _retire_legacy_completion_files(
                    completions_dir,
                    LEGACY_INFRA_TOOLS_COMMANDS,
                )
            print(f"Created: {completion_file}")
            print(f"\nSystem-wide bash completions installed in {completions_dir}")
            print("New shells will have tab completion enabled automatically.")
            return True

        config_file = get_bash_config_file()
        if command_name == INFRA_TOOLS_COMMAND:
            _retire_legacy_user_completions(config_file)
        if config_file.exists():
            content = config_file.read_text()
            if "argcomplete" in content and command_name in content:
                print(f"Completions already configured in {config_file}")
                return True

        lines = [
            "# Basaltwater shell completions",
            f'eval "$(register-python-argcomplete {command_name})"',
        ]
        with open(config_file, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(lines) + "\n")

        print(f"Added completions to {config_file}")
        print(f"Run 'source {config_file}' or restart your shell to enable completions.")
        return True
    except Exception as exc:
        print(f"Error setting up bash completions: {exc}")
        return False


def setup_zsh_completions(command_name: str = INFRA_TOOLS_COMMAND, global_install: bool = False) -> bool:
    """Setup zsh completions for basaltw."""
    register_cmd = _find_register_argcomplete()
    if register_cmd is None:
        print("Error: register-python-argcomplete not found in PATH")
        print("Make sure argcomplete is installed: uv tool install --upgrade argcomplete")
        return False

    try:
        if global_install:
            result = subprocess.run(
                ["zsh", "-c", "echo $fpath"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                print("Error: Could not determine zsh fpath")
                return False

            completions_dir = None
            for fpath_entry in result.stdout.strip().split():
                candidate = Path(fpath_entry)
                if "site-functions" in fpath_entry and candidate.exists():
                    completions_dir = candidate
                    break

            if completions_dir is None:
                print("Error: Could not find zsh site-functions directory")
                return False

            completion_file = completions_dir / f"_{command_name}"
            result = subprocess.run(
                [register_cmd, "--shell", "zsh", command_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                print(f"Error creating completion for {command_name}: {result.stderr}")
                return False

            completion_file.write_text(result.stdout)
            if command_name == INFRA_TOOLS_COMMAND:
                _retire_legacy_completion_files(
                    completions_dir,
                    tuple(f"_{name}" for name in LEGACY_INFRA_TOOLS_COMMANDS),
                )
            print(f"Created: {completion_file}")
            print(f"\nZsh completions installed in {completions_dir}")
            print("You may need to run 'compinit' or restart your shell.")
            return True

        config_file = get_zsh_config_file()
        if command_name == INFRA_TOOLS_COMMAND:
            _retire_legacy_user_completions(config_file)
        if config_file.exists():
            content = config_file.read_text()
            if "argcomplete" in content and command_name in content:
                print(f"Completions already configured in {config_file}")
                return True

        lines = [
            "# Basaltwater shell completions",
            f'eval "$(register-python-argcomplete {command_name})"',
        ]
        with open(config_file, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(lines) + "\n")

        print(f"Added completions to {config_file}")
        print(f"Run 'source {config_file}' or restart your shell to enable completions.")
        return True
    except Exception as exc:
        print(f"Error setting up zsh completions: {exc}")
        return False


def setup_fish_completions(command_name: str = INFRA_TOOLS_COMMAND, global_install: bool = False) -> bool:
    """Setup fish completions for basaltw."""
    del global_install

    register_cmd = _find_register_argcomplete()
    if register_cmd is None:
        print("Error: register-python-argcomplete not found in PATH")
        print("Make sure argcomplete is installed: uv tool install --upgrade argcomplete")
        return False

    try:
        completions_dir = get_fish_config_dir() / "completions"
        completions_dir.mkdir(parents=True, exist_ok=True)
        completion_file = completions_dir / f"{command_name}.fish"
        result = subprocess.run(
            [register_cmd, "--shell", "fish", command_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(f"Error creating completion for {command_name}: {result.stderr}")
            return False

        completion_file.write_text(result.stdout)
        if command_name == INFRA_TOOLS_COMMAND:
            _retire_legacy_completion_files(
                completions_dir,
                tuple(f"{name}.fish" for name in LEGACY_INFRA_TOOLS_COMMANDS),
            )
        print(f"Created: {completion_file}")
        print(f"\nFish completions installed in {completions_dir}")
        print("Completions are active immediately in new fish shells.")
        return True
    except Exception as exc:
        print(f"Error setting up fish completions: {exc}")
        return False


def setup_tcsh_completions(command_name: str = INFRA_TOOLS_COMMAND, global_install: bool = False) -> bool:
    """Setup tcsh completions for basaltw."""
    del command_name, global_install
    print("Note: tcsh completion support is limited.")
    print("Consider using bash or zsh for full tab completion support.")
    return False


def run_completion_setup(shell: str, global_install: bool, command_name: str = INFRA_TOOLS_COMMAND) -> int:
    """Install shell completion for the Basaltwater CLI."""
    if shell == "auto":
        shell = detect_shell()
        print(f"Detected shell: {shell}")

    if global_install and os.geteuid() != 0:
        print("Error: Global installation requires root privileges")
        print(f"Run with: sudo python3 {command_name} completions --global")
        return 1

    print(f"\nSetting up {shell} completions...")
    if global_install:
        print("(system-wide installation)\n")
    else:
        print("(user installation)\n")

    setup_funcs = {
        "bash": setup_bash_completions,
        "zsh": setup_zsh_completions,
        "fish": setup_fish_completions,
        "tcsh": setup_tcsh_completions,
    }
    if shell not in setup_funcs:
        print(f"Error: Unsupported shell '{shell}'")
        print("Supported shells: bash, zsh, fish, tcsh")
        return 1

    success = setup_funcs[shell](command_name=command_name, global_install=global_install)
    if not success:
        return 1

    print("\nTo use completions immediately, run:")
    print(f'  eval "$(register-python-argcomplete {command_name})"')
    print("\nOr restart your shell.")
    return 0
