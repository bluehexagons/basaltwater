"""User-scoped coding tools for an existing CachyOS desktop.

Only missing system packages are installed. No repository synchronization,
system upgrades, account policy, desktop configuration, or update timers.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import shlex
import shutil
from subprocess import CompletedProcess
from typing import Any

from common.agent_steps import (
    BASE_AGENT_SKILL_NAMES, BROWSER_AGENT_SKILL_NAMES, install_managed_agent_skills,
)
from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import run
from lib.validation import validate_filesystem_path, validate_package_name
from lib.vendor_installer import install as install_vendor_tool


CACHYOS_SKILLS = ("infra-tools-cachyos-workstation", "infra-tools-cachyos-workspace")
CACHYOS_T3_SKILL = "infra-tools-cachyos-t3code"
T3_SERVICE = "infra-tools-cachyos-t3.service"
_MARKER = "# Managed by infra_tools CachyOS setup"

CACHYOS_DESKTOP_PACKAGES = (
    ("install_obs", "obs", "obs-studio"),
    ("install_blender", "blender", "blender"),
    ("install_kdenlive", "kdenlive", "kdenlive"),
    ("install_krita", "krita", "krita"),
    ("install_inkscape", "inkscape", "inkscape"),
    ("install_scribus", "scribus", "scribus"),
    ("install_audacity", "audacity", "audacity"),
    ("install_ardour", "ardour", "ardour"),
    ("install_lmms", "lmms", "lmms"),
    ("install_freecad", "freecad", "freecad"),
    ("install_kicad", "kicad", "kicad"),
    ("install_shotcut", "shotcut", "shotcut"),
    ("install_gimp", "gimp", "gimp"),
    ("install_remmina", "remmina", "remmina"),
)
CACHYOS_SYSADMIN_PACKAGES = (
    ("nmap", "nmap"),
    ("tcpdump", "tcpdump"),
    ("dig", "bind"),
    ("virt-manager", "virt-manager"),
    ("wireshark", "wireshark-qt"),
)


def _home(config: SetupConfig) -> Path:
    return Path(pwd.getpwnam(config.username).pw_dir)


def _tool_path(home: Path) -> str:
    return os.pathsep.join((str(home / ".local/bin"), str(home / ".opencode/bin"),
                           os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")))


def _user_run(command: list[str], home: Path, **kwargs: Any) -> CompletedProcess[str]:
    kwargs.setdefault("input_data", "")
    return run(
        [
            "env",
            "PATH=" + _tool_path(home),
            "CODEX_NON_INTERACTIVE=1",
            "CI=1",
            "NONINTERACTIVE=1",
            "NON_INTERACTIVE=1",
            "GIT_TERMINAL_PROMPT=0",
            "npm_config_yes=true",
            "NPM_CONFIG_YES=true",
            *command,
        ],
        **kwargs,
    )


def _directory(path: Path) -> None:
    validate_filesystem_path(str(path))
    if not path.is_absolute():
        raise ValueError(f"Expected absolute directory: {path}")
    # Work as the human user, and don't replace or traverse symlinked managed
    # destinations. Existing permissions and contents are retained.
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise ValueError(f"Refusing symlinked managed directory: {parent}")
        if parent.exists() and not parent.is_dir():
            raise ValueError(f"Expected directory: {parent}")
    path.mkdir(parents=True, exist_ok=True)


def _write_managed(path: Path, content: str, *, mode: int = 0o644) -> bool:
    _directory(path.parent)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing unsafe managed file: {path}")
    previous = path.read_text() if path.exists() else None
    if previous is not None and _MARKER not in previous:
        raise ValueError(f"Refusing to overwrite unmanaged file: {path}")
    if previous == content:
        return False
    write_text_atomic(str(path), content, mode=mode)
    return True


def configure_cachyos_shell(home: str, shell: str) -> None:
    """Add a small PATH fragment, retaining the desktop user's shell configuration."""
    root = Path(home)
    _directory(root / ".local/bin")
    if shell == "fish":
        _write_managed(root / ".config/fish/conf.d/infra-tools-cachyos.fish",
                       f'{_MARKER}\nfish_add_path --path "$HOME/.local/bin" "$HOME/.opencode/bin"\n')
        return
    if shell not in {"bash", "zsh"}:
        print(f"Add {root / '.local/bin'} and {root / '.opencode/bin'} to your {shell} PATH")
        return
    fragment = root / ".config/infra-tools/cachyos-path.sh"
    _write_managed(fragment, f'{_MARKER}\nexport PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"\n')
    rc = root / (".bashrc" if shell == "bash" else ".zshrc")
    if rc.is_symlink() or (rc.exists() and not rc.is_file()):
        raise ValueError(f"Refusing unsafe shell configuration: {rc}")
    source = f". {shlex.quote(str(fragment))}"
    previous = rc.read_text() if rc.exists() else ""
    if source not in previous.splitlines():
        with rc.open("a") as handle:
            handle.write(f"\n{_MARKER}\n{source}\n")


def install_missing_packages(packages: list[str]) -> None:
    """Use the current pacman database; never translate apt update to pacman -Sy."""
    packages = list(dict.fromkeys(validate_package_name(name) for name in packages))
    missing = []
    for package in packages:
        result = run(["pacman", "-Q", "--", package], check=False, capture_output=True)
        if result.returncode == 1:
            missing.append(package)
        elif result.returncode:
            raise RuntimeError(f"Could not query pacman for {package}")
    if not missing:
        print("  Requested system packages already installed")
        return
    command = ["pacman", "-S", "--needed", "--noconfirm", "--", *missing]
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    result = run(command, check=False, interactive=os.geteuid() != 0)
    if result.returncode:
        raise RuntimeError(
            "Package installation failed. If pacman reported 'Could not resolve host', "
            "check DNS and access to the configured mirror (for example, with "
            "`resolvectl query archlinux.cachyos.org`). Resolve the pacman error, "
            "update CachyOS through its normal full-system update workflow if needed, "
            "then rerun setup. infra-tools does not change DNS, refresh repositories, "
            "or upgrade the OS."
        )


def cachyos_packages(config: SetupConfig) -> list[str]:
    home = _home(config)
    packages = ["ca-certificates", "curl", "git", "ripgrep", "base-devel"]
    commands = [("gh", "github-cli")] if config.install_gh else []
    if config.install_node:
        commands += [("node", "nodejs"), ("npm", "npm"), ("pnpm", "pnpm")]
    if config.install_python:
        commands += [("python", "python"), ("uv", "uv")]
    if config.install_go:
        commands += [("go", "go")]
    if config.install_git_lfs:
        commands += [("git-lfs", "git-lfs")]
    if config.install_godot:
        commands += [("godot", "godot")]
    if config.install_av_tools:
        commands += [("ffmpeg", "ffmpeg"), ("magick", "imagemagick")]
    if config.install_gl_tools:
        commands += [("glxinfo", "mesa-utils"), ("vulkaninfo", "vulkan-tools")]
    if config.install_sunshine:
        packages.append("sunshine")
    if config.install_moonlight:
        packages.append("moonlight-qt")
    if config.install_gaming:
        packages.extend(("cachyos-gaming-meta", "cachyos-gaming-applications"))
    for field, _command, package in CACHYOS_DESKTOP_PACKAGES:
        if getattr(config, field):
            packages.append(package)
    if config.install_remmina:
        packages.extend(("freerdp", "libvncserver", "spice-gtk", "gtk-vnc", "libsecret"))
    if config.install_sysadmin_tools:
        packages.extend(package for _command, package in CACHYOS_SYSADMIN_PACKAGES)
    for command, package in commands:
        if not shutil.which(command, path=_tool_path(home)):
            packages.append(package)
    if config.web_interfaces:
        packages.append("python")  # node-gyp builds
    return packages


def install_cachyos_packages(config: SetupConfig) -> None:
    install_missing_packages(cachyos_packages(config))
    if config.install_git_lfs:
        configure_cachyos_git_lfs(config)


_LFS_FILTERS = {
    "filter.lfs.clean": "git-lfs clean -- %f",
    "filter.lfs.smudge": "git-lfs smudge -- %f",
    "filter.lfs.process": "git-lfs filter-process",
    "filter.lfs.required": "true",
}


def _git_config_values(home: Path, key: str) -> list[str]:
    # Query outside a project so repository-local settings cannot mask a
    # missing user default. Honor existing system and user configuration.
    result = _user_run(["git", "config", "--get-all", key], home,
                       cwd="/", capture_output=True, check=False, timeout=15)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot read Git configuration; inspect git config locally")
    return result.stdout.splitlines() if result.returncode == 0 else []


def configure_cachyos_git_lfs(config: SetupConfig) -> None:
    """Fill missing LFS defaults without replacing filters or repository hooks."""
    home = _home(config)
    for key, default in _LFS_FILTERS.items():
        if not _git_config_values(home, key):
            _user_run(["git", "config", "--global", "--add", key, default], home,
                      cwd="/", capture_output=True, timeout=15)
    print("  Git LFS filters configured; existing values and repository hooks retained")


def install_cachyos_agents(config: SetupConfig) -> None:
    home = _home(config)
    from lib.agent_cli import update_agent_tools

    for tool in config.selected_agent_tools():
        if tool == "gh":
            continue
        executable = shutil.which(tool, path=_tool_path(home))
        if executable:
            try:
                managed = os.path.commonpath(
                    (os.path.realpath(executable), os.path.realpath(home))
                ) == os.path.realpath(home)
            except ValueError:
                managed = False
            if not managed:
                print(f"  Keeping externally managed {tool} ({executable})")
                continue
            print(f"  Updating existing {tool} (vendor checks may take a while)")
            results = update_agent_tools([tool], home=str(home))
            if any(result.get("status") == "failed" for result in results):
                raise RuntimeError(
                    f"{tool} update failed; inspect its private agent update record"
                )
            continue
        if install_vendor_tool(
            tool,
            accept_vendor_channel=True,
            non_interactive=True,
        ) != 0:
            raise RuntimeError(f"{tool} installer failed")
        if not shutil.which(tool, path=_tool_path(home)):
            raise RuntimeError(f"{tool} installer finished without an executable on the user PATH")


def prepare_cachyos_workspace(config: SetupConfig) -> None:
    home = _home(config)
    workspace = Path(config.agent_workspace) if config.agent_workspace else home / "repos"
    _directory(workspace)
    validate_filesystem_path(str(workspace), must_exist=True, check_writable=True)
    for repository in config.agent_repos or []:
        destination = workspace / repository.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError(f"Refusing unsafe repository destination: {destination}")
            result = _user_run(["git", "-C", str(destination), "rev-parse", "--show-toplevel"],
                               home, capture_output=True, check=False)
            if result.returncode or Path(result.stdout.strip()).resolve() != destination.resolve():
                raise ValueError(f"Existing destination is not a repository root: {destination}")
            result = _user_run(["git", "-C", str(destination), "remote", "get-url", "origin"],
                               home, capture_output=True, check=False)
            if result.returncode or result.stdout.strip().rstrip("/") != repository.rstrip("/"):
                raise ValueError(f"Existing repository has a different origin: {destination}")
            validate_filesystem_path(str(destination), must_exist=True, check_writable=True)
            result = _user_run(["git", "-C", str(destination), "rev-parse", "--absolute-git-dir"],
                               home, capture_output=True)
            validate_filesystem_path(result.stdout.strip(), must_exist=True, check_writable=True)
            print(f"  Keeping existing repository path: {destination}")
            continue
        _user_run(["git", "clone", "--", repository, str(destination)], home)


def install_cachyos_skills(config: SetupConfig) -> None:
    unit = _home(config) / ".config/systemd/user" / T3_SERVICE
    existing_t3 = unit.is_file() and not unit.is_symlink() and _MARKER in unit.read_text()
    names = CACHYOS_SKILLS + ((CACHYOS_T3_SKILL,) if config.web_interfaces or existing_t3 else ())
    install_managed_agent_skills(
        config.username, config.selected_agent_tools(), names,
        reconcile_skill_names=(
            *BASE_AGENT_SKILL_NAMES, *BROWSER_AGENT_SKILL_NAMES, CACHYOS_T3_SKILL,
            "infra-tools-desktop", "infra-tools-t3code", "infra-tools-web-gateway",
            "infra-tools-godot-web",
        ),
    )


def install_cachyos_t3(config: SetupConfig) -> None:
    from common.cachyos_t3 import install

    install(config)


def report_cachyos_readiness(config: SetupConfig) -> None:
    home = _home(config)
    commands = ["git", "rg", *config.selected_agent_tools()]
    for enabled, command in ((config.install_node, "node"), (config.install_python, "uv"),
                             (config.install_go, "go"), (config.install_git_lfs, "git-lfs"),
                             (config.install_godot, "godot")):
        if enabled:
            commands.append(command)
    for command in commands:
        executable = shutil.which(command, path=_tool_path(home))
        if executable is None:
            raise RuntimeError(f"Requested command missing: {command}")
        version_arg = "version" if command == "go" else "--version"
        result = _user_run([executable, version_arg], home, capture_output=True, check=False, timeout=30)
        if result.returncode:
            raise RuntimeError(f"{command} failed its version check")
        print(f"  {command}: executable verified")
    identity = _user_run(["git", "var", "GIT_AUTHOR_IDENT"], home,
                         cwd="/", capture_output=True, check=False, timeout=15)
    if identity.returncode:
        print("  WARNING: Default Git author identity unavailable; commits may fail. "
              "Configure user.name and user.email with git config --global, or set "
              "them per repository. Provider login does not configure commit identity.")
    else:
        print("  Git author identity: available outside a project; repository overrides may differ")
    if config.install_git_lfs:
        if any(not any(value.strip() for value in _git_config_values(home, key))
               for key in _LFS_FILTERS):
            raise RuntimeError("Git LFS filters are incomplete; inspect git config and rerun --git-lfs")
        print("  Git LFS: filters present; custom filters and remote transfers require a project test")
    for enabled, commands in (
        (config.install_av_tools, ("ffmpeg", "ffprobe", "magick")),
        (config.install_gl_tools, ("glxinfo", "vulkaninfo")),
    ):
        if enabled:
            for command in commands:
                if not shutil.which(command, path=_tool_path(home)):
                    raise RuntimeError(f"Requested command missing: {command}")
                print(f"  {command}: available; media/GPU behavior requires a project test")
    for enabled, command, package in (
        (config.install_sunshine, "sunshine", "sunshine"),
        (config.install_moonlight, "moonlight", "moonlight-qt"),
    ):
        if enabled:
            if not shutil.which(command, path=_tool_path(home)):
                raise RuntimeError(f"Requested command missing: {command}")
            print(f"  {command}: native CachyOS package available ({package})")
    if config.install_gaming:
        print(
            "  CachyOS gaming bundle: native gaming libraries, launchers, and tools requested; "
            "verify the intended GPU and games interactively"
        )
    for field, command, package in CACHYOS_DESKTOP_PACKAGES:
        enabled = getattr(config, field)
        if enabled:
            if not shutil.which(command, path=_tool_path(home)):
                raise RuntimeError(f"Requested command missing: {command}")
            print(f"  {command}: native CachyOS package available ({package})")
    if config.install_sysadmin_tools:
        for command, package in CACHYOS_SYSADMIN_PACKAGES:
            if not shutil.which(command, path=_tool_path(home)):
                raise RuntimeError(f"Requested command missing: {command}")
            print(f"  {command}: native CachyOS package available ({package})")
        print(
            "  Sysadmin tools do not enable libvirt, grant packet-capture access, "
            "or change network policy"
        )
    print("  Provider authentication: use each provider's local login; existing credentials retained")
    print("  KDE automation and managed Playwright: not installed")


def reconcile_cachyos_user_cache(config: SetupConfig) -> None:
    """Run target-user cache maintenance during setup on hosts without timers."""
    from common.setup_maintenance import run_user_cache_maintenance

    if not run_user_cache_maintenance(config):
        raise RuntimeError("Coding tools were installed, but user cache maintenance is incomplete. "
                           "Resolve the reported cleanup error and rerun setup; "
                           "this profile has no automatic cache retry timer.")
