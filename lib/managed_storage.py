"""Host storage preflight and conservative native cache maintenance.

Run ``python3 -m lib.managed_storage --help`` on the managed host.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
from functools import wraps
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from lib.agent_storage_inventory import inspect_storage, _mounts
from lib.maintenance_defaults import DEFAULT_SETUP_TMPDIR
from lib.validation import validate_filesystem_path
from lib.validators import validate_username
from lib.streamed_process import run_streamed

LOCK = "/run/basaltwater-storage.lock"
BUILD_HOMES = "/var/lib/basaltwater/build-users"
_held = ContextVar("managed_storage_lock", default=False)
_lock_fd = ContextVar("managed_storage_lock_fd", default=None)
_deployment_cleanup_accounts: ContextVar[list[str] | None] = ContextVar(
    "deployment_cleanup_accounts", default=None)
MIB = 1024 * 1024
CACHES = (
    (".npm/_cacache", "npm-content", "Packages must be downloaded again"),
    (".nvm/.cache", "nvm-downloads", "Node archives must be downloaded again"),
    (".cache/go-build", "go-build", "Go packages must be recompiled"),
)
BUILD_CACHES = (
    (".npm/_logs", "npm-logs", "Old npm diagnostics are discarded"),
    (".npm/_npx", "npx-builds", "Tools must be downloaded again"),
    ("go/pkg/mod", "go-modules", "Modules and downloaded toolchains must be downloaded again"),
    (".cache/uv", "uv-downloads", "Python packages must be downloaded again"),
    (".cache/pip", "pip-downloads", "Python packages must be downloaded again"),
)


class StoragePreflightError(OSError):
    """Capacity is below the configured operation requirements."""

    def __init__(self, message: str, failures: list[dict]):
        super().__init__(message)
        self.failures = failures


@contextmanager
def storage_lock(*, cleanup: bool = False):
    """Exclude cleanup across setup and deployments, including nested calls."""
    if _held.get():
        if cleanup:
            raise ValueError("Cannot clean caches inside setup or deployment")
        yield
        return
    fd = os.open(LOCK, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError("Unsafe storage maintenance lock")
        fcntl.flock(fd, (fcntl.LOCK_EX if cleanup else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        token = _held.set(True)
        fd_token = _lock_fd.set(fd)
        try:
            yield
        finally:
            _held.reset(token)
            _lock_fd.reset(fd_token)
    finally:
        os.close(fd)


def filesystem(path: str) -> dict:
    """Observe the filesystem of the nearest existing parent, including tmpfs."""
    validate_filesystem_path(path)
    current = Path(path).absolute()
    while not current.exists():
        current = current.parent
    current = current.resolve()
    mounts = _mounts()
    mount = max((p for p in mounts if current == Path(p) or Path(p) in current.parents), key=len)
    usage = os.statvfs(current)
    return {"path": path, "mount": mount, "filesystem_type": mounts[mount],
            "total_bytes": usage.f_blocks * usage.f_frsize, "total_inodes": usage.f_files,
            "free_bytes": usage.f_bavail * usage.f_frsize,
            "free_inodes": usage.f_favail, "inodes_supported": usage.f_files != 0}


def preflight(phase: str, paths: list[str], *, reserve_bytes: int = 512 * MIB,
              peak_bytes: int = 512 * MIB, min_inodes: int = 10000) -> list[dict]:
    """Require an estimated peak plus a reserve independently on every mount."""
    rows = _preflight_observations(paths, reserve_bytes=reserve_bytes, peak_bytes=peak_bytes, min_inodes=min_inodes)
    return _check_preflight(phase, rows)


def _preflight_observations(paths: list[str], *, reserve_bytes: int, peak_bytes: int, min_inodes: int) -> list[dict]:
    if min(reserve_bytes, peak_bytes, min_inodes) < 0:
        raise ValueError("Storage thresholds cannot be negative")
    rows = [filesystem(path) for path in dict.fromkeys(paths)]
    for row in rows:
        row.update(required_bytes=reserve_bytes + peak_bytes, required_inodes=min_inodes)
    return rows


def _check_preflight(phase: str, rows: list[dict]) -> list[dict]:
    failures = [row for row in rows if row["free_bytes"] < row["required_bytes"] or (
        row["inodes_supported"] and row["free_inodes"] < row["required_inodes"])]
    if failures:
        totals = inspect_storage(paths=[BUILD_HOMES], timeout=5)["candidates"]
        raise StoragePreflightError(f"Storage preflight failed during {phase}: {json.dumps(failures)}; "
                      f"managed build storage: {json.dumps(totals)}. "
                      "Preview managed storage cleanup or increase capacity/adjust peak estimates.",
                      failures)
    return rows


def needs_reclamation(observed: dict, *, target: int, requirements: dict[str, dict] | None = None) -> bool:
    """Select only constrained filesystems and include preflight inode needs."""
    minimum_inodes = 0
    if requirements is not None:
        required = requirements.get(observed["mount"])
        if required is None:
            return False
        target = max(target, required["required_bytes"])
        minimum_inodes = required["required_inodes"]
    return observed["free_bytes"] < target or bool(
        minimum_inodes and observed["inodes_supported"] and observed["free_inodes"] < minimum_inodes)


def configured_preflight(phase: str, paths: list[str]) -> list[dict]:
    """Environment overrides express estimated peak usage, not guarantees."""
    temporary = {"/tmp", os.environ.get("TMPDIR", "/tmp")}
    rows = _preflight_observations([p for p in paths if p not in temporary],
        reserve_bytes=int(os.environ.get("BASALTWATER_STORAGE_RESERVE_MIB", "512")) * MIB,
        peak_bytes=int(os.environ.get("BASALTWATER_STORAGE_PEAK_MIB", "512")) * MIB,
        min_inodes=int(os.environ.get("BASALTWATER_STORAGE_MIN_INODES", "10000")))
    rows.extend(_preflight_observations([p for p in paths if p in temporary],
        reserve_bytes=int(os.environ.get("BASALTWATER_STORAGE_TMP_RESERVE_MIB", "64")) * MIB,
        peak_bytes=int(os.environ.get("BASALTWATER_STORAGE_TMP_PEAK_MIB", "128")) * MIB,
        min_inodes=int(os.environ.get("BASALTWATER_STORAGE_MIN_INODES", "10000"))))
    # Report every constrained filesystem before the single recovery pass.
    return _check_preflight(phase, rows)


def _cleanup_preflight_pressure(phase: str, failures: list[dict], names: list[str]) -> None:
    """Reclaim eligible storage on failing mounts while holding exclusive ownership."""
    from lib.storage_pressure import clean_system_storage, clean_build_versions

    setup = phase == "setup"
    requirements = {}
    for row in failures:
        reserve = 1536 * MIB if setup else 0
        if row["mount"] != "/" and row.get("filesystem_type") in {"tmpfs", "ramfs"}:
            reserve = min(reserve, row["total_bytes"] // 10)
        required = requirements.setdefault(row["mount"], dict(required_bytes=0, required_inodes=0))
        required["required_bytes"] = max(required["required_bytes"], reserve, row["required_bytes"])
        required["required_inodes"] = max(required["required_inodes"], row["required_inodes"])
    print(f"Storage is below {phase} requirements. Running one disposable-cache cleanup pass...", flush=True)
    try:
        for row in clean_system_storage(target=0, apply=True, requirements=requirements):
            change = ""
            if "net_freed_bytes" in row:
                change = f"; free space change {row['net_freed_bytes'] / MIB:+.1f} MiB"
            if "net_freed_inodes" in row:
                change += f"; free inodes {row['net_freed_inodes']:+d}"
            print(f"  {row['category']}: {row['status']}{change}" + (f" ({row['reason']})" if row.get("reason") else ""))
        result = maintain(names=names, apply=True, budget=1024 * MIB if setup else None,
                          target=0, requirements=requirements)
        for row in clean_build_versions(target=0, apply=True, requirements=requirements):
            print(f"  {row['path']}: {row['status']}" + (f" ({row['reason']})" if row.get("reason") else ""))
    except (OSError, ValueError) as exc:
        print(f"  Warning: cache maintenance deferred or failed: {exc}")
        return
    for account in result["accounts"]:
        cleaned = sum(row["status"] == "cleaned" for row in account["candidates"])
        print(f"  Cache maintenance for {account['account']}: {cleaned} cleaned")
        if account.get("error"):
            print(f"  Warning: {account['error']}")
        for row in account["candidates"]:
            if row["status"] in {"deferred", "skipped_unsafe_or_incomplete"} or row["status"].startswith("failed"):
                print(f"  {row['category']}: {row['status']}" + (f" ({row['reason']})" if row.get("reason") else ""))


@contextmanager
def _operation_storage(phase: str, paths: list[str], cleanup_accounts: list[str] | None,
                       recover_deployments: bool):
    if cleanup_accounts is not None and not recover_deployments:
        # Release shared ownership before attempting exclusive cache cleanup.
        try:
            with storage_lock():
                configured_preflight(phase, paths)
        except StoragePreflightError as exc:
            try:
                with storage_lock(cleanup=True):
                    _cleanup_preflight_pressure(phase, exc.failures, cleanup_accounts)
            except (OSError, ValueError) as cleanup_error:
                print(f"  Warning: cache maintenance deferred or failed: {cleanup_error}")

    # Keep exclusive ownership across setup with repository builds so recovery
    # between sites never drops the lock or races an independent build.
    with storage_lock(cleanup=recover_deployments):
        names = cleanup_accounts if recover_deployments else (
            _deployment_cleanup_accounts.get() if phase == "deployment" else None)
        try:
            configured_preflight(phase, paths)
        except StoragePreflightError as exc:
            if names is None:
                raise
            _cleanup_preflight_pressure(phase, exc.failures, names)
            configured_preflight(phase, paths)
        token = _deployment_cleanup_accounts.set(cleanup_accounts) if recover_deployments else None
        try:
            yield
        finally:
            if token is not None:
                _deployment_cleanup_accounts.reset(token)


@contextmanager
def operation_guard(phase: str, paths: list[str], *, cleanup_accounts: list[str] | None = None,
                    recover_deployments: bool = False):
    """One host-operation boundary, mocked by unrelated orchestration tests."""
    with _operation_storage(phase, paths, cleanup_accounts, recover_deployments):
        try:
            yield
        except Exception as exc:
            if getattr(exc, "errno", None) != 28 and "no space left" not in str(exc).lower():
                raise
            try:
                capacity = [filesystem(path) for path in dict.fromkeys(paths)]
                totals = inspect_storage(paths=[BUILD_HOMES], timeout=5)["candidates"]
                detail = json.dumps({"phase": phase, "filesystems": capacity, "managed_storage": totals})
            except (OSError, ValueError):
                detail = f"phase={phase}; storage observations unavailable"
            raise OSError(28, f"Storage exhausted: {detail}") from exc


@contextmanager
def _operation_tmpdir():
    """Keep Python and child tools on the filesystem checked by preflight."""
    previous = os.environ.get("TMPDIR")
    directory = previous or DEFAULT_SETUP_TMPDIR
    validate_filesystem_path(directory)
    if not os.path.isabs(directory) or not os.path.isdir(directory) or not os.access(directory, os.W_OK | os.X_OK):
        raise ValueError(f"Setup and deployment TMPDIR must be an absolute, writable directory: {directory}")
    saved_cache = tempfile.tempdir
    os.environ["TMPDIR"] = directory
    tempfile.tempdir = directory
    try:
        yield directory
    finally:
        tempfile.tempdir = saved_cache
        if previous is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous


def guarded_setup(function):
    @wraps(function)
    def wrapped(config, args):
        if args.dry_run:
            return function(config, args)
        paths = ["/opt/basaltwater", "/var/cache/apt/archives", "/var/lib/basaltwater"]
        try:
            paths.append(pwd.getpwnam(config.username).pw_dir)
            cleanup_accounts = [config.username]
        except KeyError:
            paths.append(f"/home/{config.username}")
            cleanup_accounts = []
        if config.deploy_specs:
            paths.extend([BUILD_HOMES, "/var/www"])
        operation_started = False
        try:
            with _operation_tmpdir() as scratch, operation_guard(
                "setup", [*paths, scratch], cleanup_accounts=cleanup_accounts,
                recover_deployments=bool(config.deploy_specs and not config.artifact_deployments),
            ):
                operation_started = True
                return function(config, args)
        except StoragePreflightError as exc:
            if operation_started:
                # Preserve the setup runner's failure journal for a later
                # deployment refusal after setup mutations have begun.
                raise
            print(f"Error: {exc}", file=sys.stderr)
            print("On the target, inspect: sudo python3 -B -m lib.managed_storage --pressure\n"
                  "Inspect the daily job: sudo journalctl -u basaltwater-storage.service -n 100 --no-pager\n"
                  "Setup was stopped; disposable-cache cleanup could not provide the required capacity.",
                  file=sys.stderr)
            return 1
    return wrapped


def guarded_deployment(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        paths = [self.base_dir, BUILD_HOMES]
        if "staging_root" in kwargs:
            paths.append(kwargs["staging_root"])
        with _operation_tmpdir() as scratch, operation_guard("deployment", [*paths, scratch]):
            return function(self, *args, **kwargs)
    return wrapped


def accounts(names: list[str]) -> list:
    """Root, exact managed build homes, and explicitly selected host accounts."""
    selected = {"root", *names}
    entries = pwd.getpwall()
    for entry in entries:
        if entry.pw_name.startswith("build-") and entry.pw_dir == f"{BUILD_HOMES}/{entry.pw_name}":
            selected.add(entry.pw_name)
    if len(selected) > 128:
        raise ValueError("Maintenance account inventory exceeds 128 accounts")
    for name in selected:
        if not validate_username(name):
            raise ValueError("Invalid maintenance account")
    result = []
    for name in sorted(selected):
        try:
            result.append(pwd.getpwnam(name))
        except KeyError as exc:
            raise ValueError(f"Unknown maintenance account: {name}") from exc
    return result


def activity(uid: int) -> str | None:
    """Defer on any build-account activity or relevant root tool; fail closed."""
    try:
        for index, entry in enumerate(Path("/proc").iterdir()):
            if index > 32768:
                return "process inventory limit"
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                name = (entry / "comm").read_text().strip()
                if uid != 0 or re.fullmatch(r"node|npm.*|npx|pnpm.*|yarn.*|go|compile|link|gcc.*|cc1.*|make|curl|wget|apt.*|dpkg.*", name):
                    return "account has active processes"
            except FileNotFoundError:
                continue
    except (OSError, ValueError):
        return "process activity unknown"
    return None


def _native_cleanup(category: str, expected_home: str, device: int, inode: int) -> None:
    """Execute as the owner with fixed paths, no shell profiles or Go hooks."""
    categories = CACHES + BUILD_CACHES
    if category not in {item[1] for item in categories}:
        raise ValueError("Unknown cache category")
    home = pwd.getpwuid(os.geteuid()).pw_dir
    validate_filesystem_path(home, must_exist=True)
    if home != expected_home:
        raise ValueError("Maintenance account home changed")
    if category in {item[1] for item in BUILD_CACHES} and not (
            home.startswith(BUILD_HOMES + "/build-") and Path(home).parent == Path(BUILD_HOMES)):
        raise ValueError("Extended cleanup requires an exact managed build home")
    relative = next(relative for relative, name, _ in categories if name == category)
    path = f"{home}/{relative}"
    if not safe_cache_path(path, home, os.geteuid()):
        raise ValueError("Cache path is unsafe")
    info = os.lstat(path)
    if (info.st_dev, info.st_ino) != (device, inode):
        raise ValueError("Cache changed since inventory")
    measurement = inspect_storage(home=home, paths=[path], timeout=10)
    if measurement["issues"] or not measurement["candidates"]:
        raise ValueError("Worker cache inventory unavailable")
    row = measurement["candidates"][0]
    allowed_issues = {"symlinks_skipped"} if category in {item[1] for item in BUILD_CACHES} else set()
    if (row["status"] != "measured" or set(row["issues"]) - allowed_issues or row["other_owner_entries"]
            or row["hardlinked_files"] or row["is_mountpoint"]):
        raise ValueError("Worker cache inventory is unsafe or incomplete")
    if allowed_issues:
        # These exact managed build caches are reproducible. rmtree unlinks
        # internal symlinks and never follows them into persistent data.
        _remove_build_cache(path)
        return
    env = {"HOME": home, "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
           "GOENV": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOCACHEPROG": "",
           "GOCACHE": f"{home}/.cache/go-build", "NVM_DIR": f"{home}/.nvm",
           "NPM_CONFIG_IGNORE_SCRIPTS": "true", "NPM_CONFIG_UPDATE_NOTIFIER": "false"}
    try:
        if category != "npm-content":
            _run_native(category, env)
        else:
            # Allocation can fail before npm starts on a full temporary
            # filesystem. Exact managed build caches still permit fallback.
            with tempfile.TemporaryDirectory(prefix="basaltwater-cache-config-") as directory:
                for name in ("user", "global"):
                    config_path = Path(directory) / (name + ".npmrc")
                    config_path.write_text("")
                    env["NPM_CONFIG_" + name.upper() + "CONFIG"] = str(config_path)
                _run_native(category, env)
    except (OSError, subprocess.SubprocessError):
        if home.startswith(BUILD_HOMES + "/build-") and Path(home).parent == Path(BUILD_HOMES):
            _fallback_build_cache(category, path, home)
        else:
            raise


def _fallback_build_cache(category: str, path: str, home: str) -> None:
    """Retain runtime exclusion when a native Node cache cleaner fails."""
    from lib.node_runtime_ownership import runtime_lock

    root = Path(home) / ".nvm"
    if category != "go-build" and os.path.lexists(root):
        with runtime_lock(root):
            _remove_build_cache(path)
    else:
        _remove_build_cache(path)


def _remove_build_cache(path: str) -> None:
    """Discard an already validated, inactive, owner-controlled build cache."""
    if not os.path.exists(path):
        return
    for directory, _dirs, _files in os.walk(path, followlinks=False):
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("Build cache changed during cleanup")
        os.chmod(directory, stat.S_IMODE(info.st_mode) | 0o700, follow_symlinks=False)
    shutil.rmtree(path)


def _run_native(category: str, env: dict[str, str]) -> None:
    from lib.node_runtime_ownership import runtime_lock

    if category == "go-build":
        subprocess.run(["go", "clean", "-cache"], env=env, cwd="/", check=True, timeout=120)
        return
    if category == "npm-content" and not os.path.lexists(Path(env["NVM_DIR"]) / "nvm.sh"):
        subprocess.run(["npm", "--cache", f'{env["HOME"]}/.npm', "cache", "clean", "--force"],
                       env=env, cwd="/", check=True, timeout=120)
        return
    with runtime_lock(env["NVM_DIR"]):
        command = '. "$NVM_DIR/nvm.sh" --no-use && '
        command += ('nvm cache clear' if category == "nvm-downloads" else
                    'nvm exec default npm --cache "$HOME/.npm" cache clean --force')
        subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", command],
                       env=env, cwd="/", check=True, timeout=120)


def safe_cache_path(path: str, home: str, uid: int) -> bool:
    """Reject linked or writable parent directories before native commands."""
    if os.path.realpath(path) != path:
        return False
    current = Path(path)
    while current.is_relative_to(home):
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            return False
        if current == Path(home):
            return True
        current = current.parent
    return False


def maintain(*, names: list[str], apply: bool, budget: int | None, target: int, force: bool = False,
             requirements: dict[str, dict] | None = None) -> dict:
    """Inventory all candidates; evict only when over budget or below reserve."""
    if target < 0 or (budget is not None and budget < 0):
        raise ValueError("Storage policy cannot be negative")
    result = {"read_only": not apply, "accounts": [], "shortfalls": []}
    for account in accounts(names):
        home = account.pw_dir
        record = {"account": account.pw_name, "uid": account.pw_uid, "candidates": []}
        result["accounts"].append(record)
        try:
            validate_filesystem_path(home)
            if not os.path.lexists(home):
                record["status"] = "absent_home"
                continue
            validate_filesystem_path(home, must_exist=True)
            categories = CACHES
            if account.pw_name.startswith("build-") and home == f"{BUILD_HOMES}/{account.pw_name}":
                categories += tuple(item for item in BUILD_CACHES if needs_reclamation(
                    filesystem(f"{home}/{item[0]}"), target=target, requirements=requirements))
            paths = [f"{home}/{relative}" for relative, _, _ in categories]
            inventory = inspect_storage(home=home, paths=paths, timeout=10)
            if inventory["issues"] or len(inventory["candidates"]) != len(categories):
                raise ValueError("Cache inventory incomplete")
            total = sum(row["allocated_bytes"] for row in inventory["candidates"])
            by_path = {row["path"]: row for row in inventory["candidates"]}
            for relative, category, cost in categories:
                row = by_path[f"{home}/{relative}"]
                row.update(category=category, rebuild_cost=cost)
                record["candidates"].append(row)
                row["before"] = filesystem(row["path"])
                row["after"] = row["before"]
                if row["status"] == "absent":
                    continue
                if requirements is not None and row["before"]["mount"] not in requirements:
                    row["status"] = "outside_pressure_mounts"
                    continue
                allowed_issues = {"symlinks_skipped"} if (relative, category, cost) in BUILD_CACHES else set()
                if (row["status"] != "measured" or set(row["issues"]) - allowed_issues or row["other_owner_entries"]
                        or row["owner_uid"] != account.pw_uid or row["is_mountpoint"]
                        or row["hardlinked_files"] or not safe_cache_path(row["path"], home, account.pw_uid)):
                    row["status"] = "skipped_unsafe_or_incomplete"
                    continue
                if not force and (budget is None or total <= budget) and not needs_reclamation(
                        row["before"], target=target, requirements=requirements):
                    row["status"] = "below_limits"
                    continue
                reason = activity(account.pw_uid)
                if reason:
                    row.update(status="deferred", reason=reason)
                    continue
                row["status"] = "preview"
                if not apply:
                    continue
                identity = os.lstat(row["path"])
                # Never execute an account's NVM script as root on its behalf.
                code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                        "from lib.managed_storage import _native_cleanup; "
                        "_native_cleanup(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))")
                command = ["/usr/bin/python3", "-I", "-c", code,
                           str(Path(__file__).resolve().parent.parent), category, home,
                           str(identity.st_dev), str(identity.st_ino)]
                if account.pw_uid != os.geteuid():
                    command = ["runuser", "-u", account.pw_name, "--", *command]
                try:
                    output_tail = []
                    def capture(line):
                        output_tail.append(line[-1000:])
                        del output_tail[:-4]
                    status = run_streamed(command, cwd="/", env={"PATH": os.defpath},
                                          on_output=capture, timeout=150,
                                          pass_fds=(() if _lock_fd.get() is None else (_lock_fd.get(),)),
                                          cleanup_group_on_exit=True)
                    if status:
                        raise OSError(f"Native cleanup exit {status}: {''.join(output_tail)[-2000:]}")
                    row["status"] = "cleaned"
                except (OSError, subprocess.SubprocessError) as exc:
                    row["status"] = "failed_native_cleanup"
                    row["reason"] = str(exc)[-2500:]
                row["after"] = filesystem(row["path"])
                measured = inspect_storage(home=home, paths=[row["path"]], timeout=5)
                row["measurement_after"] = measured
                if measured["issues"] or not measured["candidates"]:
                    row["status"] = "failed_verification"
                    raise ValueError("Post-cleanup measurement unavailable")
                after = measured["candidates"][0]
                if (after["status"] not in {"measured", "absent"}
                        or set(after["issues"]) - allowed_issues or after["other_owner_entries"]):
                    row["status"] = "failed_verification"
                    raise ValueError("Post-cleanup measurement incomplete or unsafe")
                total -= max(0, row["allocated_bytes"] - after["allocated_bytes"])
        except (OSError, ValueError) as exc:
            record["error"] = str(exc)
    for record in result["accounts"]:
        for row in record["candidates"]:
            try:
                row["after"] = filesystem(row["path"])
            except (OSError, ValueError):
                record["error"] = "Final filesystem observation unavailable"
                continue
            if needs_reclamation(row["after"], target=target, requirements=requirements):
                required = (requirements or {}).get(row["after"]["mount"], {})
                result["shortfalls"].append({"path": row["path"], "mount": row["after"]["mount"],
                    "remaining_bytes": max(0, max(target, required.get("required_bytes", 0)) - row["after"]["free_bytes"]),
                    "remaining_inodes": max(0, required.get("required_inodes", 0) - row["after"].get("free_inodes", 0))
                        if row["after"].get("inodes_supported") else 0})
    return result


def _maintenance_capacity(paths: list[str], *, target: int, min_inodes: int) -> dict:
    """Observe each filesystem once for reporting and derive its cleanup target."""
    root_mount = filesystem("/")["mount"]
    rows = {}
    errors = []
    for path in dict.fromkeys(paths):
        try:
            row = filesystem(path)
        except (OSError, ValueError) as exc:
            errors.append(dict(path=path, status="unavailable", reason=str(exc)[-2000:]))
            continue
        if row["mount"] in rows:
            continue
        required_bytes, required_inodes = target, min_inodes
        # A disk-sized reserve can never be reached on a small /tmp or /run
        # tmpfs. Keep the ordinary reserve as a ceiling, with 10% headroom on
        # separate memory filesystems. Setup still enforces its workload peak.
        if row["mount"] != root_mount and row.get("filesystem_type") in {"tmpfs", "ramfs"}:
            required_bytes = min(target, row["total_bytes"] // 10)
            required_inodes = min(min_inodes, row["total_inodes"] // 10)
        remaining_bytes = max(0, required_bytes - row["free_bytes"])
        remaining_inodes = max(0, required_inodes - row["free_inodes"]) if row["inodes_supported"] else 0
        row.update(required_bytes=required_bytes, required_inodes=required_inodes,
                   remaining_bytes=remaining_bytes, remaining_inodes=remaining_inodes,
                   status="below_target" if remaining_bytes or remaining_inodes else "available")
        rows[row["mount"]] = row
    return {"requirements": rows, "filesystems": [*rows.values(), *errors],
            "shortfalls": [row for row in rows.values() if row["status"] == "below_target"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Clean eligible caches; default is preview")
    parser.add_argument("--account", action="append", default=[], help="Additional host account (repeatable)")
    parser.add_argument("--budget-mib", type=int, default=1024, help="Combined cache budget per account")
    parser.add_argument("--target-mib", type=int, default=1536, help="Free-space target including build reserve")
    parser.add_argument("--min-free-inodes", type=int, default=10000, help="Minimum free inodes; ignored where unsupported")
    parser.add_argument("--force", action="store_true", help="Select eligible caches even below budget")
    parser.add_argument("--pressure", action="store_true", help="Also reclaim archived journals, package downloads and unused build runtimes")
    parser.add_argument("--payloads", action="store_true", help="Also maintain installed builder/target terminal payloads")
    parser.add_argument("--payload-budget-mib", type=int, default=4096, help="Retained payload budget per installed role")
    parser.add_argument("--payload-min-age-hours", type=int, default=168, help="Minimum payload retention age")
    parser.add_argument("--release-builder-bundles", action="store_true", help="Explicitly end retrieval of eligible successful builder artifacts")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Host maintenance must run as root")
    if min(args.budget_mib, args.target_mib, args.min_free_inodes, args.payload_budget_mib, args.payload_min_age_hours) < 0:
        parser.error("Storage thresholds cannot be negative")
    if args.release_builder_bundles and not args.payloads:
        parser.error("Bundle release requires --payloads")
    try:
        with storage_lock(cleanup=True):
            paths = ["/", BUILD_HOMES]
            for account in accounts(args.account):
                paths.append(account.pw_dir)
                paths.extend(f"{account.pw_dir}/{relative}" for relative, _, _ in CACHES + BUILD_CACHES)
                if (args.pressure and account.pw_name.startswith("build-")
                        and account.pw_dir == f"{BUILD_HOMES}/{account.pw_name}"):
                    paths.append(f"{account.pw_dir}/.nvm/versions/node")
            pressure = []
            if args.pressure:
                from lib.storage_pressure import SYSTEM_STORAGE_PATHS, clean_system_storage, clean_build_versions

                paths.extend(SYSTEM_STORAGE_PATHS)
            capacity = _maintenance_capacity(paths, target=args.target_mib * MIB, min_inodes=args.min_free_inodes)
            requirements = capacity["requirements"]
            if args.pressure:
                pressure = clean_system_storage(target=0, apply=args.apply, requirements=requirements)
            result = maintain(names=args.account, apply=args.apply, budget=args.budget_mib * MIB,
                              target=0, force=args.force, requirements=requirements)
            if args.pressure:
                pressure.extend(clean_build_versions(target=0, apply=args.apply, requirements=requirements))
                result["pressure"] = pressure
        # Release the cache lease before dispatch. Target collection reacquires
        # storage -> maintenance -> admission -> artifact; builder collection
        # uses its existing worker -> input -> payload -> job lock order.
        if args.payloads:
            from lib.build_storage_maintenance import maintain_roles

            result["roles"] = maintain_roles(apply=args.apply, budget=args.payload_budget_mib * MIB,
                target=args.target_mib * MIB, min_age=args.payload_min_age_hours * 3600,
                release_bundles=args.release_builder_bundles)
        # Include system-only mounts and inode pressure, even when no account
        # caches exist there. Payload reclamation may have resolved a shortfall.
        capacity = _maintenance_capacity(paths, target=args.target_mib * MIB, min_inodes=args.min_free_inodes)
        result.update(filesystems=capacity["filesystems"], shortfalls=capacity["shortfalls"])
        print(json.dumps(result, indent=2))
        return int(bool(result["shortfalls"] or any(row["status"] == "unavailable" for row in result["filesystems"])
            or any(row["status"] in {"failed", "deferred_or_failed"}
            for row in result.get("pressure", [])) or any(
            row.get("status") == "deferred_or_failed" or row.get("remaining_bytes") or row.get("remaining_budget_bytes")
            for row in result.get("roles", [])) or any(
            record.get("error") or any(row["status"].startswith("failed") for row in record["candidates"])
            for record in result["accounts"])))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "deferred_or_failed", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
