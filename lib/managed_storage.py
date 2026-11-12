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
import stat
import subprocess
import sys
import tempfile

from lib.agent_storage_inventory import inspect_storage, _mounts
from lib.validation import validate_filesystem_path
from lib.validators import validate_username
from lib.streamed_process import run_streamed

LOCK = "/run/basaltwater-storage.lock"
BUILD_HOMES = "/var/lib/basaltwater/build-users"
_held = ContextVar("managed_storage_lock", default=False)
_lock_fd = ContextVar("managed_storage_lock_fd", default=None)
MIB = 1024 * 1024
CACHES = (
    (".npm/_cacache", "npm-content", "Packages must be downloaded again"),
    (".nvm/.cache", "nvm-downloads", "Node archives must be downloaded again"),
    (".cache/go-build", "go-build", "Go packages must be recompiled"),
)


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
    return {"path": path, "mount": mount, "free_bytes": usage.f_bavail * usage.f_frsize,
            "free_inodes": usage.f_favail, "inodes_supported": usage.f_files != 0}


def preflight(phase: str, paths: list[str], *, reserve_bytes: int = 512 * MIB,
              peak_bytes: int = 512 * MIB, min_inodes: int = 10000) -> list[dict]:
    """Require an estimated peak plus a reserve independently on every mount."""
    if min(reserve_bytes, peak_bytes, min_inodes) < 0:
        raise ValueError("Storage thresholds cannot be negative")
    rows = [filesystem(path) for path in dict.fromkeys(paths)]
    failures = []
    for row in rows:
        row.update(required_bytes=reserve_bytes + peak_bytes, required_inodes=min_inodes)
        if row["free_bytes"] < row["required_bytes"] or (row["inodes_supported"] and row["free_inodes"] < min_inodes):
            failures.append(row)
    if failures:
        totals = inspect_storage(paths=[BUILD_HOMES], timeout=5)["candidates"]
        raise OSError(f"Storage preflight failed during {phase}: {json.dumps(failures)}; "
                      f"managed build storage: {json.dumps(totals)}. "
                      "Preview managed storage cleanup or increase capacity/adjust peak estimates.")
    return rows


def configured_preflight(phase: str, paths: list[str]) -> list[dict]:
    """Environment overrides express estimated peak usage, not guarantees."""
    temporary = {"/tmp", os.environ.get("TMPDIR", "/tmp")}
    rows = preflight(phase, [p for p in paths if p not in temporary],
        reserve_bytes=int(os.environ.get("BASALTWATER_STORAGE_RESERVE_MIB", "512")) * MIB,
        peak_bytes=int(os.environ.get("BASALTWATER_STORAGE_PEAK_MIB", "512")) * MIB,
        min_inodes=int(os.environ.get("BASALTWATER_STORAGE_MIN_INODES", "10000")))
    rows.extend(preflight(phase, [p for p in paths if p in temporary],
        reserve_bytes=int(os.environ.get("BASALTWATER_STORAGE_TMP_RESERVE_MIB", "64")) * MIB,
        peak_bytes=int(os.environ.get("BASALTWATER_STORAGE_TMP_PEAK_MIB", "128")) * MIB,
        min_inodes=int(os.environ.get("BASALTWATER_STORAGE_MIN_INODES", "10000"))))
    return rows


@contextmanager
def operation_guard(phase: str, paths: list[str]):
    """One host-operation boundary, mocked by unrelated orchestration tests."""
    with storage_lock():
        configured_preflight(phase, paths)
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


def guarded_setup(function):
    @wraps(function)
    def wrapped(config, args):
        if args.dry_run:
            return function(config, args)
        paths = ["/opt/basaltwater", "/var/cache/apt/archives", "/var/lib/basaltwater", "/var/tmp",
                 os.environ.get("TMPDIR", "/tmp")]
        try:
            paths.append(pwd.getpwnam(config.username).pw_dir)
        except KeyError:
            paths.append(f"/home/{config.username}")
        if config.deploy_specs:
            paths.extend([BUILD_HOMES, "/var/www"])
        with operation_guard("setup", paths):
            return function(config, args)
    return wrapped


def guarded_deployment(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        paths = [self.base_dir, BUILD_HOMES, "/tmp", "/var/tmp"]
        if "staging_root" in kwargs:
            paths.append(kwargs["staging_root"])
        paths.append(os.environ.get("TMPDIR", "/tmp"))
        with operation_guard("deployment", paths):
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
    if category not in {item[1] for item in CACHES}:
        raise ValueError("Unknown cache category")
    home = pwd.getpwuid(os.geteuid()).pw_dir
    validate_filesystem_path(home, must_exist=True)
    if home != expected_home:
        raise ValueError("Maintenance account home changed")
    relative = next(relative for relative, name, _ in CACHES if name == category)
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
    if (row["status"] != "measured" or row["issues"] or row["other_owner_entries"]
            or row["hardlinked_files"] or row["is_mountpoint"]):
        raise ValueError("Worker cache inventory is unsafe or incomplete")
    env = {"HOME": home, "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
           "GOENV": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "", "GOCACHEPROG": "",
           "GOCACHE": f"{home}/.cache/go-build", "NVM_DIR": f"{home}/.nvm",
           "NPM_CONFIG_IGNORE_SCRIPTS": "true", "NPM_CONFIG_UPDATE_NOTIFIER": "false"}
    if category != "npm-content":
        _run_native(category, env)
        return
    with tempfile.TemporaryDirectory(prefix="basaltwater-cache-config-") as directory:
        for name in ("user", "global"):
            path = Path(directory) / (name + ".npmrc")
            path.write_text("")
            env["NPM_CONFIG_" + name.upper() + "CONFIG"] = str(path)
        _run_native(category, env)


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


def maintain(*, names: list[str], apply: bool, budget: int, target: int, force: bool = False) -> dict:
    """Inventory all candidates; evict only when over budget or below reserve."""
    if min(budget, target) < 0:
        raise ValueError("Storage policy cannot be negative")
    result = {"read_only": not apply, "accounts": [], "shortfalls": []}
    for account in accounts(names):
        home = account.pw_dir
        paths = [f"{home}/{relative}" for relative, _, _ in CACHES]
        record = {"account": account.pw_name, "uid": account.pw_uid, "candidates": []}
        result["accounts"].append(record)
        try:
            validate_filesystem_path(home, must_exist=True)
            inventory = inspect_storage(home=home, paths=paths, timeout=10)
            if inventory["issues"] or len(inventory["candidates"]) != len(CACHES):
                raise ValueError("Cache inventory incomplete")
            total = sum(row["allocated_bytes"] for row in inventory["candidates"])
            by_path = {row["path"]: row for row in inventory["candidates"]}
            for relative, category, cost in CACHES:
                row = by_path[f"{home}/{relative}"]
                row.update(category=category, rebuild_cost=cost)
                record["candidates"].append(row)
                row["before"] = filesystem(row["path"])
                row["after"] = row["before"]
                if row["status"] == "absent":
                    continue
                if (row["status"] != "measured" or row["issues"] or row["other_owner_entries"]
                        or row["owner_uid"] != account.pw_uid or row["is_mountpoint"]
                        or row["hardlinked_files"] or not safe_cache_path(row["path"], home, account.pw_uid)):
                    row["status"] = "skipped_unsafe_or_incomplete"
                    continue
                if not force and total <= budget and row["before"]["free_bytes"] >= target:
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
                    status = run_streamed(command, cwd="/", env={"PATH": os.defpath},
                                          on_output=lambda _line: None, timeout=150,
                                          pass_fds=(() if _lock_fd.get() is None else (_lock_fd.get(),)),
                                          cleanup_group_on_exit=True)
                    if status:
                        raise OSError("Native cleanup failed")
                    row["status"] = "cleaned"
                except (OSError, subprocess.SubprocessError):
                    row["status"] = "failed_native_cleanup"
                row["after"] = filesystem(row["path"])
                measured = inspect_storage(home=home, paths=[row["path"]], timeout=5)
                row["measurement_after"] = measured
                if measured["issues"] or not measured["candidates"]:
                    row["status"] = "failed_verification"
                    raise ValueError("Post-cleanup measurement unavailable")
                after = measured["candidates"][0]
                if after["status"] not in {"measured", "absent"} or after["issues"] or after["other_owner_entries"]:
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
            if row["after"]["free_bytes"] < target:
                result["shortfalls"].append({"path": row["path"], "mount": row["after"]["mount"],
                    "remaining_bytes": target - row["after"]["free_bytes"]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Clean eligible caches; default is preview")
    parser.add_argument("--account", action="append", default=[], help="Additional host account (repeatable)")
    parser.add_argument("--budget-mib", type=int, default=1024, help="Combined cache budget per account")
    parser.add_argument("--target-mib", type=int, default=1536, help="Free-space target including build reserve")
    parser.add_argument("--force", action="store_true", help="Select eligible caches even below budget")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Host maintenance must run as root")
    try:
        with storage_lock(cleanup=True):
            result = maintain(names=args.account, apply=args.apply, budget=args.budget_mib * MIB,
                              target=args.target_mib * MIB, force=args.force)
        print(json.dumps(result, indent=2))
        return int(bool(result["shortfalls"] or any(
            record.get("error") or any(row["status"].startswith("failed") for row in record["candidates"])
            for record in result["accounts"])))
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "deferred_or_failed", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
