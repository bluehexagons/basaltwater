"""Credential rotation and status operations for agent VMs."""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from typing import Any, Optional

from lib.agent_credentials import (
    codex_auth_warning,
    inspect_codex_auth_file,
    inspect_codex_auth_payload,
)
from lib.ssh_utils import build_ssh_command, ssh_batch_mode, ssh_process_timeout
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


AGENT_AUTH_TOOLS = ("gh", "codex", "claude", "opencode")
_AUTH_PATHS = {
    "gh": ".config/gh/hosts.yml",
    "codex": ".codex/auth.json",
    "claude": ".claude/.credentials.json",
    "opencode": ".local/share/opencode/auth.json",
}
_PULL_FILENAMES = {
    "gh": "gh-hosts.yml",
    "codex": "codex-auth.json",
    "claude": "claude-credentials.json",
    "opencode": "opencode-auth.json",
}
_MAX_CREDENTIAL_BYTES = 4 * 1024 * 1024
_HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def _validate_git_host(host: str) -> str:
    normalized = host.strip()
    if not _HOST_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid Git host: {host}")
    return normalized


def _credential_source_path(path: str, label: str) -> str:
    from lib.setup_common import _credential_source_path as validate_source_path

    return validate_source_path(path, label)


def _active_source_path(tool: str) -> str:
    from lib.setup_common import _AGENT_AUTH_PATHS, _local_user_home

    if tool == "gh":
        return os.path.join(_local_user_home(), ".config", "gh", "hosts.yml")
    return os.path.join(_local_user_home(), _AGENT_AUTH_PATHS[tool])


def _read_credential(
    tool: str,
    source: str,
    git_host: str,
    token: Optional[str],
    *,
    use_active: bool = False,
) -> bytes:
    if tool == "gh":
        if git_host != "github.com":
            raise ValueError("GitHub CLI credentials currently support only github.com")
        from lib.setup_common import (
            _github_auth_payload_from_active,
            _github_auth_payload_from_file,
            _github_token_entry,
        )

        if token is not None:
            return _github_token_entry(token, git_host).encode("utf-8")
        if use_active:
            return _github_auth_payload_from_active(source, git_host)
        return _github_auth_payload_from_file(source, git_host)

    source_path = _credential_source_path(source, f"{tool} credential file")
    with open(source_path, "rb") as file_obj:
        payload = file_obj.read(_MAX_CREDENTIAL_BYTES + 1)
    if not payload:
        raise ValueError(f"{tool} credential file is empty: {source}")
    if len(payload) > _MAX_CREDENTIAL_BYTES:
        raise ValueError(f"{tool} credential file exceeds the size limit: {source}")
    return payload


def _remote_set_script(tool: str, git_host: str) -> str:
    path_literal = json.dumps(_AUTH_PATHS[tool])
    host_literal = json.dumps(git_host)
    return f"""
import json, os, shutil, subprocess, sys, tempfile

relative_path = {path_literal}
destination = os.path.abspath(os.path.expanduser(os.path.join('~', relative_path)))
parent = os.path.dirname(destination)

def ensure_directory(path):
    current = os.path.sep
    for component in os.path.abspath(path).split(os.path.sep):
        if not component:
            continue
        current = os.path.join(current, component)
        if os.path.lexists(current):
            if os.path.islink(current) or not os.path.isdir(current):
                raise RuntimeError('unsafe credential destination')
        else:
            os.mkdir(current, 0o700)

ensure_directory(parent)
if os.path.lexists(destination) and (
    os.path.islink(destination) or not os.path.isfile(destination)
):
    raise RuntimeError('refusing unsafe credential destination')

descriptor, temporary = tempfile.mkstemp(dir=parent, prefix='.basaltwater-auth-')
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        descriptor = -1
        payload = sys.stdin.buffer.read({ _MAX_CREDENTIAL_BYTES + 1 })
        if not payload or len(payload) > { _MAX_CREDENTIAL_BYTES }:
            raise RuntimeError('invalid credential payload')
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    if os.path.exists(temporary):
        os.unlink(temporary)

authentication = None
if {json.dumps(tool)} == 'gh':
    gh_path = shutil.which('gh')
    if gh_path:
        status = subprocess.run(
            [gh_path, 'auth', 'status', '--hostname', {host_literal}],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        authentication = status.returncode == 0
        if authentication:
            subprocess.run(
                [gh_path, 'auth', 'setup-git', '--hostname', {host_literal}],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )

print(json.dumps({{'path': destination, 'present': True, 'authentication': authentication}}))
"""


def _remote_status_script(tool: str, git_host: str) -> str:
    path_literal = json.dumps(_AUTH_PATHS[tool])
    host_literal = json.dumps(git_host)
    return f"""
import json, os, shutil, subprocess, sys, time

tool = {json.dumps(tool)}
relative_path = {path_literal}
credential_path = os.path.abspath(os.path.expanduser(os.path.join('~', relative_path)))
search_path = os.pathsep.join((os.path.expanduser('~/.local/bin'), os.path.expanduser('~/.opencode/bin'), os.environ.get('PATH', '')))
tool_path = shutil.which(tool, path=search_path)
credential = {{'path': credential_path, 'present': False}}
try:
    details = os.stat(credential_path, follow_symlinks=False)
    if os.path.islink(credential_path) or not os.path.isfile(credential_path):
        raise OSError('not a regular file')
    credential.update({{
        'present': True,
        'mode': oct(details.st_mode & 0o777),
        'owner_uid': details.st_uid,
        'age_seconds': max(0, int(time.time() - details.st_mtime)),
    }})
except OSError:
    pass

if tool == 'codex' and credential['present']:
    try:
        sys.path.insert(0, '/opt/basaltwater')
        from lib.agent_credentials import inspect_codex_auth_file
        credential['details'] = inspect_codex_auth_file(credential_path)
    except (ImportError, OSError, ValueError):
        credential['details'] = {{
            'status': 'unavailable',
            'warnings': ['metadata_unavailable'],
        }}

authentication = None
if tool == 'gh' and tool_path and credential['present']:
    result = subprocess.run(
        [tool_path, 'auth', 'status', '--hostname', {host_literal}],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    authentication = result.returncode == 0

print(json.dumps({{
    'tool': tool,
    'installed': tool_path is not None,
    'path': tool_path,
    'credential': credential,
    'authentication': authentication,
}}))
"""


def _remote_pull_script(tool: str) -> str:
    path_literal = json.dumps(_AUTH_PATHS[tool])
    return f"""
import os, stat, sys

relative_path = {path_literal}
limit = {_MAX_CREDENTIAL_BYTES}
home = os.environ.get('HOME', '')
if not os.path.isabs(home):
    raise SystemExit(4)

current = home
for component in relative_path.split('/'):
    current = os.path.join(current, component)
    try:
        details = os.lstat(current)
    except FileNotFoundError:
        raise SystemExit(3)
    except OSError:
        raise SystemExit(4)
    if stat.S_ISLNK(details.st_mode):
        raise SystemExit(4)

if (
    not stat.S_ISREG(details.st_mode)
    or details.st_uid != os.geteuid()
    or details.st_mode & 0o077
    or not 0 < details.st_size <= limit
):
    raise SystemExit(4)

descriptor = os.open(current, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
try:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or opened.st_mode & 0o077
        or opened.st_dev != details.st_dev
        or opened.st_ino != details.st_ino
        or not 0 < opened.st_size <= limit
    ):
        raise SystemExit(4)
    payload = bytearray()
    while len(payload) <= limit:
        chunk = os.read(descriptor, min(65536, limit + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    finished = os.fstat(descriptor)
finally:
    os.close(descriptor)

if (
    not payload
    or len(payload) > limit
    or len(payload) != opened.st_size
    or finished.st_size != opened.st_size
    or finished.st_mtime_ns != opened.st_mtime_ns
    or finished.st_ctime_ns != opened.st_ctime_ns
):
    raise SystemExit(4)
sys.stdout.buffer.write(bytes(payload))
"""


def _run_remote_script(
    host: str,
    username: str,
    ssh_key: Optional[str],
    script: str,
    payload: Optional[bytes] = None,
    port: Optional[int] = None,
) -> subprocess.CompletedProcess[Any]:
    batch_mode = ssh_batch_mode()
    command = build_ssh_command(
        host,
        username,
        ssh_key,
        port=port,
        batch_mode=batch_mode,
        remote_command=f"python3 -c {shlex.quote(script)}",
    )
    return subprocess.run(
        command,
        input=payload,
        check=False,
        capture_output=True,
        text=payload is None,
        timeout=ssh_process_timeout(60, batch_mode=batch_mode),
    )


def _private_output_directory(path: str) -> str:
    expanded = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(expanded, must_exist=False)
    if os.path.lexists(expanded):
        details = os.lstat(expanded)
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise ValueError(f"Output path must be a non-symlink directory: {expanded}")
        if details.st_uid != os.geteuid():
            raise ValueError(f"Output directory is not owned by the current user: {expanded}")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise ValueError(f"Output directory must have mode 0700: {expanded}")
    else:
        os.makedirs(expanded, mode=0o700)
        os.chmod(expanded, 0o700)
    return expanded


def _credential_output_directory(path: str) -> str:
    """Validate or create a canonical active-user credential directory."""
    expanded = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(expanded, must_exist=False)
    if os.path.lexists(expanded):
        details = os.lstat(expanded)
        if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise ValueError(
                f"Credential parent must be a non-symlink directory: {expanded}"
            )
        if details.st_uid != os.geteuid():
            raise ValueError(
                f"Credential parent is not owned by the current user: {expanded}"
            )
        if stat.S_IMODE(details.st_mode) & 0o022:
            raise ValueError(
                f"Credential parent must not be group- or world-writable: {expanded}"
            )
    else:
        os.makedirs(expanded, mode=0o700)
        os.chmod(expanded, 0o700)
    return expanded


def _write_pulled_credential(
    destination: str,
    payload: bytes,
    *,
    overwrite: bool,
) -> None:
    if os.path.lexists(destination):
        details = os.lstat(destination)
        if not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
            raise ValueError(f"Refusing to replace unsafe destination: {destination}")
        if details.st_uid != os.geteuid():
            raise ValueError(f"Destination is not owned by the current user: {destination}")

    descriptor, temporary = tempfile.mkstemp(
        dir=os.path.dirname(destination),
        prefix=f".{os.path.basename(destination)}.",
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as file_obj:
            descriptor = -1
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
            os.unlink(temporary)
        os.chmod(destination, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _codex_pull_should_overwrite(destination: str, payload: bytes) -> bool:
    """Return whether current pulled Codex auth may replace stale local auth."""
    if not os.path.lexists(destination):
        return False
    details = os.lstat(destination)
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        return False
    if details.st_uid != os.geteuid():
        return False

    target = inspect_codex_auth_file(destination)
    source = inspect_codex_auth_payload(payload)
    if (
        target.get("access_token_expired") is True
        and source.get("access_token_expired") is True
    ):
        raise ValueError("source and destination Codex credentials are both expired")
    return (
        target.get("status") == "refresh_required"
        and source.get("status") == "current"
        and codex_auth_warning(source) is None
    )


def _pull_destination(tool: str, output_dir: Optional[str]) -> str:
    if output_dir is None:
        return _active_source_path(tool)
    return os.path.join(output_dir, _PULL_FILENAMES[tool])


def pull_agent_credentials(
    *,
    host: str,
    username: str,
    tools: list[str],
    output_dir: Optional[str],
    ssh_key: Optional[str],
    port: int = 22,
    overwrite: bool = False,
    explicit_tools: bool = False,
) -> int:
    """Pull selected canonical auth files without displaying their contents."""
    if not validate_host(host):
        raise ValueError(f"Invalid IP address or hostname: {host}")
    if not validate_username(username):
        raise ValueError(f"Invalid username: {username}")
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    if ssh_key:
        ssh_key = os.path.abspath(os.path.expanduser(ssh_key))
        validate_filesystem_path(ssh_key, must_exist=True)
        if os.path.islink(ssh_key) or not os.path.isfile(ssh_key):
            raise ValueError(f"SSH key must be a regular, non-symlink file: {ssh_key}")

    selected = list(dict.fromkeys(tools))
    for tool in selected:
        if tool not in AGENT_AUTH_TOOLS:
            raise ValueError(f"Unsupported agent auth tool: {tool}")
    destination_dir = (
        _private_output_directory(output_dir) if output_dir is not None else None
    )
    pulled = 0
    failed = 0
    for tool in selected:
        try:
            result = _run_remote_script(
                host,
                username,
                ssh_key,
                _remote_pull_script(tool),
                payload=b"",
                port=port,
            )
        except (OSError, subprocess.TimeoutExpired):
            print(f"Error: {tool}: SSH credential transfer failed", file=sys.stderr)
            failed += 1
            break
        if result.returncode == 3:
            print(f"Skipped {tool}: credential file is not present")
            failed += int(explicit_tools)
            continue
        if result.returncode != 0:
            print(
                f"Error: {tool}: remote credential read failed "
                f"(SSH exit status {result.returncode})",
                file=sys.stderr,
            )
            failed += 1
            if result.returncode != 4:
                break
            continue
        payload = (
            bytes(result.stdout)
            if isinstance(result.stdout, (bytes, bytearray))
            else b""
        )
        if not 0 < len(payload) <= _MAX_CREDENTIAL_BYTES:
            print(f"Error: {tool}: invalid credential payload", file=sys.stderr)
            failed += 1
            continue
        destination = _pull_destination(tool, destination_dir)
        try:
            if destination_dir is None:
                _credential_output_directory(os.path.dirname(destination))
            else:
                _private_output_directory(os.path.dirname(destination))
            freshness_overwrite = (
                _codex_pull_should_overwrite(destination, payload)
                if tool == "codex"
                else False
            )
            _write_pulled_credential(
                destination,
                payload,
                overwrite=overwrite or freshness_overwrite,
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {tool}: {exc}", file=sys.stderr)
            failed += 1
            continue
        action = "Refreshed" if freshness_overwrite and not overwrite else "Pulled"
        print(f"{action} {tool} credentials to {destination}")
        pulled += 1

    if pulled == 0:
        print("Error: no credential files were pulled", file=sys.stderr)
        return 1
    return 1 if failed else 0


def set_agent_credential(
    *,
    host: str,
    username: str,
    tool: str,
    ssh_key: Optional[str],
    source: Optional[str],
    use_active: bool,
    git_host: str = "github.com",
    token: Optional[str] = None,
) -> int:
    if tool not in AGENT_AUTH_TOOLS:
        raise ValueError(f"Unsupported agent auth tool: {tool}")
    git_host = _validate_git_host(git_host)
    source_count = sum(
        value is not None
        for value in (source, "active" if use_active else None, token)
    )
    if source_count != 1:
        raise ValueError("choose exactly one credential source")
    if token is not None and tool != "gh":
        raise ValueError("interactive token entry is supported only for gh")
    selected_source = _active_source_path(tool) if use_active else source
    payload = _read_credential(
        tool,
        selected_source or "",
        git_host,
        token,
        use_active=use_active,
    )
    if tool == "codex":
        metadata = inspect_codex_auth_payload(payload)
        if metadata.get("auth_mode") == "chatgpt":
            print(
                "Note: Codex ChatGPT auth is renewable per-machine state; "
                "use a dedicated source for this VM"
            )
        warning = codex_auth_warning(metadata)
        if warning:
            print(f"Warning: source: {warning}")
    result = _run_remote_script(
        host,
        username,
        ssh_key,
        _remote_set_script(tool, git_host),
        payload,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "remote credential update failed").strip()
        print(f"Error: remote credential update failed: {detail[:500]}")
        return 1
    print(f"Updated {tool} credentials on {username}@{host} atomically")
    return 0


def get_agent_auth_status(
    *,
    host: str,
    username: str,
    tools: list[str],
    ssh_key: Optional[str],
    git_host: str = "github.com",
) -> list[dict[str, Any]]:
    git_host = _validate_git_host(git_host)
    results: list[dict[str, Any]] = []
    for tool in dict.fromkeys(tools):
        if tool not in AGENT_AUTH_TOOLS:
            raise ValueError(f"Unsupported agent auth tool: {tool}")
        result = _run_remote_script(host, username, ssh_key, _remote_status_script(tool, git_host))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "remote auth status failed").strip()
            raise RuntimeError(f"{tool}: {detail[:500]}")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{tool}: invalid remote auth status") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{tool}: invalid remote auth status")
        results.append(value)
    return results


def run_agent_auth_set(args: Any) -> int:
    source = getattr(args, "agent_auth_file", None)
    use_active = bool(getattr(args, "agent_auth_active", False))
    token: Optional[str] = None
    if getattr(args, "agent_auth_interactive", False):
        choice = input("Credential source (active/file/token): ").strip().lower()
        if choice == "active":
            use_active = True
        elif choice == "file":
            source = input("Credential file: ").strip()
        elif choice == "token" and args.agent_auth_tool == "gh":
            token = getpass.getpass("GitHub token (hidden): ").strip()
        else:
            raise ValueError("choose active, file, or token (token is gh-only)")
    return set_agent_credential(
        host=args.agent_auth_host,
        username=args.agent_auth_username,
        tool=args.agent_auth_tool,
        ssh_key=args.ssh_key,
        source=source,
        use_active=use_active,
        git_host=args.git_host,
        token=token,
    )


def run_agent_auth_pull(args: Any) -> int:
    selected = list(args.agent_auth_tools or AGENT_AUTH_TOOLS)
    return pull_agent_credentials(
        host=args.agent_auth_host,
        username=args.agent_auth_username,
        tools=selected,
        output_dir=args.output_dir,
        ssh_key=args.ssh_key,
        port=args.port,
        overwrite=args.overwrite,
        explicit_tools=bool(args.agent_auth_tools),
    )


def run_agent_auth_status(args: Any) -> int:
    results = get_agent_auth_status(
        host=args.agent_auth_host,
        username=args.agent_auth_username,
        tools=list(args.agent_auth_tools or AGENT_AUTH_TOOLS),
        ssh_key=args.ssh_key,
        git_host=args.git_host,
    )
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    print("Remote agent credential status")
    for result in results:
        credential = result.get("credential", {})
        state = "present" if credential.get("present") else "not found"
        auth = result.get("authentication")
        auth_detail = ""
        if auth is True:
            auth_detail = "; authentication passed"
        elif auth is False:
            auth_detail = "; authentication failed"
        if result.get("tool") == "codex" and isinstance(credential, dict):
            details = credential.get("details")
            if isinstance(details, dict):
                warning = codex_auth_warning(details)
                if warning:
                    auth_detail += f"; {warning}"
        print(
            f"  {'✓' if result.get('installed') else '✗'} {result.get('tool')}: "
            f"{'installed' if result.get('installed') else 'not installed'}, "
            f"credentials {state}{auth_detail}"
        )
    return 0
