"""Remote administrator operations for managed target accounts."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Optional

from lib.cache import get_cache_path_for_host, load_setup_command, rename_setup_command
from lib.ssh_utils import (
    build_scp_command,
    build_ssh_command,
    chain_remote_commands,
    ensure_remote_sudo,
    shell_join,
    ssh_batch_mode,
    ssh_process_timeout,
)
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


TARGET_HELPER = "/opt/basaltwater/lib/user_rename.py"
REMOTE_ROOT = "/var/lib/basaltwater/user-renames"
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 300.0


def _operation_id() -> str:
    return uuid.uuid4().hex


def _acquire_host_lock(host: str) -> Any:
    """Serialize controller-side rename commands for one cached host."""

    lock_path = f"{get_cache_path_for_host(host)}.user-rename.lock"
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        lock_file.close()
        if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {11, 35}:
            raise RuntimeError(f"Another user rename is already running for {host}") from exc
        raise RuntimeError(f"Could not acquire user rename lock for {host}: {exc}") from exc
    return lock_file


def _release_host_lock(lock_file: Any) -> None:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _resolve_credentials(
    host: str,
    admin_user: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, Optional[str], Any]:
    config = load_setup_command(host)
    if config is None:
        raise ValueError(f"No cached setup found for {host}")
    resolved_user = admin_user or config.username
    resolved_key = ssh_key or config.ssh_key
    if not validate_username(resolved_user):
        raise ValueError(f"Invalid administrator username: {resolved_user}")
    if resolved_key:
        validate_filesystem_path(resolved_key, must_exist=False)
    return resolved_user, resolved_key, config


def _run_ssh(
    host: str,
    username: str,
    ssh_key: Optional[str],
    command: str,
    *,
    timeout: Optional[float] = 60,
) -> subprocess.CompletedProcess[str]:
    batch_mode = ssh_batch_mode()
    return subprocess.run(
        build_ssh_command(
            host,
            username,
            ssh_key,
            batch_mode=batch_mode,
            connect_timeout=30,
            server_alive_interval=30,
            remote_command=command,
        ),
        capture_output=True,
        text=True,
        timeout=ssh_process_timeout(timeout, batch_mode=batch_mode),
        check=False,
    )


def _remote_manifest_path(operation_id: str) -> str:
    return f"{REMOTE_ROOT}/{operation_id}/manifest.json"


def _remote_status_path(operation_id: str) -> str:
    return f"{REMOTE_ROOT}/{operation_id}/status.json"


def _remote_unit_path(operation_id: str) -> str:
    return f"/etc/systemd/system/basaltwater-user-rename-{operation_id}.service"


def _discard_remote_operation(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> None:
    """Best-effort cleanup before a detached migration job is started."""

    try:
        _run_ssh(
            host,
            username,
            ssh_key,
            shell_join(["sudo", "-n", "rm", "-rf", "--", f"{REMOTE_ROOT}/{operation_id}"]),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _stage_manifest(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
    manifest: dict[str, Any],
) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="basaltwater-user-rename-",
        suffix=".json",
        delete=False,
    ) as file_obj:
        json.dump(manifest, file_obj, sort_keys=True)
        file_obj.write("\n")
        local_path = file_obj.name
    remote_tmp = f"/tmp/basaltwater-user-rename-{operation_id}.json"
    try:
        scp_result = subprocess.run(
            build_scp_command(
                host,
                username,
                local_path,
                remote_tmp,
                ssh_key,
                batch_mode=ssh_batch_mode(),
                connect_timeout=30,
            ),
            capture_output=True,
            text=True,
            timeout=ssh_process_timeout(120),
            check=False,
        )
        if scp_result.returncode != 0:
            detail = (scp_result.stderr or scp_result.stdout or "").strip()
            raise RuntimeError(f"Could not stage rename manifest: {detail[:400]}")

        destination = _remote_manifest_path(operation_id)
        command = chain_remote_commands(
            [
                ["sudo", "-n", "mkdir", "-p", f"{REMOTE_ROOT}/{operation_id}"],
                ["sudo", "-n", "chmod", "700", f"{REMOTE_ROOT}/{operation_id}"],
                [
                    "sudo",
                    "-n",
                    "install",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "600",
                    remote_tmp,
                    destination,
                ],
                ["rm", "-f", remote_tmp],
            ]
        )
        result = _run_ssh(host, username, ssh_key, command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Could not install rename manifest: {detail[:400]}")
    finally:
        try:
            os.unlink(local_path)
        except FileNotFoundError:
            pass


def _stage_unit(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> None:
    manifest_path = _remote_manifest_path(operation_id)
    unit_name = f"basaltwater-user-rename-{operation_id}.service"
    unit_content = (
        "[Unit]\n"
        "Description=basaltwater managed target user rename\n"
        "After=local-fs.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/usr/bin/python3 /opt/basaltwater/lib/user_rename.py "
        f"--manifest {manifest_path} --run\n"
        "TimeoutStartSec=infinity\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="basaltwater-user-rename-",
        suffix=".service",
        delete=False,
    ) as file_obj:
        file_obj.write(unit_content)
        local_path = file_obj.name
    remote_tmp = f"/tmp/basaltwater-user-rename-{operation_id}.service"
    try:
        scp_result = subprocess.run(
            build_scp_command(
                host,
                username,
                local_path,
                remote_tmp,
                ssh_key,
                batch_mode=ssh_batch_mode(),
                connect_timeout=30,
            ),
            capture_output=True,
            text=True,
            timeout=ssh_process_timeout(120),
            check=False,
        )
        if scp_result.returncode != 0:
            detail = (scp_result.stderr or scp_result.stdout or "").strip()
            raise RuntimeError(f"Could not stage rename unit: {detail[:400]}")
        result = _run_ssh(
            host,
            username,
            ssh_key,
            chain_remote_commands(
                [
                    [
                        "sudo",
                        "-n",
                        "install",
                        "-o",
                        "root",
                        "-g",
                        "root",
                        "-m",
                        "644",
                        remote_tmp,
                        _remote_unit_path(operation_id),
                    ],
                    ["rm", "-f", remote_tmp],
                    ["sudo", "-n", "systemctl", "daemon-reload"],
                    [
                        "sudo",
                        "-n",
                        "systemctl",
                        "enable",
                        "--now",
                        "--no-block",
                        unit_name,
                    ],
                ]
            ),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"Could not start rename job: {detail[:400]}")
    finally:
        try:
            os.unlink(local_path)
        except FileNotFoundError:
            pass


def _preflight(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> dict[str, Any]:
    command = shell_join(
        [
            "sudo",
            "-n",
            "/usr/bin/python3",
            TARGET_HELPER,
            "--manifest",
            _remote_manifest_path(operation_id),
            "--preflight",
            "--json",
        ]
    )
    result = _run_ssh(host, username, ssh_key, command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Target preflight failed: {detail[:500]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Target preflight returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(str(payload.get("error", "Target preflight failed")))
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("Target preflight did not return a manifest")
    return manifest


def _read_status(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> Optional[dict[str, Any]]:
    command = shell_join(["sudo", "-n", "cat", _remote_status_path(operation_id)])
    result = _run_ssh(host, username, ssh_key, command, timeout=30)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_manifest(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> Optional[dict[str, Any]]:
    """Read the root-owned manifest when resuming an interrupted operation."""

    command = shell_join(["sudo", "-n", "cat", _remote_manifest_path(operation_id)])
    result = _run_ssh(host, username, ssh_key, command, timeout=30)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _start_resume_unit(
    host: str,
    username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> None:
    unit_name = f"basaltwater-user-rename-{operation_id}.service"
    result = _run_ssh(
        host,
        username,
        ssh_key,
        chain_remote_commands(
            [
                ["sudo", "-n", "test", "-f", _remote_unit_path(operation_id)],
                [
                    "sudo",
                    "-n",
                    "systemctl",
                    "enable",
                    "--now",
                    "--no-block",
                    unit_name,
                ],
            ]
        ),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not resume rename job: {detail[:400]}")


def _wait_for_completion(
    host: str,
    admin_user: str,
    new_username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> Optional[dict[str, Any]]:
    users = [admin_user]
    if new_username not in users:
        users.append(new_username)
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last_phase = "starting"
    while time.monotonic() < deadline:
        for username in users:
            status = _read_status(host, username, ssh_key, operation_id)
            if status is None:
                continue
            phase = status.get("phase")
            if isinstance(phase, str) and phase != last_phase:
                print(f"  Rename {operation_id}: {phase}")
                last_phase = phase
            if status.get("status") in {"success", "failed"}:
                return status
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _verify_new_login(host: str, username: str, ssh_key: Optional[str]) -> bool:
    result = _run_ssh(host, username, ssh_key, "id -u", timeout=30)
    return result.returncode == 0 and bool(result.stdout.strip())


def _read_resume_operation(
    host: str,
    configured_admin: str,
    new_username: str,
    ssh_key: Optional[str],
    operation_id: str,
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Find an identity that can read a staged operation during recovery.

    The controller cache intentionally retains the old username until the
    migration and new-name SSH login are verified. After identity cutover,
    that cached login no longer exists, so resume must also try the new name.
    Reading either root-owned operation file proves SSH and passwordless sudo.
    """

    usernames = [configured_admin]
    if new_username not in usernames:
        usernames.append(new_username)
    for username in usernames:
        status = _read_status(host, username, ssh_key, operation_id)
        if status is not None and status.get("status") == "success":
            return username, status, None
        manifest = _read_manifest(host, username, ssh_key, operation_id)
        if status is not None or manifest is not None:
            return username, status, manifest
    raise RuntimeError(
        "Could not read the staged rename operation as either "
        f"{configured_admin} or {new_username}"
    )


def _confirm(host: str, old_username: str, new_username: str, new_home: str) -> bool:
    print(
        f"This will log out {old_username} on {host}, rename the account to "
        f"{new_username}, and use home {new_home}."
    )
    try:
        answer = input("Continue? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() == "y"


def run_user_rename(
    host: str,
    new_username: str,
    *,
    admin_user: Optional[str] = None,
    ssh_key: Optional[str] = None,
    new_home: Optional[str] = None,
    keep_home: bool = False,
    dry_run: bool = False,
    assume_yes: bool = False,
    resume: Optional[str] = None,
) -> int:
    lock_file: Optional[Any] = None
    if not validate_host(host):
        print(f"Error: Invalid IP address or hostname: {host}")
        return 1
    if not validate_username(new_username):
        print(f"Error: Invalid destination username: {new_username}")
        return 1
    if new_home:
        try:
            validate_filesystem_path(new_home, must_exist=False)
        except ValueError as exc:
            print(f"Error: invalid --new-home: {exc}")
            return 1
        if not new_home.startswith("/"):
            print("Error: --new-home must be an absolute path")
            return 1
    if keep_home and new_home:
        print("Error: --keep-home cannot be combined with --new-home")
        return 1
    if resume and not re.fullmatch(r"[0-9a-f]{16,64}", resume):
        print(f"Error: Invalid operation ID: {resume}")
        return 1
    if resume and (dry_run or new_home is not None or keep_home):
        print("Error: --resume cannot be combined with home options or --dry-run")
        return 1
    try:
        resolved_admin, resolved_key, config = _resolve_credentials(host, admin_user, ssh_key)
        lock_file = _acquire_host_lock(host)
        old_username = config.username
        if old_username == new_username:
            print("Error: destination username is already configured")
            return 1
        operation_id = resume or _operation_id()
        operation_admin = resolved_admin
        if resume:
            print(f"Resuming target user rename operation {operation_id}…")
            operation_admin, existing_status, existing_manifest = _read_resume_operation(
                host,
                resolved_admin,
                new_username,
                resolved_key,
                operation_id,
            )
            if not existing_status or existing_status.get("status") != "success":
                if existing_manifest is None:
                    raise RuntimeError("Could not read the staged rename manifest")
                if existing_manifest.get("old_username") != old_username:
                    raise RuntimeError("Rename operation belongs to a different source account")
                if existing_manifest.get("new_username") != new_username:
                    raise RuntimeError("Rename operation destination does not match the command")
                _start_resume_unit(
                    host,
                    operation_admin,
                    resolved_key,
                    operation_id,
                )
        else:
            if not ensure_remote_sudo(host, resolved_admin, resolved_key):
                return 1
            manifest: dict[str, Any] = {
                "operation_id": operation_id,
                "old_username": old_username,
                "new_username": new_username,
                "keep_home": keep_home,
            }
            if new_home:
                manifest["new_home"] = new_home
            _stage_manifest(host, resolved_admin, resolved_key, operation_id, manifest)
            try:
                details = _preflight(host, resolved_admin, resolved_key, operation_id)
            except Exception:
                _discard_remote_operation(
                    host,
                    resolved_admin,
                    resolved_key,
                    operation_id,
                )
                raise
            if not isinstance(details.get("old_home"), str) or not isinstance(details.get("new_home"), str):
                raise RuntimeError("Target preflight did not return home paths")
            if dry_run:
                print(json.dumps(details, indent=2, sort_keys=True))
                _discard_remote_operation(
                    host,
                    resolved_admin,
                    resolved_key,
                    operation_id,
                )
                return 0
            if not assume_yes and not _confirm(host, old_username, new_username, details["new_home"]):
                _discard_remote_operation(
                    host,
                    resolved_admin,
                    resolved_key,
                    operation_id,
                )
                print("Aborted.")
                return 0
            _stage_unit(host, resolved_admin, resolved_key, operation_id)

        status = _wait_for_completion(
            host,
            operation_admin,
            new_username,
            resolved_key,
            operation_id,
        )
        if status is None:
            print(
                f"Rename operation {operation_id} is still in progress or cannot be reached. "
                f"Retry with --resume {operation_id}."
            )
            return 1
        if status.get("status") != "success":
            print(
                f"Rename operation {operation_id} failed during "
                f"{status.get('phase', 'unknown')}: {status.get('error', 'unknown error')}",
                file=sys.stderr,
            )
            print(f"Retry with --resume {operation_id} after reviewing the target.", file=sys.stderr)
            return 1
        if not _verify_new_login(host, new_username, resolved_key):
            print(
                f"Remote rename completed, but SSH verification as {new_username} failed. "
                f"Operation ID: {operation_id}",
                file=sys.stderr,
            )
            return 1
        manifest_status = status
        if not isinstance(manifest_status.get("details"), dict):
            manifest_status = _read_status(host, operation_admin, resolved_key, operation_id)
        if (
            manifest_status is None
            or not isinstance(manifest_status.get("details"), dict)
        ) and operation_admin != new_username:
            manifest_status = _read_status(host, new_username, resolved_key, operation_id)
        if manifest_status is None or not isinstance(manifest_status.get("details"), dict):
            raise RuntimeError("Successful rename status did not include completion details")
        details = manifest_status["details"]
        if details.get("old_username") != old_username:
            raise RuntimeError("Rename status belongs to a different source account")
        if details.get("username") != new_username:
            raise RuntimeError("Rename status belongs to a different destination account")
        old_home = details.get("old_home")
        completed_home = details.get("home")
        if not isinstance(old_home, str) or not old_home.startswith("/"):
            raise RuntimeError("Successful rename status did not include the old home")
        if not isinstance(completed_home, str) or not completed_home.startswith("/"):
            raise RuntimeError("Successful rename status did not include the new home")
        rename_setup_command(
            host,
            old_username=old_username,
            new_username=new_username,
            old_home=old_home,
            new_home=completed_home,
        )
        print(f"✓ Renamed {old_username} to {new_username} on {host}")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock_file is not None:
            _release_host_lock(lock_file)
