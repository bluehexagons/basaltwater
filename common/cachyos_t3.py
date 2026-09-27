"""Stage CachyOS T3 runtimes before replacing the user's working service."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import filecmp
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tempfile
import time
from typing import Iterator
import urllib.error
import urllib.parse
import urllib.request

from common.cachyos_steps import (
    T3_SERVICE, _MARKER, _directory, _home, _tool_path, _user_run, _write_managed,
)
from lib.atomic_io import read_json_file, write_json_atomic, write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import CommandExecutionError, CommandTimeoutError, run
from lib.validation import validate_arch_package_name, validate_filesystem_path


DESKTOP_PACKAGE = "t3code-bin"


def _desktop_version() -> str | None:
    """Query package metadata without launching Electron or touching T3 data."""
    package = validate_arch_package_name(DESKTOP_PACKAGE)
    result = run(["pacman", "-Q", "--", package], capture_output=True, check=False, timeout=15)
    if result.returncode == 1:
        return None
    parts = (result.stdout or "").split()
    if result.returncode or len(parts) != 2 or parts[0] != package or not re.fullmatch(r"[0-9A-Za-z.+_:~-]+", parts[1]):
        raise RuntimeError("Cannot determine t3code-bin package state; inspect pacman before retrying")
    return parts[1]


def _desktop_install_command(home: Path) -> list[str]:
    """Prefer current CachyOS's Shelly CLI, preserving its review policy."""
    package = validate_arch_package_name(DESKTOP_PACKAGE)
    executable = shutil.which("shelly", path=_tool_path(home))
    if executable:
        return [executable, "install", "aur", package]
    for name in ("paru", "yay"):
        executable = shutil.which(name, path=_tool_path(home))
        if executable:
            return [executable, "-S", "--aur", "--needed", "--", package]
    raise RuntimeError("Installing T3 desktop requires Shelly, paru, or yay; "
                       "restore CachyOS's default helper with sudo pacman -S --needed shelly "
                       "and rerun --t3code-desktop")


def _shelly_cache(home: Path, *, create: bool = False) -> None:
    """Prepare user-owned caches before Shelly elevates and runs Git as us.

    Shelly 3.1.6 creates its cache as root, then drops privileges for Git.
    Check both XDG and default locations because elevation may drop XDG.
    Existing caches are never deleted, chowned, or recursively repaired.
    """
    roots = [home / ".cache"]
    configured = os.environ.get("XDG_CACHE_HOME", "")
    if configured and Path(configured).is_absolute() and Path(configured) not in roots:
        roots.append(Path(configured))
    for root in roots:
        cache = root / "Shelly"
        validate_filesystem_path(str(cache))
        existing = Path(cache.anchor)
        for path in reversed((cache, *cache.parents)):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise ValueError(f"Unsafe Shelly AUR cache directory: {path}")
            if path.exists():
                existing = path
                if not os.access(path, os.X_OK):
                    break  # Report this parent before inspecting its children.
        # The nearest existing parent must let this account create missing
        # directories. Checking this in preflight avoids a late opaque failure.
        if not os.access(existing, os.W_OK | os.X_OK) or (
            existing == cache and existing.stat().st_uid != os.getuid()
        ):
            raise RuntimeError(
                f"Shelly AUR cache is not writable/owned by this user: {existing}. "
                f"Inspect with: ls -ld -- {shlex.quote(str(existing))}. "
                "Repair that directory's ownership/permissions before retrying; "
                "a root-owned Shelly cache can cause its generic download error."
            )
        if create:
            _directory(cache)


def _install_desktop_package(command: list[str], home: Path) -> None:
    shelly = Path(command[0]).name == "shelly"
    if shelly:
        _shelly_cache(home, create=True)
    try:
        # Keep stdin/stdout/stderr attached for review, build, and sudo prompts.
        run(command, interactive=True)
    except (CommandExecutionError, CommandTimeoutError) as exc:
        details = (
            "\nShelly may omit Git's underlying clone/pull error. Check cache "
            "ownership and the configured source with `shelly config get AurUrl`; "
            "see docs/CACHYOS.md (AUR download failures) for a Git-only probe. "
            "Shelly logs: /var/log/shelly.log or "
            "${XDG_STATE_HOME:-$HOME/.local/state}/shelly/shelly.log."
        ) if shelly else ""
        raise RuntimeError(
            f"T3 desktop installation failed: {exc}\n"
            f"Retry as your desktop user: {shlex.join(command)}{details}\n"
            "The managed T3 web service has not been disabled."
        ) from exc


def _check_managed_paths(home: Path) -> tuple[Path, Path]:
    prefix = home / ".local/share/basaltwater/cachyos-t3"
    unit = home / ".config/systemd/user" / T3_SERVICE
    marker = prefix / "desktop-mode"
    for path in (prefix, unit.parent):
        validate_filesystem_path(str(path))
        for parent in (path, *path.parents):
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError(f"Unsafe T3 directory: {parent}")
    for path in (unit, marker):
        if path.is_symlink() or (path.exists() and (
            not path.is_file() or _MARKER not in path.read_text()
        )):
            raise ValueError(f"Refusing unmanaged T3 file: {path}")
    upstream = unit.with_name("t3code.service")
    if upstream.exists() or upstream.is_symlink():
        raise RuntimeError("An existing T3 user service is present; manage it with its original installer")
    dropins = unit.with_name(T3_SERVICE + ".d")
    if dropins.is_symlink() or (dropins.exists() and (
        not dropins.is_dir() or any(dropins.glob("*.conf"))
    )):
        raise RuntimeError("Unmanaged T3 service drop-ins are present; resolve them before switching or updating T3")
    return prefix, unit


def _check_service_ownership(unit: Path) -> None:
    """Inspect effective units, including global/runtime user-unit directories."""
    for name in ("t3code.service", T3_SERVICE):
        result = run([
            "systemctl", "--user", "show", name,
            "--property=LoadState", "--property=FragmentPath", "--property=DropInPaths",
            "--property=ActiveState",
        ], capture_output=True, check=False, timeout=15)
        values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
        if result.returncode or set(values) != {"LoadState", "FragmentPath", "DropInPaths", "ActiveState"}:
            raise RuntimeError("Cannot inspect T3 user service ownership; check the local systemd user session")
        if (values["LoadState"] == "not-found" and values["ActiveState"] == "inactive"
                and not values["FragmentPath"] and not values["DropInPaths"]):
            continue
        if name == "t3code.service":
            raise RuntimeError("An existing T3 user service is present; manage it with its original installer")
        if (values["LoadState"] != "loaded" or values["FragmentPath"] != str(unit)
                or values["DropInPaths"] or not unit.is_file()):
            raise RuntimeError("Unmanaged or overridden Basaltwater T3 service; resolve it before switching or updating T3")


def preflight(config: SetupConfig) -> None:
    """Check T3 prerequisites before the setup runner installs any packages."""
    home = _home(config)
    _prefix, unit = _check_managed_paths(home)
    _check_service_ownership(unit)
    if config.t3code_desktop and _desktop_version() is None:
        command = _desktop_install_command(home)
        if Path(command[0]).name == "shelly":
            _shelly_cache(home)


def install_desktop(config: SetupConfig) -> None:
    """Retain AUR ownership and retire only the Basaltwater web service."""
    home = _home(config)
    prefix, unit = _check_managed_paths(home)
    _check_service_ownership(unit)
    version = _desktop_version()
    if version is None:
        command = _desktop_install_command(home)
        # Run as the desktop user with a terminal for sudo/build prompts. No
        # database refresh, full-system upgrade, or root AUR build is requested.
        _install_desktop_package(command, home)
        version = _desktop_version()
        if version is None:
            raise RuntimeError("AUR installation finished without t3code-bin")
    executable = shutil.which("t3code", path="/usr/bin:/bin")
    if not executable:
        raise RuntimeError("t3code-bin is installed but its desktop executable is missing; repair it with your AUR helper")
    owner = run(["pacman", "-Qqo", "--", executable], capture_output=True, check=False, timeout=15)
    if owner.returncode or owner.stdout.strip() != DESKTOP_PACKAGE:
        raise RuntimeError("T3 desktop executable is not owned by t3code-bin; inspect the package installation")
    _directory(prefix)
    with _setup_lock(prefix):
        _check_managed_paths(home)
        _check_service_ownership(unit)
        _recover_activation(prefix, unit, start_previous=False)
        if unit.exists():
            run(["systemctl", "--user", "disable", "--now", T3_SERVICE])
            active = run(["systemctl", "--user", "is-active", T3_SERVICE], capture_output=True, check=False)
            if active.returncode != 3 or active.stdout.strip() not in {"inactive", "failed"}:
                raise RuntimeError("Could not verify that the managed T3 web service stopped; inspect systemctl --user")
            enabled = run(["systemctl", "--user", "is-enabled", T3_SERVICE], capture_output=True, check=False)
            if enabled.returncode != 1 or enabled.stdout.strip() != "disabled":
                raise RuntimeError("Managed T3 web service remains enabled; inspect user/global systemd enablement")
        _write_managed(prefix / "desktop-mode", _MARKER + "\n", mode=0o600)
    print(f"  T3 desktop: {DESKTOP_PACKAGE} {version}; updates remain with your AUR helper")
    print("  Managed web service disabled; desktop settings, credentials, and all T3 data retained")
    print("  Open T3 Code from KDE and verify a provider thread and terminal")
    for tool in config.selected_agent_tools():
        if tool != "gh":
            binary = shutil.which(tool, path=_tool_path(home))
            if binary:
                print(f"  {tool} provider binary: {binary} (use in T3 provider settings if discovery fails)")


def _unit_quote(value: str) -> str:
    return json.dumps(value.replace("%", "%%"), ensure_ascii=False)


def _unit_exec_quote(value: str) -> str:
    # systemd expands $ even inside quoted ExecStart arguments.
    return _unit_quote(value.replace("$", "$$"))


def _unit_path(value: str) -> str:
    validate_filesystem_path(value)
    return value.replace("\\", "\\x5c").replace("%", "%%").replace(" ", "\\x20")


@contextmanager
def _setup_lock(prefix: Path) -> Iterator[None]:
    descriptor = os.open(prefix / ".setup.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Unsafe T3 setup lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another CachyOS T3 setup is running; retry after it finishes") from exc
        yield
    finally:
        os.close(descriptor)


def _check_binary(binary: Path, prefix: Path) -> None:
    if not binary.is_file() or not binary.resolve().is_relative_to(prefix.resolve()):
        raise ValueError(f"Refusing unsafe T3 runtime executable: {binary}")


def _check_runtime(prefix: Path, home: Path) -> str:
    binary = prefix / "bin/t3"
    _check_binary(binary, prefix)
    result = _user_run([str(binary), "--version"], home, capture_output=True, timeout=30)
    output = (result.stdout or result.stderr or "").strip()
    match = re.fullmatch(r"(?:t3 )?v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)", output)
    if not match:
        raise RuntimeError("T3 runtime did not report a valid version")
    version = match.group(1)
    # Version output alone does not load the native terminal addon. Older T3
    # releases place node-pty under t3, while newer releases bundle it under
    # the platform-specific CLI package. Exercise a disposable shell without
    # contacting providers or starting a T3 server.
    script = (
        "const root = process.argv[1];"
        "const paths = [root];"
        "try {"
        "  const platformPackage = '@t3code/t3-' + process.platform + '-' + process.arch;"
        "  const packageJson = require.resolve(platformPackage + '/package.json', {paths: [root]});"
        "  paths.push(require('node:path').dirname(packageJson));"
        "} catch {}"
        "const pty = require(require.resolve('node-pty', {paths}));"
        "const child = pty.spawn('/bin/sh', ['-c', 'exit 0'], {cwd: '/', env: {PATH: '/usr/bin:/bin'}});"
        "const timer = setTimeout(() => {child.kill(); process.exit(1)}, 5000);"
        "child.onExit(({exitCode}) => {clearTimeout(timer); process.exit(exitCode === 0 ? 0 : 1)});"
    )
    _user_run(["node", "-e", script, str(prefix / "lib/node_modules/t3")], home,
              capture_output=True, timeout=15)
    return version


def _wait_for_ui(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    expected = urllib.parse.urlsplit(url)

    def same_origin(location: str) -> bool:
        try:
            observed = urllib.parse.urlsplit(location)
            return (
                observed.scheme == expected.scheme
                and observed.hostname == expected.hostname
                and observed.port == expected.port
            )
        except ValueError:
            return False

    stable = 0
    for _attempt in range(20):
        active = run(["systemctl", "--user", "is-active", "--quiet", T3_SERVICE], check=False)
        if active.returncode == 0:
            try:
                with opener.open(url, timeout=2) as response:
                    if response.status == 200 and same_origin(response.geturl()):
                        stable += 1
                        if stable >= 3:
                            print(f"  T3 HTTP UI reachable: {url}")
                            print("  Provider threads and interactive terminals still require a client test")
                            return
                    else:
                        stable = 0
            except (OSError, urllib.error.URLError):
                stable = 0
        else:
            stable = 0
        time.sleep(1)
    raise RuntimeError(f"T3 UI did not become reachable; inspect journalctl --user -u {T3_SERVICE}")


def _prune_releases(releases: Path, keep: set[Path]) -> None:
    """Retain the current and previous runtime; never adopt unmarked directories."""
    for entry in releases.iterdir():
        marker = entry / ".basaltwater-release"
        if entry in keep or entry.is_symlink() or not entry.is_dir():
            continue
        try:
            if marker.is_symlink() or not marker.is_file() or marker.read_text() != _MARKER:
                continue
            shutil.rmtree(entry)
        except (OSError, UnicodeError):
            print("  WARNING: An older T3 runtime could not be pruned; current and previous releases retained")


def install(config: SetupConfig) -> None:
    home = _home(config)
    prefix, unit = _check_managed_paths(home)
    _check_service_ownership(unit)
    version = _user_run(["node", "--version"], home, capture_output=True, timeout=30).stdout.strip()
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise RuntimeError("Cannot determine Node version")
    major, minor, _patch = map(int, match.groups())
    if not ((major == 22 and minor >= 16) or (major == 23 and minor >= 11)
            or (major == 24 and minor >= 10) or major > 24):
        raise RuntimeError("T3 requires Node 22.16+, 23.11+, or 24.10+; update your Node runtime and rerun")

    _directory(unit.parent)
    _directory(prefix)
    with _setup_lock(prefix):
        _check_managed_paths(home)
        _check_service_ownership(unit)
        _recover_activation(prefix, unit)
        _install_locked(config, home, prefix, unit)
        (prefix / "desktop-mode").unlink(missing_ok=True)


def _copy_entry(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=".t3-copy-", dir=destination.parent) as temporary:
        entry = Path(temporary) / "entry"
        shutil.copy2(source, entry, follow_symlinks=False)
        if not entry.is_symlink():
            with entry.open("rb") as stream:
                os.fsync(stream.fileno())
        os.replace(entry, destination)


def _same_entry(left: Path, right: Path) -> bool:
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)
    return left.is_file() and right.is_file() and filecmp.cmp(left, right, shallow=False)


def _recover_activation(prefix: Path, unit: Path, *, start_previous: bool = True) -> None:
    transaction = prefix / ".activation"
    if not transaction.exists() and not transaction.is_symlink():
        return
    _directory(transaction)
    owner = transaction / "owner"
    if owner.is_symlink() or not owner.is_file() or owner.read_text() != _MARKER:
        raise ValueError("Unmanaged T3 .activation directory; inspect it before retrying")
    if (transaction / "committed").is_file() or not (transaction / "state.json").exists():
        # No activation began, or it passed its UI check before interruption.
        shutil.rmtree(transaction)
        return
    state = read_json_file(str(transaction / "state.json"), max_bytes=1024)
    if (not isinstance(state, dict)
            or set(state) != {"had_binary", "had_unit", "was_active", "was_enabled", "unit_mode"}
            or any(type(state[key]) is not bool for key in ("had_binary", "had_unit", "was_active", "was_enabled"))
            or type(state["unit_mode"]) is not int or not 0 <= state["unit_mode"] <= 0o777):
        raise ValueError("Invalid T3 recovery record; inspect the private .activation directory")
    proposed = transaction / T3_SERVICE
    previous = transaction / "previous-unit"
    allowed_units = [proposed.read_text()]
    if state["had_unit"]:
        allowed_units.append(previous.read_text())
    unit_present = unit.exists() or unit.is_symlink()
    if state["had_unit"] and not unit_present:
        raise ValueError("T3 unit was removed outside setup; preserve and inspect .activation before retrying")
    if unit.is_symlink() or (unit_present and unit.read_text() not in allowed_units):
        raise ValueError("T3 unit changed outside setup; preserve and inspect .activation before retrying")
    binary = prefix / "bin/t3"
    _directory(binary.parent)
    binary_present = binary.exists() or binary.is_symlink()
    if state["had_binary"] and not binary_present:
        raise ValueError("T3 executable was removed outside setup; inspect .activation before retrying")
    allowed_binaries = ("previous-t3", "next-t3") if state["had_binary"] else ("next-t3",)
    if binary_present and not any(
        _same_entry(binary, transaction / name) for name in allowed_binaries
    ):
        raise ValueError("T3 executable changed outside setup; inspect .activation before retrying")
    stopped = run(["systemctl", "--user", "stop", T3_SERVICE], check=False)
    if stopped.returncode not in (0, 5):
        raise RuntimeError("Cannot stop T3 for recovery; runtime snapshots retained in .activation")
    if not state["was_enabled"] and unit.exists():
        run(["systemctl", "--user", "disable", T3_SERVICE])
    if state["had_binary"]:
        _copy_entry(transaction / "previous-t3", binary)
    else:
        binary.unlink(missing_ok=True)
    if state["had_unit"]:
        write_text_atomic(str(unit), previous.read_text(), mode=state["unit_mode"])
    else:
        unit.unlink(missing_ok=True)
    run(["systemctl", "--user", "daemon-reload"])
    if state["was_active"] and start_previous:
        run(["systemctl", "--user", "start", T3_SERVICE])
    shutil.rmtree(transaction)
    print("  Restored the previous T3 runtime and unit. Application data was not rolled back.")


def _activate(prefix: Path, unit: Path, candidate: Path, content: str, url: str) -> None:
    binary = prefix / "bin/t3"
    transaction = prefix / ".activation"
    transaction.mkdir(mode=0o700)
    write_text_atomic(str(transaction / "owner"), _MARKER)
    proposed = transaction / T3_SERVICE
    proposed.write_text(content)
    try:
        run(["systemd-analyze", "--user", "verify", str(proposed)], capture_output=True, timeout=30)
        had_binary = binary.exists() or binary.is_symlink()
        had_unit = unit.exists()
        if had_binary:
            _copy_entry(binary, transaction / "previous-t3")
        if had_unit:
            write_text_atomic(str(transaction / "previous-unit"), unit.read_text())
        was_active = run(["systemctl", "--user", "is-active", "--quiet", T3_SERVICE], check=False).returncode == 0
        was_enabled = run(["systemctl", "--user", "is-enabled", "--quiet", T3_SERVICE], check=False).returncode == 0
        write_json_atomic(str(transaction / "state.json"), {
            "had_binary": had_binary, "had_unit": had_unit,
            "was_active": was_active, "was_enabled": was_enabled,
            "unit_mode": stat.S_IMODE(unit.stat().st_mode) if had_unit else 0o644,
        })
        if had_unit or was_active:
            run(["systemctl", "--user", "stop", T3_SERVICE])
        link = transaction / "next-t3"
        link.symlink_to(os.path.relpath(candidate / "bin/t3", binary.parent))
        _copy_entry(link, binary)
        _write_managed(unit, content)
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", T3_SERVICE])
        run(["systemctl", "--user", "start", T3_SERVICE])
        _wait_for_ui(url)
        write_text_atomic(str(transaction / "committed"), "UI check passed\n")
    except BaseException:
        try:
            _recover_activation(prefix, unit)
        except Exception as exc:
            raise RuntimeError("T3 recovery is incomplete; snapshots and runtimes retained in "
                               f"{transaction}. Resolve the service error and rerun setup to retry recovery.") from exc
        raise
    shutil.rmtree(transaction)


def _install_locked(config: SetupConfig, home: Path, prefix: Path, unit: Path) -> None:
    binary = prefix / "bin/t3"
    releases = prefix / "releases"
    _directory(binary.parent)
    _directory(releases)
    had_binary = binary.exists() or binary.is_symlink()
    if had_binary:
        _check_binary(binary, prefix)
    previous_target = binary.resolve() if had_binary else None
    candidate = Path(tempfile.mkdtemp(prefix="candidate-", dir=releases))
    activated = False
    try:
        # npm never writes into the active prefix, even when the package version
        # is unchanged. Native dependencies may need rebuilding after a Node update.
        _user_run(["npm", "install", "--global", "--prefix", str(candidate),
                   "--allow-scripts=node-pty,msgpackr-extract", "t3@latest"], home)
        version = _check_runtime(candidate, home)
        (candidate / ".basaltwater-release").write_text(_MARKER)
        release = releases / f"{version}-{candidate.name.removeprefix('candidate-')}"
        candidate.rename(release)
        candidate = release
        _check_runtime(candidate, home)
        workspace = config.agent_workspace or str(home / "repos")
        _directory(Path(workspace))
        validate_filesystem_path(workspace, must_exist=True, check_writable=True)
        host = config.web_interface_host or "127.0.0.1"
        data = prefix / "data"
        _directory(data)
        if unit.exists() and "--base-dir" not in unit.read_text():
            print("  Moving the managed web service to isolated data; previous ~/.t3 data is retained, not migrated")
        content = (
            f"{_MARKER}\n[Unit]\nDescription=Local CachyOS T3 Code\n"
            "\n[Service]\nType=simple\nUMask=0077\n"
            f"WorkingDirectory={_unit_path(workspace)}\n"
            f"Environment={_unit_quote('PATH=' + _tool_path(home))}\n"
            "UnsetEnvironment=T3CODE_STATE_DIR T3CODE_BASE_DIR T3CODE_HOME\n"
            f"ExecStart={_unit_exec_quote(str(candidate / 'bin/t3'))} serve --host {host} "
            f"--port {config.web_interface_port} --base-dir {_unit_exec_quote(str(data))} --no-browser\n"
            "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
        )
        _activate(prefix, unit, candidate, content, f"http://{host}:{config.web_interface_port}/")
        activated = True
        keep = {candidate}
        if previous_target is not None and previous_target.is_relative_to(releases):
            keep.add(releases / previous_target.relative_to(releases).parts[0])
        _prune_releases(releases, keep)
    finally:
        if not activated and not (prefix / ".activation").exists():
            shutil.rmtree(candidate)
