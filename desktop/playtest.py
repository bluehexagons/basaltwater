"""Prepare per-task application data from project-owned playtest fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from lib.validation import validate_filesystem_path

NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}")
SEED_LIMIT = 16 * 1024 * 1024
XDG_DIRECTORIES = {"XDG_DATA_HOME": "data", "XDG_CONFIG_HOME": "config",
                   "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state"}


def seed_path(value: str) -> Path:
    if not isinstance(value, str):
        raise ValueError("Playtest seed paths must be strings")
    validate_filesystem_path(value)
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or value != path.as_posix():
        raise ValueError("Playtest seed paths must be canonical relative file paths")
    return path


def declarations(value: object) -> dict:
    from desktop.development import validate_argv

    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("playtests must be an object of at most 32 profiles")
    for name, plan in value.items():
        if not NAME.fullmatch(name) or not isinstance(plan, dict) or set(plan) - {"argv", "seed"}:
            raise ValueError("Invalid playtest declaration")
        validate_argv(plan.get("argv"))
        seed = plan.get("seed", {})
        if not isinstance(seed, dict) or len(seed) > 64:
            raise ValueError("Playtest seed must be an object of at most 64 destination/source paths")
        destinations = {seed_path(destination) for destination in seed}
        for path in destinations:
            if path.as_posix() in XDG_DIRECTORIES.values() or any(parent in destinations for parent in path.parents):
                raise ValueError("Playtest seed destinations conflict with a profile directory or another file")
        for source in seed.values():
            seed_path(source)
    return value


def load_plan(project: Path, name: str) -> tuple[list[str], dict]:
    from desktop.development import _executable
    from lib.agent_environment import PROJECT_FILE, load_project_environment

    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid playtest profile name")
    selected = load_project_environment(str(project))["playtests"].get(name)
    if selected is None:
        raise ValueError(f"Playtest {name} is not declared in {PROJECT_FILE}")
    argv = list(selected["argv"])
    if "/" in argv[0] and not Path(argv[0]).is_absolute():
        local = (project / argv[0]).resolve(strict=True)
        if not local.is_relative_to(project):
            raise ValueError("A relative playtest executable must remain within the project")
        argv[0] = str(local)
    argv[0] = _executable(argv[0])
    return argv, selected.get("seed", {})


def _open_seed(project: Path, source: str) -> int:
    """Open the fixture within the project without following any symlink."""
    parts = Path(source).parts
    directory = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)


def seed_profile(project: Path, seed: dict, profile: Path | None = None) -> None:
    """Validate fixtures for a dry run or copy them into a fresh task profile."""
    if profile is not None:
        profile.mkdir(mode=0o700)
        for name in XDG_DIRECTORIES.values():
            (profile / name).mkdir(mode=0o700)
    size = 0
    for destination, source in seed.items():
        with os.fdopen(_open_seed(project, source), "rb") as stream:
            before = os.fstat(stream.fileno())
            size += before.st_size
            if not stat.S_ISREG(before.st_mode) or size > SEED_LIMIT:
                raise ValueError("Playtest fixtures must be regular files totaling at most 16 MiB")
            if profile is not None:
                data = stream.read(before.st_size + 1)
                after = os.fstat(stream.fileno())
                if (len(data) != before.st_size
                        or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError("Playtest fixture changed while copying")
                target = profile / destination
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with target.open("xb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(data)


def profile_environment(profile: Path) -> dict:
    return {"BASALTWATER_PLAYTEST_PROFILE": str(profile),
            **{variable: str(profile / path) for variable, path in XDG_DIRECTORIES.items()}}
