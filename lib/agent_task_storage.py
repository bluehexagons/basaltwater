"""Fixed account-level storage operations outside provider command namespaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import selectors
import signal
import stat
import subprocess
import sys
import time

from lib.agent_storage_inventory import _inspection_context, _summarize_inventory, inspect_storage
from lib.cleanup_paths import remove_checked_path
from lib.streamed_process import caller_process_group
from lib.validation import validate_filesystem_path


STORAGE_OPERATIONS = ("none", "review", "npm-content-cache", "go-build-cache", "podman-images")
_OUTPUT_LIMIT = 128 * 1024


def validate_storage_operation(task: dict) -> str:
    """Accept fixed operations only; an approved prompt cannot supply shell code."""
    operation = task.get("storage_operation", "none")
    if not isinstance(operation, str) or operation not in STORAGE_OPERATIONS:
        raise ValueError("Select a supported host storage operation")
    if operation not in {"none", "review"}:
        if (task["mode"] != "workspace" or task["workspace"] != "existing"
                or task["network"] or task["temporary_files"] or task["writable_directories"]
                or task["persistence"] != "fresh" or task["interval"] != "once"
                or task["continue_until_done"] or task.get("session_history")
                or task.get("web_search", "disabled") != "disabled"):
            raise ValueError("Host cleanup requires one fresh workspace task, existing directory, and no additional access")
    return operation


def _npm_activity() -> bool:
    """Check ordinary package-manager identities without exposing arguments."""
    deadline = time.monotonic() + 3
    with os.scandir("/proc") as entries:
        for index, entry in enumerate(entries):
            if index >= 16384 or time.monotonic() >= deadline:
                raise ValueError("Process inventory incomplete")
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat(follow_symlinks=False).st_uid != os.geteuid():
                    continue
                with open(entry.path + "/comm", "rb") as source:
                    name = source.read(65).strip()
                if len(name) > 64:
                    raise ValueError("Process metadata incomplete")
                if any(name == tool or name.startswith(tool + b" ") or name.startswith(tool + b"-")
                       for tool in (b"npm", b"npx", b"pnpm", b"yarn")):
                    return True
                if name in (b"node", b"nodejs"):
                    with open(entry.path + "/cmdline", "rb") as source:
                        args = source.read(65537)
                    if len(args) > 65536 or not args.endswith(b"\0"):
                        raise ValueError("Process arguments incomplete")
                    if any(os.path.basename(arg) in (b"npm-cli.js", b"npx-cli.js", b"pnpm.cjs", b"yarn.js")
                           for arg in args.split(b"\0")):
                        return True
            except FileNotFoundError:
                continue
    return False


def npm_content_cache(home: str, *, apply: bool = False) -> dict:
    """Evict only the default downloaded content; preserve npx tools and config."""
    path = os.path.join(home, ".npm", "_cacache")
    result = {"status": "failed", "path": path, "cleanup_executed": False, "cleanup_attempted": False,
              "free_bytes_before": None, "free_bytes_after": None, "issues": [],
              "notes": ["Only default npm downloaded content is selected; custom caches, npx tools and configuration are preserved.",
                        "Package-manager activity is a snapshot; new installs can start afterwards. Future installs may download packages again."]}
    try:
        validate_filesystem_path(path)
        # Pin every home-relative ancestor and reject links/shared writes.
        for current in (home, os.path.join(home, ".npm"), path):
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError("Unsafe npm content cache")
        expected = os.lstat(path)
        report = inspect_storage(home=home, paths=[path], max_entries=50000, timeout=10)
        row = report["candidates"][0]
        if report["issues"] or row["status"] != "measured" or row["issues"] or row["other_owner_entries"]:
            raise ValueError("Npm cache inventory incomplete")
        result["before"] = {key: row[key] for key in ("allocated_bytes", "apparent_bytes", "files")}
        usage = os.statvfs(home)
        result["free_bytes_before"] = usage.f_bavail * usage.f_frsize
        if _npm_activity():
            result["status"] = "deferred_active"
        elif not apply:
            result["status"] = "preview"
        else:
            result["cleanup_attempted"] = True
            if remove_checked_path(path, expected, uid=os.geteuid(), in_use=_npm_activity):
                result.update(status="cleaned", cleanup_executed=True)
                usage = os.statvfs(home)
                result["free_bytes_after"] = usage.f_bavail * usage.f_frsize
            else:
                result.update(status="deferred_active", cleanup_attempted=False)
    except FileNotFoundError:
        result["status"] = "absent" if not result.get("before") else "uncertain" if result["cleanup_attempted"] else "failed"
    except (OSError, ValueError, KeyError, IndexError) as exc:
        result["status"] = "uncertain" if result["cleanup_attempted"] else "failed"
        if getattr(exc, "recovery_path", None):
            result["recovery_path"] = exc.recovery_path
        result["issues"].append("npm_content_cache_observation_or_cleanup_failed")
    return result


def storage_operation(operation: str, home: str) -> dict:
    """Execute on the worker's host, as its account, with fixed targets only."""
    if operation not in STORAGE_OPERATIONS[1:]:
        raise ValueError("Unsupported storage operation")
    validate_filesystem_path(home, must_exist=True)
    if os.geteuid() == 0 or os.getuid() != os.geteuid() or pwd.getpwuid(os.geteuid()).pw_dir != home:
        raise ValueError("Storage operations require the non-root account's real home")
    context = _inspection_context()
    if (context["issues"] or context["proc_self_pid_matches"] is not True
            or context["uid_map_to_parent"] != [[0, 0, 4294967295]]
            or context["gid_map_to_parent"] != [[0, 0, 4294967295]]):
        raise ValueError("Host storage observer requires an unrestricted account namespace")
    from lib.go_cache_cleanup import clean_go_build_cache
    from lib.podman_cleanup import clean_podman_images

    started = time.time()
    if operation == "review":
        data = {"inventory": _summarize_inventory(inspect_storage(home=home)),
                "npm-content-cache": npm_content_cache(home),
                "go-build-cache": clean_go_build_cache(expected_home=home),
                "podman-images": clean_podman_images()}
    elif operation == "npm-content-cache":
        data = npm_content_cache(home, apply=True)
    elif operation == "go-build-cache":
        data = clean_go_build_cache(apply=True, expected_home=home)
    else:
        data = clean_podman_images(apply=True)
    return {"operation": operation, "observer": "account_host_worker", "inspection_context": context,
            "started_at": started, "finished_at": time.time(), "result": data}


def run_storage_operation(operation: str, home: str, cancel: object, *, timeout: float,
                          deadline_at: float = 0, lease_fd: int | None = None) -> dict:
    """Bound host observations/mutations, output, cancellation and descendants."""
    if operation not in STORAGE_OPERATIONS[1:]:
        raise ValueError("Unsupported storage operation")
    if lease_fd is not None and (type(lease_fd) is not int or lease_fd < 0):
        raise ValueError("Invalid task worker lease descriptor")
    # Isolated Python ignores caller PYTHONPATH and startup hooks. Only the
    # installed package location is added; no caller command/path is accepted.
    root = str(Path(__file__).resolve().parent.parent)
    code = "import sys;sys.path.insert(0," + repr(root) + ");from lib.agent_task_storage import main;main()"
    command = [sys.executable, "-I", "-c", code, operation, home]
    environment = dict(os.environ, HOME=home, PATH=os.pathsep.join((os.path.join(home, ".local/bin"),
                       "/usr/local/go/bin", "/usr/local/bin", "/usr/bin", "/bin")))
    started = time.monotonic()
    output = bytearray()
    if cancel.is_set():
        return {"error": "Cancelled before host storage operation", "cancelled": True}
    with subprocess.Popen(command, cwd=home, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
                          pass_fds=() if lease_fd is None else (lease_fd,)) as process:
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    if (cancel.is_set() or time.monotonic() - started >= timeout
                            or deadline_at and time.time() >= deadline_at):
                        return {"error": "Host storage operation interrupted; inspect before retrying",
                                "cancelled": cancel.is_set()}
                    for key, _ in selector.select(timeout=0.25):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output) > _OUTPUT_LIMIT:
                            return {"error": "Host storage evidence exceeded its limit; inspect before retrying"}
            if process.returncode:
                return {"error": "Host storage observation or operation unavailable; inspect worker namespace and account"}
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        value = json.loads(output)
        if not isinstance(value, dict) or value.get("operation") != operation or "result" not in value:
            raise ValueError("Invalid storage result")
        return value
    except (ValueError, UnicodeError, RecursionError):
        return {"error": "Host storage evidence was invalid; inspect before retrying"}


def main() -> None:
    """Private argv entrypoint for the fixed-operation worker child."""
    try:
        if len(sys.argv) != 3:
            raise ValueError("Invalid storage invocation")
        with caller_process_group():
            result = storage_operation(sys.argv[1], sys.argv[2])
        payload = json.dumps(result, ensure_ascii=True)
        if len(payload.encode()) > _OUTPUT_LIMIT:
            raise ValueError("Storage evidence exceeds limit")
        print(payload)
    except (OSError, ValueError, KeyError):
        raise SystemExit(1) from None
