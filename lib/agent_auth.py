"""Credential rotation and status operations for agent VMs."""

from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import subprocess
from typing import Any, Optional

from lib.agent_credentials import (
    codex_auth_warning,
    inspect_codex_auth_payload,
)
from lib.ssh_utils import build_ssh_command, ssh_batch_mode, ssh_process_timeout


AGENT_AUTH_TOOLS = ("gh", "codex", "claude", "opencode")
_AUTH_PATHS = {
    "gh": ".config/gh/hosts.yml",
    "codex": ".codex/auth.json",
    "claude": ".claude/.credentials.json",
    "opencode": ".local/share/opencode/auth.json",
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
