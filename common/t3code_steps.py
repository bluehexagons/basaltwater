"""T3 Code server and web-interface setup steps."""

from __future__ import annotations

import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.error
from contextlib import contextmanager
from typing import Iterator

from common.agent_steps import (
    BASE_AGENT_SKILL_NAMES,
    BROWSER_AGENT_SKILL_NAMES,
    agent_workflow_skill_names,
    install_managed_agent_skills,
)
from common.common_steps import _run_as_login_user
from lib.atomic_io import write_text_atomic
from lib.auth_failure_bans import (
    configure_nginx_auth_failure_ban,
    remove_nginx_auth_failure_ban,
)
from lib.config import SetupConfig
from lib.local_http import open_loopback
from lib.remote_utils import install_package, is_dry_run, run
from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr
from lib.validators import validate_username


T3_SERVICE_NAME = "t3code"
LEGACY_T3_SERVICE_NAME = "infra-tools-t3code"
LEGACY_T3_SERVICE_FILE = f"/etc/systemd/system/{LEGACY_T3_SERVICE_NAME}.service"
T3_CONNECT_RESTART_PATH_UNIT = "infra-tools-t3code-connect.path"
T3_CONNECT_RESTART_SERVICE_UNIT = "infra-tools-t3code-connect.service"
T3_CONNECT_RESTART_PATH_FILE = (
    f"/etc/systemd/system/{T3_CONNECT_RESTART_PATH_UNIT}"
)
T3_CONNECT_RESTART_SERVICE_FILE = (
    f"/etc/systemd/system/{T3_CONNECT_RESTART_SERVICE_UNIT}"
)
T3_UFW_RULE_COMMENT_PREFIX = "infra_tools T3 Code"
DEVICE_PAIRING_SERVICE_NAME = "infra-tools-device-pairing"
DEVICE_PAIRING_SERVICE_FILE = (
    f"/etc/systemd/system/{DEVICE_PAIRING_SERVICE_NAME}.service"
)
DEVICE_PAIRING_CONFIG_DIR = "/etc/infra-tools/device-pairing"
DEVICE_PAIRING_AUTH_FILE = f"{DEVICE_PAIRING_CONFIG_DIR}/htpasswd"
DEVICE_PAIRING_PROVIDERS_FILE = f"{DEVICE_PAIRING_CONFIG_DIR}/providers.json"
DEVICE_PAIRING_PAYLOAD_FILE = "/opt/infra_tools/device_pairing_payload/htpasswd"
DEVICE_PAIRING_SOCKET = "/run/infra-tools-device-pairing/http.sock"
DEVICE_PAIRING_SCRIPT = (
    "/opt/infra_tools/common/service_tools/device_pairing_service.py"
)
T3_ADMIN_PAIR_SCRIPT = (
    "/opt/infra_tools/common/service_tools/t3code_admin_pair.py"
)
T3_AGENT_SKILLS_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "agent_skills"
)
T3_CAPABILITY_AGENT_SKILL_NAMES = (
    "infra-tools-t3code",
    "infra-tools-web-gateway",
)
T3_BROWSER_AGENT_SKILL_NAMES = (
    "infra-tools-browser-testing",
    "infra-tools-t3-preview-testing",
)
T3_AGENT_SKILL_NAMES = (
    *BASE_AGENT_SKILL_NAMES,
    *T3_CAPABILITY_AGENT_SKILL_NAMES,
)
DEVICE_PAIRING_NGINX_SITE = "/etc/nginx/sites-available/infra-tools-device-pairing"
DEVICE_PAIRING_NGINX_LINK = "/etc/nginx/sites-enabled/infra-tools-device-pairing"
DEVICE_PAIRING_AUTH_FAILURE_LOG = (
    "/var/log/nginx/infra-tools-device-pairing-auth-failures.log"
)
_UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(.*)$")
_T3_RUNTIME_RELATIVE_PATH = (".t3", "runtime")
_T3_SEMVER_NUMBER = r"(?:0|[1-9][0-9]*)"
_T3_SEMVER_PRERELEASE = (
    rf"(?:{_T3_SEMVER_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
_T3_VERSION_RE = re.compile(
    rf"^{_T3_SEMVER_NUMBER}\.{_T3_SEMVER_NUMBER}\.{_T3_SEMVER_NUMBER}"
    rf"(?:-{_T3_SEMVER_PRERELEASE}(?:\.{_T3_SEMVER_PRERELEASE})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_T3_GH_CONFIG_EXPORT = 'export GH_CONFIG_DIR="$HOME/.config/gh"'
_T3_NATIVE_PACKAGES = ("node-pty", "msgpackr-extract")
_T3_UPDATE_INNER_COMMAND = (
    "/usr/bin/env "
    "-u npm_config_allow_scripts "
    "-u NPM_CONFIG_ALLOW_SCRIPTS "
    "-u npm_config_dangerously_allow_all_scripts "
    "-u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS "
    "t3 service update"
)
_T3_READINESS_ATTEMPTS = 20
_T3_READINESS_STABLE_CHECKS = 3
_T3_COMMAND_OUTPUT_EXCERPT_LIMIT = 1200


def _user_home(config: SetupConfig) -> str:
    try:
        return pwd.getpwnam(config.username).pw_dir
    except KeyError as exc:
        raise RuntimeError(f"Target user does not exist: {config.username}") from exc


def _workspace(config: SetupConfig, home: str) -> str:
    path = config.agent_workspace or os.path.join(home, "repos")
    validate_filesystem_path(path, must_exist=False)
    return path


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _ufw_numbered_rules() -> list[tuple[int, str, str]]:
    """Return numbered UFW rules as ``(number, comment, line)`` records."""

    result = run("ufw status numbered", check=False, capture_output=True)
    if result.returncode != 0 or not isinstance(result.stdout, str):
        raise RuntimeError("Could not inspect UFW rules for T3 Code")
    rules: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        match = _UFW_NUMBERED_RULE_RE.match(line.strip())
        if not match:
            continue
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        rules.append((int(match.group(1)), comment, line))
    return rules


def _remove_managed_rules(
    rules: list[tuple[int, str, str]],
    desired_comments: set[str],
) -> None:
    stale_numbers = [
        number
        for number, comment, _line in rules
        if comment.startswith(T3_UFW_RULE_COMMENT_PREFIX)
        and comment not in desired_comments
    ]
    for number in sorted(stale_numbers, reverse=True):
        result = run(
            f"ufw --force delete {number}",
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                "Could not remove a stale T3 Code firewall rule"
                + (f": {detail}" if detail else "")
            )


def _configure_firewall(config: SetupConfig, port: int, host: str) -> None:
    sources = [
        validate_network_ip_or_cidr(source, "T3 Code web source")
        for source in config.effective_web_interface_sources()
    ]

    active = run(
        "ufw status 2>/dev/null | grep -q 'Status: active'",
        check=False,
    ).returncode == 0
    if _is_loopback(host):
        if active:
            _remove_managed_rules(_ufw_numbered_rules(), set())
        return
    if not sources:
        raise RuntimeError(
            "A non-loopback T3 Code web bind requires --access-source or "
            "--web-interface-source"
        )
    if not active:
        raise RuntimeError(
            "T3 Code web access outside loopback requires an active UFW firewall"
        )

    existing_rules = _ufw_numbered_rules()
    existing_managed_comments = {
        comment
        for _number, comment, _line in existing_rules
        if comment.startswith(T3_UFW_RULE_COMMENT_PREFIX)
    }
    exposed_ports = [(port, "")]
    if config.device_pairing_providers:
        exposed_ports.append((config.device_pairing_port, " pairing"))
    conflicting = []
    for exposed_port, _suffix in exposed_ports:
        conflicting.extend(
            line
            for _number, comment, line in existing_rules
            if f"{exposed_port}/tcp" in line
            and "ALLOW IN" in line
            and comment not in existing_managed_comments
        )
    if conflicting:
        port_list = ", ".join(str(item[0]) for item in exposed_ports)
        raise RuntimeError(
            f"Unmanaged UFW allow rules already expose managed port(s) {port_list}; "
            "remove them before using --web-interface-source"
        )

    desired_comments: set[str] = set()
    for source in sources:
        for exposed_port, suffix in exposed_ports:
            comment = (
                f"{T3_UFW_RULE_COMMENT_PREFIX}{suffix} "
                f"{exposed_port}/tcp source {source}"
            )
            desired_comments.add(comment)
            if comment in existing_managed_comments:
                continue
            result = run(
                "ufw allow from "
                f"{shlex.quote(source)} to any port {exposed_port} proto tcp "
                f"comment {shlex.quote(comment)}",
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(
                    f"Could not install T3 Code firewall rule for {source}"
                    + (f": {detail}" if detail else "")
                )
    updated_rules = _ufw_numbered_rules()
    observed_comments = {comment for _number, comment, _line in updated_rules}
    missing = desired_comments - observed_comments
    if missing:
        raise RuntimeError(
            "UFW did not retain all requested T3 Code source rules: "
            + ", ".join(sorted(missing))
        )
    _remove_managed_rules(updated_rules, desired_comments)


def _t3_runtime_path(home: str) -> str:
    return os.path.join(home, *_T3_RUNTIME_RELATIVE_PATH)


def _t3_service_file(home: str) -> str:
    return os.path.join(home, ".config", "systemd", "user", "t3code.service")


def _t3_service_drop_in(home: str) -> str:
    return os.path.join(
        home,
        ".config",
        "systemd",
        "user",
        "t3code.service.d",
        "infra-tools.conf",
    )


def _active_t3_binary(home: str) -> str | None:
    """Resolve the immutable executable selected by T3's service manager."""

    state_file = os.path.join(_t3_runtime_path(home), "service-state.json")
    if os.path.islink(state_file) or not os.path.isfile(state_file):
        return None
    try:
        with open(state_file, encoding="utf-8") as file_obj:
            state = json.load(file_obj)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("protocol") != 2:
        return None
    version = state.get("activeVersion")
    if not isinstance(version, str) or _T3_VERSION_RE.fullmatch(version) is None:
        return None
    binary = os.path.join(
        _t3_runtime_path(home),
        "versions",
        version,
        "node_modules",
        "t3",
        "dist",
        "bin.mjs",
    )
    return binary if os.path.isfile(binary) and os.access(binary, os.X_OK) else None


def _retained_failed_t3_binary(home: str) -> tuple[str, str] | None:
    """Return a failed immutable update candidate retained by T3's launcher."""

    state_file = os.path.join(_t3_runtime_path(home), "service-state.json")
    if os.path.islink(state_file) or not os.path.isfile(state_file):
        return None
    try:
        with open(state_file, encoding="utf-8") as file_obj:
            state = json.load(file_obj)
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("protocol") != 2:
        return None
    update = state.get("update")
    if not isinstance(update, dict) or update.get("status") not in {
        "failed",
        "rolled-back",
    }:
        return None
    active_version = state.get("activeVersion")
    target_version = update.get("targetVersion")
    if (
        not isinstance(active_version, str)
        or _T3_VERSION_RE.fullmatch(active_version) is None
        or not isinstance(target_version, str)
        or _T3_VERSION_RE.fullmatch(target_version) is None
        or target_version == active_version
    ):
        return None
    binary = os.path.join(
        _t3_runtime_path(home),
        "versions",
        target_version,
        "node_modules",
        "t3",
        "dist",
        "bin.mjs",
    )
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        return None
    return target_version, binary


def _t3_version_root(binary: str) -> str:
    """Return the immutable version root containing an active T3 executable."""

    version_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(binary)))
    )
    validate_filesystem_path(version_root, must_exist=True)
    expected = os.path.join(
        version_root,
        "node_modules",
        "t3",
        "dist",
        "bin.mjs",
    )
    if os.path.normpath(binary) != os.path.normpath(expected):
        raise RuntimeError(f"Invalid T3 Code runtime path: {binary}")
    return version_root


def _t3_native_runtime_healthy(
    username: str,
    home: str,
    node_bin: str,
    binary: str,
) -> bool:
    """Load T3's required native terminal module with its service Node.js."""

    node = os.path.join(node_bin, "node")
    node_pty = os.path.join(_t3_version_root(binary), "node_modules", "node-pty")
    if not os.path.isfile(node) or not os.access(node, os.X_OK):
        return False
    result = _run_as_login_user(
        username,
        home,
        f"{shlex.quote(node)} -e "
        f"{shlex.quote('require(process.argv[1])')} {shlex.quote(node_pty)}",
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _t3_command_failure_detail(
    result: subprocess.CompletedProcess[str],
) -> str:
    """Return bounded diagnostics without discarding the start of command output."""

    def excerpt(value: str | None) -> str:
        text = (value or "").strip()
        if len(text) <= _T3_COMMAND_OUTPUT_EXCERPT_LIMIT:
            return text
        head_length = _T3_COMMAND_OUTPUT_EXCERPT_LIMIT // 2
        tail_length = _T3_COMMAND_OUTPUT_EXCERPT_LIMIT - head_length
        return (
            f"{text[:head_length].rstrip()}\n"
            "... output omitted ...\n"
            f"{text[-tail_length:].lstrip()}"
        )

    parts = [f"exit code {result.returncode}"]
    for label, output in (("stderr", result.stderr), ("stdout", result.stdout)):
        bounded = excerpt(output)
        if bounded:
            parts.append(f"{label}:\n{bounded}")
    return "\n".join(parts)


def _rebuild_t3_native_runtime(
    username: str,
    home: str,
    node_bin: str,
    binary: str,
) -> None:
    """Rebuild native dependencies omitted by npm's lifecycle-script policy."""

    npm = os.path.join(node_bin, "npm")
    if not os.path.isfile(npm) or not os.access(npm, os.X_OK):
        raise RuntimeError("T3 Code native runtime repair requires npm")
    version_root = _t3_version_root(binary)
    packages = " ".join(shlex.quote(package) for package in _T3_NATIVE_PACKAGES)
    with _temporary_t3_npm_config(home) as npm_config:
        result = _run_as_login_user(
            username,
            home,
            "unset npm_config_allow_scripts NPM_CONFIG_ALLOW_SCRIPTS "
            "npm_config_dangerously_allow_all_scripts "
            "NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS && "
            "export CC=gcc && "
            "export CXX=g++ && "
            f"export NPM_CONFIG_USERCONFIG={shlex.quote(npm_config)} && "
            "export npm_config_foreground_scripts=true && "
            f"{shlex.quote(npm)} rebuild --foreground-scripts "
            f"--prefix {shlex.quote(version_root)} {packages}",
            check=False,
            capture_output=True,
        )
    if result.returncode != 0:
        detail = _t3_command_failure_detail(result)
        raise RuntimeError(f"T3 Code native runtime repair failed:\n{detail}")
    if not _t3_native_runtime_healthy(username, home, node_bin, binary):
        raise RuntimeError(
            "T3 Code native runtime is incomplete after repair; node-pty did not load"
        )


@contextmanager
def _temporary_t3_npm_config(home: str) -> Iterator[str]:
    """Yield a target-readable npm 12 allowlist for one native rebuild."""

    if os.path.islink(home) or not os.path.isdir(home):
        raise RuntimeError(f"Refusing unsafe T3 home directory: {home}")
    owner = os.stat(home, follow_symlinks=False)
    descriptor, path = tempfile.mkstemp(
        prefix=".infra-tools-t3-npmrc-",
        dir=home,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(f"allow-scripts={','.join(_T3_NATIVE_PACKAGES)}\n")
        os.chmod(path, 0o600)
        os.chown(path, owner.st_uid, owner.st_gid)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@contextmanager
def _temporary_t3_loginctl_shim(home: str, username: str) -> Iterator[str]:
    """Resolve T3's no-argument linger request in a sessionless setup shell."""

    if not validate_username(username):
        raise ValueError(f"Invalid T3 Code linger username: {username}")
    if os.path.islink(home) or not os.path.isdir(home):
        raise RuntimeError(f"Refusing unsafe T3 home directory: {home}")
    real_loginctl = shutil.which("loginctl")
    if real_loginctl is None or not os.path.isabs(real_loginctl):
        raise RuntimeError("T3 Code service setup requires loginctl")
    validate_filesystem_path(real_loginctl, must_exist=True)
    directory = tempfile.mkdtemp(
        prefix=".infra-tools-t3-loginctl-",
        dir=home,
    )
    shim = os.path.join(directory, "loginctl")
    try:
        os.chmod(directory, 0o755)
        descriptor = os.open(
            shim,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o755,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(
                "#!/bin/sh\n"
                'if [ "$#" -eq 1 ] && [ "$1" = "enable-linger" ]; then\n'
                f'  if [ "$({shlex.quote(real_loginctl)} show-user '
                f'{shlex.quote(username)} --property=Linger --value)" '
                '= "yes" ]; then\n'
                "    exit 0\n"
                "  fi\n"
                f"  exec {shlex.quote(real_loginctl)} enable-linger "
                f"{shlex.quote(username)}\n"
                "fi\n"
                f'exec {shlex.quote(real_loginctl)} "$@"\n'
            )
        os.chmod(shim, 0o755)
        yield directory
    finally:
        try:
            os.unlink(shim)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(directory)
        except FileNotFoundError:
            pass


def _t3_local_endpoint_reachable(host: str, port: int) -> bool:
    if host == "0.0.0.0":
        local_host = "127.0.0.1"
    elif host in {"::", "[::]"}:
        local_host = "::1"
    else:
        local_host = host
    try:
        with open_loopback(
            f"http://{_url_host(local_host)}:{port}/",
            timeout=2,
        ) as response:
            return response.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False


def _wait_for_t3_service(
    username: str,
    uid: int,
    host: str,
    port: int,
    home: str,
) -> None:
    """Require the user service and endpoint to remain ready across checks."""

    stable_checks = 0
    for attempt in range(_T3_READINESS_ATTEMPTS):
        service = _user_systemctl(
            username,
            uid,
            "is-active",
            "--quiet",
            T3_SERVICE_NAME,
        )
        if service.returncode == 0 and _t3_local_endpoint_reachable(host, port):
            stable_checks += 1
            if stable_checks >= _T3_READINESS_STABLE_CHECKS:
                return
        else:
            stable_checks = 0
        if attempt + 1 < _T3_READINESS_ATTEMPTS:
            time.sleep(1)
    service_log = os.path.join(home, ".t3", "userdata", "logs", "boot-service.log")
    raise RuntimeError(
        "T3 Code user service did not reach stable readiness; inspect "
        f"{service_log} and 'journalctl --user -u {T3_SERVICE_NAME}.service'"
    )


def _user_systemctl(
    username: str,
    uid: int,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = [
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
    ]
    return run(
        ["runuser", "-u", username, "--", *environment, "systemctl", "--user", *arguments],
        check=False,
        capture_output=True,
    )


def _ensure_user_manager(username: str, uid: int) -> None:
    linger = run(["loginctl", "enable-linger", username], check=False, capture_output=True)
    if linger.returncode != 0:
        detail = (linger.stderr or linger.stdout or "").strip()
        raise RuntimeError(f"Could not enable the T3 Code user service: {detail}")
    manager = run(
        ["systemctl", "start", f"user@{uid}.service"],
        check=False,
        capture_output=True,
    )
    if manager.returncode != 0:
        detail = (manager.stderr or manager.stdout or "").strip()
        raise RuntimeError(f"Could not start the T3 Code user manager: {detail}")


def _remove_legacy_shell_path(home: str, uid: int, gid: int) -> bool:
    bashrc = os.path.join(home, ".bashrc")
    if not os.path.isfile(bashrc) or os.path.islink(bashrc):
        return False
    with open(bashrc, encoding="utf-8") as file_obj:
        existing = file_obj.read()
    marker = (
        "# infra-tools T3 Code runtime\n"
        'export PATH="$HOME/.local/share/infra-tools/t3code/'
        'node_modules/.bin:$PATH"\n'
    )
    if marker not in existing:
        return False
    write_text_atomic(bashrc, existing.replace(marker, ""), mode=0o644)
    os.chown(bashrc, uid, gid)
    return True


def _node_bin_directory(username: str, home: str) -> str:
    result = _run_as_login_user(
        username,
        home,
        'export NVM_DIR="$HOME/.nvm" && '
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && '
        'dirname "$(command -v node)"',
        check=False,
        capture_output=True,
    )
    path = (result.stdout or "").strip()
    if result.returncode != 0 or not os.path.isabs(path):
        raise RuntimeError("T3 Code requires Node.js in the target-user login environment")
    validate_filesystem_path(path, must_exist=True)
    return path


def _install_t3_npm_shim(
    home: str,
    node_bin: str,
    uid: int,
    gid: int,
) -> tuple[str, bool]:
    """Allow only T3's native scripts during immutable runtime staging."""

    if os.path.islink(home) or not os.path.isdir(home):
        raise RuntimeError(f"Refusing unsafe T3 home directory: {home}")
    real_npm = os.path.join(node_bin, "npm")
    if not os.path.isfile(real_npm) or not os.access(real_npm, os.X_OK):
        raise RuntimeError("T3 Code service updates require npm")
    validate_filesystem_path(real_npm, must_exist=True)
    current = home
    for component in (".local", "share", "infra-tools", "t3-npm", "bin"):
        current = os.path.join(current, component)
        if os.path.lexists(current):
            if os.path.islink(current) or not os.path.isdir(current):
                raise RuntimeError(f"Refusing unsafe T3 npm shim directory: {current}")
        else:
            os.mkdir(current, mode=0o755)
        os.chown(current, uid, gid)

    runtime_versions = os.path.join(_t3_runtime_path(home), "versions")
    wrapper = os.path.join(current, "npm")
    content = f"""#!/bin/bash
set -eu
real_npm={shlex.quote(real_npm)}
runtime_versions={shlex.quote(runtime_versions)}
prefix=
package=
expect_prefix=false
for argument in "$@"; do
  if "$expect_prefix"; then
    prefix=$argument
    expect_prefix=false
    continue
  fi
  case "$argument" in
    --prefix) expect_prefix=true ;;
    --prefix=*) prefix=${{argument#--prefix=}} ;;
    t3@*) package=$argument ;;
  esac
done
staging=
case "$prefix" in
  "$runtime_versions"/.staging-*) staging=${{prefix##*/}} ;;
esac
if [ "${{1-}}" = install ] \
  && [[ "$staging" =~ ^\\.staging-[0-9A-Za-z_-]+$ ]] \
  && [[ "$package" =~ ^t3@(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)([-+][0-9A-Za-z.-]+)?$ ]]; then
  if [ -L "$prefix" ] || [ ! -d "$prefix" ]; then
    echo "Refusing unsafe T3 npm staging directory: $prefix" >&2
    exit 1
  fi
  npm_config="$prefix/.npmrc"
  if [ -e "$npm_config" ] || [ -L "$npm_config" ]; then
    echo "Refusing existing T3 npm staging policy: $npm_config" >&2
    exit 1
  fi
  umask 077
  set -o noclobber
  printf '%s\\n' \
    'allow-scripts={','.join(_T3_NATIVE_PACKAGES)}' \
    'dangerously-allow-all-scripts=false' \
    'ignore-scripts=false' > "$npm_config"
  set +o noclobber
  status=0
  /usr/bin/env \
    -u npm_config_allow_scripts \
    -u NPM_CONFIG_ALLOW_SCRIPTS \
    -u npm_config_dangerously_allow_all_scripts \
    -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
    -u npm_config_ignore_scripts \
    -u NPM_CONFIG_IGNORE_SCRIPTS \
    "$real_npm" "$@" || status=$?
  rm -- "$npm_config"
  exit "$status"
fi
exec "$real_npm" "$@"
"""
    changed = _write_executable_if_changed(wrapper, content)
    os.chown(wrapper, uid, gid)
    return current, changed


def _configure_t3_service_drop_in(
    home: str,
    workspace: str,
    host: str,
    port: int,
    node_bin: str,
    npm_shim_bin: str,
    uid: int,
    gid: int,
) -> bool:
    path = _t3_service_drop_in(home)
    parent = os.path.dirname(path)
    current = home
    for component in (".config", "systemd", "user", "t3code.service.d"):
        current = os.path.join(current, component)
        if os.path.lexists(current):
            if os.path.islink(current) or not os.path.isdir(current):
                raise RuntimeError(
                    f"Refusing unsafe T3 service configuration: {current}"
                )
        else:
            os.mkdir(current, mode=0o755)
        os.chown(current, uid, gid)
    environment_path = ":".join(
        (
            npm_shim_bin,
            node_bin,
            os.path.join(home, ".opencode", "bin"),
            os.path.join(home, ".local", "bin"),
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        )
    )
    content = f"""# Managed by infra_tools
[Service]
WorkingDirectory={workspace}
Environment=T3CODE_HOST={host}
Environment=T3CODE_PORT={port}
Environment=T3CODE_NO_BROWSER=true
Environment=GH_CONFIG_DIR={home}/.config/gh
Environment=CC=gcc
Environment=CXX=g++
Environment=npm_config_strict_allow_scripts=false
UnsetEnvironment=npm_config_allow_scripts NPM_CONFIG_ALLOW_SCRIPTS npm_config_dangerously_allow_all_scripts NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS
Environment=PATH={environment_path}
"""
    changed = _write_text_if_changed(path, content, 0o644)
    os.chown(parent, uid, gid)
    os.chown(path, uid, gid)
    return changed


def _install_t3_service(
    home: str,
    username: str,
    uid: int,
    gid: int,
    workspace: str,
    host: str,
    port: int,
    *,
    refresh: bool = False,
) -> str:
    """Install or update T3 through its supported per-user service manager."""

    for name, package in (
        ("T3 Code native build tools", "build-essential"),
        ("T3 Code Python build support", "python3"),
    ):
        if not install_package(name, package, f"apt-get install -y -qq {package}"):
            raise RuntimeError(f"Could not install {package}, required by T3 Code")

    _ensure_user_manager(username, uid)
    node_bin = _node_bin_directory(username, home)
    npm_shim_bin, npm_shim_changed = _install_t3_npm_shim(
        home,
        node_bin,
        uid,
        gid,
    )
    drop_in_changed = _configure_t3_service_drop_in(
        home,
        workspace,
        host,
        port,
        node_bin,
        npm_shim_bin,
        uid,
        gid,
    )
    service_file = _t3_service_file(home)
    binary = _active_t3_binary(home)
    previous_binary = binary
    service_was_present = os.path.isfile(service_file)
    update_needed = refresh or binary is None or not os.path.isfile(service_file)
    update_failed = False

    legacy_present = os.path.lexists(LEGACY_T3_SERVICE_FILE)
    if legacy_present and (
        os.path.islink(LEGACY_T3_SERVICE_FILE)
        or not os.path.isfile(LEGACY_T3_SERVICE_FILE)
    ):
        raise RuntimeError(
            f"Refusing unsafe legacy service file: {LEGACY_T3_SERVICE_FILE}"
        )
    if legacy_present:
        run(
            ["systemctl", "stop", f"{LEGACY_T3_SERVICE_NAME}.service"],
            check=False,
            capture_output=True,
        )

    try:
        retained_candidate = _retained_failed_t3_binary(home)
        if retained_candidate is not None:
            candidate_version, candidate_binary = retained_candidate
            if not _t3_native_runtime_healthy(
                username,
                home,
                node_bin,
                candidate_binary,
            ):
                _rebuild_t3_native_runtime(
                    username,
                    home,
                    node_bin,
                    candidate_binary,
                )
                print(
                    "  ✓ Repaired retained T3 Code "
                    f"v{candidate_version} update candidate"
                )
        if update_needed:
            with _temporary_t3_loginctl_shim(home, username) as shim_path:
                result = _run_as_login_user(
                    username,
                    home,
                    'export NVM_DIR="$HOME/.nvm" && '
                    '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && '
                    f'export PATH={shlex.quote(shim_path)}:'
                    f'{shlex.quote(npm_shim_bin)}:"$PATH" && '
                    'unset npm_config_dangerously_allow_all_scripts '
                    'NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS '
                    'npm_config_allow_scripts NPM_CONFIG_ALLOW_SCRIPTS && '
                    'export CC=gcc && '
                    'export CXX=g++ && '
                    'export npm_config_strict_allow_scripts=false && '
                    'export npm_config_foreground_scripts=true && '
                    f'export XDG_RUNTIME_DIR=/run/user/{uid} && '
                    f'export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus && '
                    'npx --yes --package=t3@latest -c '
                    f'{shlex.quote(_T3_UPDATE_INNER_COMMAND)}',
                    check=False,
                    capture_output=True,
                )
            if result.returncode != 0:
                detail = _t3_command_failure_detail(result)
                binary = _active_t3_binary(home)
                can_retain_runtime = (
                    refresh
                    and previous_binary is not None
                    and service_was_present
                    and binary is not None
                    and os.path.isfile(service_file)
                )
                if not can_retain_runtime:
                    raise RuntimeError(f"T3 Code service update failed:\n{detail}")
                update_failed = True
                print(
                    "  ⚠ T3 Code updater failed "
                    f"(exit code {result.returncode}); retaining the valid "
                    "managed runtime"
                )
                print(
                    "\n".join(
                        f"    {line}" for line in detail.splitlines()
                    )
                )
        binary = _active_t3_binary(home)
        if binary is None:
            raise RuntimeError("T3 Code did not create a valid managed runtime")
        native_repaired = False
        if not _t3_native_runtime_healthy(username, home, node_bin, binary):
            _user_systemctl(username, uid, "stop", T3_SERVICE_NAME)
            _rebuild_t3_native_runtime(username, home, node_bin, binary)
            native_repaired = True
        daemon_reload = _user_systemctl(username, uid, "daemon-reload")
        if daemon_reload.returncode != 0:
            raise RuntimeError("Could not reload the T3 Code user service")
        enable = _user_systemctl(username, uid, "enable", T3_SERVICE_NAME)
        if enable.returncode != 0:
            raise RuntimeError("Could not enable the T3 Code user service")
        action = (
            "restart"
            if drop_in_changed
            or npm_shim_changed
            or (update_needed and binary != previous_binary)
            or native_repaired
            else "start"
        )
        service_result = _user_systemctl(username, uid, action, T3_SERVICE_NAME)
        if service_result.returncode != 0:
            detail = (service_result.stderr or service_result.stdout or "").strip()
            raise RuntimeError(f"Could not {action} the T3 Code user service: {detail}")
        _wait_for_t3_service(username, uid, host, port, home)
    except Exception:
        if legacy_present:
            _user_systemctl(username, uid, "stop", T3_SERVICE_NAME)
            run(
                ["systemctl", "start", f"{LEGACY_T3_SERVICE_NAME}.service"],
                check=False,
                capture_output=True,
            )
        raise

    if legacy_present:
        disabled = run(
            ["systemctl", "disable", f"{LEGACY_T3_SERVICE_NAME}.service"],
            check=False,
            capture_output=True,
        )
        if disabled.returncode != 0:
            _user_systemctl(username, uid, "stop", T3_SERVICE_NAME)
            run(
                ["systemctl", "start", f"{LEGACY_T3_SERVICE_NAME}.service"],
                check=False,
                capture_output=True,
            )
            raise RuntimeError("Could not disable the legacy T3 Code service")
        os.remove(LEGACY_T3_SERVICE_FILE)
        run(["systemctl", "daemon-reload"])
        run(
            ["systemctl", "reset-failed", f"{LEGACY_T3_SERVICE_NAME}.service"],
            check=False,
            capture_output=True,
        )
    _remove_legacy_shell_path(home, uid, gid)
    legacy_wrapper = os.path.join(home, ".local", "bin", "infra-tools-t3code-web")
    if os.path.isfile(legacy_wrapper) and not os.path.islink(legacy_wrapper):
        with open(legacy_wrapper, encoding="utf-8") as file_obj:
            if ".local/share/infra-tools/t3code" in file_obj.read():
                os.remove(legacy_wrapper)

    if update_failed and native_repaired:
        status = "retained and repaired after updater failure"
    elif update_failed:
        status = "retained and validated after updater failure"
    elif update_needed:
        status = "updated"
    elif native_repaired:
        status = "repaired"
    else:
        status = "already healthy"
    print(f"  ✓ T3 Code user service {status} for {username}")
    return binary


def _write_executable_if_changed(path: str, content: str) -> bool:
    """Atomically write a managed user executable without following symlinks."""

    if os.path.lexists(path) and (
        os.path.islink(path) or not os.path.isfile(path)
    ):
        raise RuntimeError(f"Refusing unsafe managed executable: {path}")
    changed = True
    try:
        with open(path, encoding="utf-8") as file_obj:
            changed = file_obj.read() != content
    except FileNotFoundError:
        pass
    if changed:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}-", dir=os.path.dirname(path)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
                file_obj.write(content)
            os.chmod(temporary, 0o755)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    else:
        os.chmod(path, 0o755)
    return changed


def _write_passthrough_wrapper(path: str, home: str) -> bool:
    """Write a stable launcher that follows T3's selected service version."""

    runtime = _t3_runtime_path(home)
    content = (
        "#!/bin/bash\n"
        "set -eu\n"
        f"export HOME={shlex.quote(home)}\n"
        'export NVM_DIR="$HOME/.nvm"\n'
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"\n'
        f"{_T3_GH_CONFIG_EXPORT}\n"
        f"state={shlex.quote(os.path.join(runtime, 'service-state.json'))}\n"
        'version=$(/usr/bin/python3 -c \'import json,re,sys; '
        'value=json.load(open(sys.argv[1], encoding="utf-8")); '
        'assert value.get("protocol") == 2; '
        'version=value["activeVersion"]; '
        f"assert re.fullmatch({json.dumps(_T3_VERSION_RE.pattern)}, version); "
        'print(version)\' "$state")\n'
        f'binary={shlex.quote(os.path.join(runtime, "versions"))}/"$version"'
        '/node_modules/t3/dist/bin.mjs\n'
        'test -x "$binary" || { echo "T3 Code runtime is unavailable" >&2; exit 1; }\n'
        'exec "$binary" "$@"\n'
    )
    return _write_executable_if_changed(path, content)


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _t3_pairing_urls(target_host: str, bind_host: str, port: int) -> tuple[str, str]:
    if bind_host == "0.0.0.0":
        local_host = "127.0.0.1"
        public_host = target_host
    elif bind_host in {"::", "[::]"}:
        local_host = "::1"
        public_host = target_host
    else:
        local_host = bind_host
        public_host = bind_host
    return (
        f"http://{_url_host(local_host)}:{port}",
        f"http://{_url_host(public_host)}:{port}",
    )


def _write_admin_pair_wrapper(
    path: str,
    home: str,
    workspace: str,
    t3_cli_wrapper: str,
    server_url: str,
    base_url: str,
) -> bool:
    content = (
        "#!/bin/bash\n"
        "set -eu\n"
        f"export HOME={shlex.quote(home)}\n"
        f"cd {shlex.quote(workspace)}\n"
        f"exec /usr/bin/python3 {shlex.quote(T3_ADMIN_PAIR_SCRIPT)} "
        f"--t3-binary {shlex.quote(t3_cli_wrapper)} "
        f"--base-dir {shlex.quote(os.path.join(home, '.t3'))} "
        f"--server-url {shlex.quote(server_url)} "
        f'--base-url {shlex.quote(base_url)} "$@"\n'
    )
    return _write_executable_if_changed(path, content)


def _write_text_if_changed(path: str, content: str, mode: int) -> bool:
    if os.path.islink(path):
        raise RuntimeError(f"Refusing symlinked managed configuration: {path}")
    try:
        with open(path, encoding="utf-8") as file_obj:
            changed = file_obj.read() != content
    except OSError:
        changed = True
    if changed:
        write_text_atomic(path, content, mode=mode)
    else:
        os.chmod(path, mode)
    return changed


def _ensure_t3_agent_skill(config: SetupConfig) -> bool:
    """Install managed agent-VM workflow skills for compatible agents."""

    return install_managed_agent_skills(
        config.username,
        config.selected_agent_tools(),
        (
            *agent_workflow_skill_names(config),
            *T3_CAPABILITY_AGENT_SKILL_NAMES,
        ),
        source_root=T3_AGENT_SKILLS_ROOT,
        reconcile_skill_names=BROWSER_AGENT_SKILL_NAMES,
    )


def _validate_htpasswd_file(path: str) -> None:
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError(f"Device-pairing auth must be a regular file: {path}")
    if os.path.getsize(path) > 64 * 1024:
        raise RuntimeError("Device-pairing auth file exceeds the size limit")
    try:
        with open(path, encoding="utf-8") as file_obj:
            records = [
                line.rstrip("\n") for line in file_obj if line.rstrip("\n")
            ]
    except UnicodeDecodeError as exc:
        raise RuntimeError("Device-pairing auth file must be UTF-8 text") from exc
    if not records:
        raise RuntimeError("Device-pairing auth file has no user records")
    for record in records:
        username, separator, password_hash = record.partition(":")
        if (
            not separator
            or not validate_username(username)
            or not password_hash.startswith("$")
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in record
            )
        ):
            raise RuntimeError("Device-pairing auth file contains an invalid record")


def _replace_pairing_auth_file(payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=".htpasswd-", dir=DEVICE_PAIRING_CONFIG_DIR
    )
    try:
        with os.fdopen(descriptor, "wb") as file_obj:
            file_obj.write(payload)
        os.chmod(temporary, 0o640)
        os.replace(temporary, DEVICE_PAIRING_AUTH_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _install_pairing_auth_file() -> tuple[bool, bytes | None]:
    if os.path.lexists(DEVICE_PAIRING_CONFIG_DIR) and (
        os.path.islink(DEVICE_PAIRING_CONFIG_DIR)
        or not os.path.isdir(DEVICE_PAIRING_CONFIG_DIR)
    ):
        raise RuntimeError(
            f"Refusing unsafe device-pairing config path: {DEVICE_PAIRING_CONFIG_DIR}"
        )
    os.makedirs(DEVICE_PAIRING_CONFIG_DIR, mode=0o750, exist_ok=True)
    source_available = os.path.lexists(DEVICE_PAIRING_PAYLOAD_FILE)
    previous_payload = None
    if source_available:
        _validate_htpasswd_file(DEVICE_PAIRING_PAYLOAD_FILE)
        with open(DEVICE_PAIRING_PAYLOAD_FILE, "rb") as source_file:
            payload = source_file.read()
        existing = None
        if os.path.lexists(DEVICE_PAIRING_AUTH_FILE):
            if os.path.islink(DEVICE_PAIRING_AUTH_FILE) or not os.path.isfile(
                DEVICE_PAIRING_AUTH_FILE
            ):
                raise RuntimeError(
                    "Refusing unsafe device-pairing auth destination: "
                    f"{DEVICE_PAIRING_AUTH_FILE}"
                )
            with open(DEVICE_PAIRING_AUTH_FILE, "rb") as destination_file:
                existing = destination_file.read()
            previous_payload = existing
        changed = existing != payload
        if changed:
            _replace_pairing_auth_file(payload)
    else:
        if not os.path.exists(DEVICE_PAIRING_AUTH_FILE):
            raise RuntimeError(
                "Device pairing needs --device-pairing-auth-file on first setup "
                "or credentials entered through --interactive"
            )
        _validate_htpasswd_file(DEVICE_PAIRING_AUTH_FILE)
        with open(DEVICE_PAIRING_AUTH_FILE, "rb") as destination_file:
            previous_payload = destination_file.read()
        changed = False

    web_account = pwd.getpwnam("www-data")
    os.chown(DEVICE_PAIRING_CONFIG_DIR, 0, web_account.pw_gid)
    os.chmod(DEVICE_PAIRING_CONFIG_DIR, 0o750)
    os.chown(DEVICE_PAIRING_AUTH_FILE, 0, web_account.pw_gid)
    os.chmod(DEVICE_PAIRING_AUTH_FILE, 0o640)
    return changed, previous_payload


def _restore_pairing_auth_file(previous_payload: bytes | None) -> None:
    if previous_payload is None:
        if os.path.exists(DEVICE_PAIRING_AUTH_FILE):
            os.remove(DEVICE_PAIRING_AUTH_FILE)
        return
    _replace_pairing_auth_file(previous_payload)
    web_account = pwd.getpwnam("www-data")
    os.chown(DEVICE_PAIRING_AUTH_FILE, 0, web_account.pw_gid)
    os.chmod(DEVICE_PAIRING_AUTH_FILE, 0o640)


def _nginx_listen_address(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _existing_pairing_t3_https_port() -> int | None:
    """Retain the last validated T3 HTTPS port during reconciliation."""

    if os.path.islink(DEVICE_PAIRING_PROVIDERS_FILE):
        raise RuntimeError(
            "Refusing symlinked device-pairing provider configuration: "
            f"{DEVICE_PAIRING_PROVIDERS_FILE}"
        )
    try:
        with open(DEVICE_PAIRING_PROVIDERS_FILE, encoding="utf-8") as file_obj:
            providers = json.load(file_obj)
        t3_provider = providers["providers"]["t3code"]
        https_port = t3_provider.get("https_public_port")
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if (
        not isinstance(https_port, int)
        or isinstance(https_port, bool)
        or not 1024 <= https_port <= 65535
    ):
        return None
    return https_port


def _configure_device_pairing(
    config: SetupConfig,
    home: str,
    t3_cli_wrapper: str,
    host: str,
    t3_port: int,
) -> None:
    t3_state_dir = os.path.join(home, ".t3")
    if os.path.lexists(t3_state_dir) and (
        os.path.islink(t3_state_dir) or not os.path.isdir(t3_state_dir)
    ):
        raise RuntimeError(
            f"Refusing unsafe T3 Connect state directory: {t3_state_dir}"
        )
    from web.web_steps import install_nginx

    install_nginx(config)
    os.makedirs(t3_state_dir, mode=0o700, exist_ok=True)
    os.chmod(t3_state_dir, 0o700)
    account = pwd.getpwnam(config.username)
    os.chown(t3_state_dir, account.pw_uid, account.pw_gid)
    auth_changed, previous_auth = _install_pairing_auth_file()
    web_account = pwd.getpwnam("www-data")
    server_url, _default_base_url = _t3_pairing_urls(config.host, host, t3_port)
    existing_https_port = _existing_pairing_t3_https_port()

    providers = {
        "version": 1,
        "providers": {
            "t3code": {
                "label": "T3 Code",
                "command": [
                    "/usr/bin/python3",
                    T3_ADMIN_PAIR_SCRIPT,
                    "--t3-binary",
                    t3_cli_wrapper,
                    "--base-dir",
                    t3_state_dir,
                    "--server-url",
                    server_url,
                    "--label",
                    "infra-tools device enrollment",
                    "--json",
                ],
                "base_url_flag": "--base-url",
                "url_field": "pairUrl",
                "expires_field": "expiresAt",
                "public_port": t3_port,
                "connect": {
                    "link_command": [
                        t3_cli_wrapper,
                        "connect",
                        "link",
                        "--headless",
                        "--base-dir",
                        t3_state_dir,
                    ],
                    "status_command": [
                        t3_cli_wrapper,
                        "connect",
                        "status",
                        "--json",
                        "--base-dir",
                        t3_state_dir,
                    ],
                    "unlink_command": [
                        t3_cli_wrapper,
                        "connect",
                        "unlink",
                        "--base-dir",
                        t3_state_dir,
                    ],
                    "restart_request": os.path.join(
                        t3_state_dir,
                        "infra-tools-connect-restart",
                    ),
                },
            }
        },
    }
    if existing_https_port is not None:
        providers["providers"]["t3code"]["https_public_port"] = existing_https_port
    providers_content = json.dumps(providers, indent=2, sort_keys=True) + "\n"
    providers_changed = _write_text_if_changed(
        DEVICE_PAIRING_PROVIDERS_FILE, providers_content, 0o640
    )
    os.chown(DEVICE_PAIRING_PROVIDERS_FILE, 0, web_account.pw_gid)

    service_content = f"""[Unit]
Description=infra-tools protected device-pairing broker
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=simple
User={config.username}
Group=www-data
RuntimeDirectory=infra-tools-device-pairing
RuntimeDirectoryMode=0750
UMask=0007
Environment=HOME={home}
Environment=T3CODE_PORT={t3_port}
ExecStart=/usr/bin/python3 {DEVICE_PAIRING_SCRIPT} --config {DEVICE_PAIRING_PROVIDERS_FILE} --socket {DEVICE_PAIRING_SOCKET}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={t3_state_dir}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    service_changed = _write_text_if_changed(
        DEVICE_PAIRING_SERVICE_FILE, service_content, 0o644
    )
    if service_changed:
        run("systemctl daemon-reload")
    enabled = run(
        f"systemctl is-enabled {DEVICE_PAIRING_SERVICE_NAME}.service",
        check=False,
    )
    if enabled.returncode != 0:
        run(f"systemctl enable {DEVICE_PAIRING_SERVICE_NAME}.service")
    if service_changed or providers_changed:
        run(f"systemctl restart {DEVICE_PAIRING_SERVICE_NAME}.service")
    else:
        active = run(
            f"systemctl is-active {DEVICE_PAIRING_SERVICE_NAME}.service",
            check=False,
        )
        if active.returncode != 0:
            run(f"systemctl start {DEVICE_PAIRING_SERVICE_NAME}.service")

    nginx_content = f"""# Managed by infra_tools device pairing
limit_req_zone $binary_remote_addr zone=infra_tools_device_pairing_auth:10m rate=5r/m;

map $status $infra_tools_device_pairing_auth_failure {{
    default 0;
    401 1;
}}

map $realip_remote_addr $infra_tools_device_pairing_gateway_request {{
    default 0;
    127.0.0.1 1;
    ::1 1;
}}

map "$infra_tools_device_pairing_gateway_request:$http_x_forwarded_proto" $infra_tools_device_pairing_public_proto {{
    default $scheme;
    "1:https" https;
}}

map "$infra_tools_device_pairing_gateway_request:$http_x_forwarded_host" $infra_tools_device_pairing_public_host {{
    default $host;
    ~^1:(?<infra_tools_device_pairing_forwarded_host>.+)$ $infra_tools_device_pairing_forwarded_host;
}}

log_format infra_tools_device_pairing_auth '$remote_addr [$time_local] infra-tools-auth-failure';

server {{
    listen {_nginx_listen_address(host, config.device_pairing_port)};
    server_name _;
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    real_ip_header X-Forwarded-For;
    real_ip_recursive on;
    access_log {DEVICE_PAIRING_AUTH_FAILURE_LOG} infra_tools_device_pairing_auth
        if=$infra_tools_device_pairing_auth_failure;

    auth_basic "Device pairing";
    auth_basic_user_file {DEVICE_PAIRING_AUTH_FILE};
    client_max_body_size 4k;

    location / {{
        limit_req zone=infra_tools_device_pairing_auth burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://unix:{DEVICE_PAIRING_SOCKET}:/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $infra_tools_device_pairing_public_host;
        proxy_set_header X-Forwarded-Proto $infra_tools_device_pairing_public_proto;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 35s;
        proxy_send_timeout 35s;
    }}
}}
"""
    previous_nginx_content = None
    if os.path.lexists(DEVICE_PAIRING_NGINX_SITE):
        if os.path.islink(DEVICE_PAIRING_NGINX_SITE) or not os.path.isfile(
            DEVICE_PAIRING_NGINX_SITE
        ):
            raise RuntimeError(
                f"Refusing unmanaged Nginx pairing site: {DEVICE_PAIRING_NGINX_SITE}"
            )
        with open(DEVICE_PAIRING_NGINX_SITE, encoding="utf-8") as file_obj:
            previous_nginx_content = file_obj.read()
    nginx_changed = previous_nginx_content != nginx_content
    if nginx_changed:
        write_text_atomic(DEVICE_PAIRING_NGINX_SITE, nginx_content, mode=0o644)
    link_created = False
    if not os.path.islink(DEVICE_PAIRING_NGINX_LINK):
        if os.path.lexists(DEVICE_PAIRING_NGINX_LINK):
            raise RuntimeError(
                f"Refusing unmanaged Nginx pairing site: {DEVICE_PAIRING_NGINX_LINK}"
            )
        os.symlink(DEVICE_PAIRING_NGINX_SITE, DEVICE_PAIRING_NGINX_LINK)
        nginx_changed = True
        link_created = True
    validation = run("nginx -t", check=False, capture_output=True)
    if validation.returncode != 0:
        if previous_nginx_content is None:
            if os.path.exists(DEVICE_PAIRING_NGINX_SITE):
                os.remove(DEVICE_PAIRING_NGINX_SITE)
        else:
            write_text_atomic(
                DEVICE_PAIRING_NGINX_SITE,
                previous_nginx_content,
                mode=0o644,
            )
        if link_created and os.path.islink(DEVICE_PAIRING_NGINX_LINK):
            os.unlink(DEVICE_PAIRING_NGINX_LINK)
        if auth_changed:
            _restore_pairing_auth_file(previous_auth)
        raise RuntimeError("Nginx rejected the device-pairing configuration")
    if nginx_changed or auth_changed:
        run("systemctl reload nginx")
    configure_nginx_auth_failure_ban(
        "device-pairing",
        DEVICE_PAIRING_AUTH_FAILURE_LOG,
    )


def _remove_device_pairing() -> None:
    if os.path.islink(DEVICE_PAIRING_CONFIG_DIR):
        raise RuntimeError(
            f"Refusing unsafe device-pairing config path: {DEVICE_PAIRING_CONFIG_DIR}"
        )
    changed = False
    if os.path.exists(DEVICE_PAIRING_SERVICE_FILE):
        run(f"systemctl disable --now {DEVICE_PAIRING_SERVICE_NAME}.service", check=False)
        os.remove(DEVICE_PAIRING_SERVICE_FILE)
        run("systemctl daemon-reload")
    for path in (
        DEVICE_PAIRING_NGINX_LINK,
        DEVICE_PAIRING_NGINX_SITE,
        DEVICE_PAIRING_PROVIDERS_FILE,
        DEVICE_PAIRING_AUTH_FILE,
    ):
        if os.path.lexists(path):
            os.remove(path)
            changed = True
    if os.path.isdir(DEVICE_PAIRING_CONFIG_DIR):
        try:
            os.rmdir(DEVICE_PAIRING_CONFIG_DIR)
        except OSError:
            pass
    if changed and shutil.which("nginx"):
        validation = run("nginx -t", check=False, capture_output=True)
        if validation.returncode != 0:
            raise RuntimeError("Nginx configuration is invalid after removing device pairing")
        run("systemctl reload nginx", check=False)
    remove_nginx_auth_failure_ban("device-pairing")


def _configure_connect_restart_units(
    state_dir: str,
    username: str,
    uid: int,
) -> None:
    """Install a root-owned path trigger for safe T3 service reconciliation."""

    if os.geteuid() != 0:
        return
    if os.path.lexists(state_dir) and (
        os.path.islink(state_dir) or not os.path.isdir(state_dir)
    ):
        raise RuntimeError(f"Refusing unsafe T3 Connect state directory: {state_dir}")
    request_path = os.path.join(state_dir, "infra-tools-connect-restart")
    path_content = f"""[Unit]
Description=Watch for T3 Connect service reconciliation requests

[Path]
PathExists={request_path}
Unit={T3_CONNECT_RESTART_SERVICE_UNIT}

[Install]
WantedBy=multi-user.target
"""
    service_content = f"""[Unit]
Description=Reconcile the managed T3 Connect service
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/rm -f {request_path}
ExecStart=/usr/sbin/runuser -u {username} -- /usr/bin/env XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus /usr/bin/systemctl --user restart {T3_SERVICE_NAME}.service
"""
    path_changed = _write_text_if_changed(
        T3_CONNECT_RESTART_PATH_FILE,
        path_content,
        0o644,
    )
    service_changed = _write_text_if_changed(
        T3_CONNECT_RESTART_SERVICE_FILE,
        service_content,
        0o644,
    )
    if path_changed or service_changed:
        run("systemctl daemon-reload")
    enabled = run(
        f"systemctl is-enabled {T3_CONNECT_RESTART_PATH_UNIT}",
        check=False,
    )
    if enabled.returncode != 0:
        run(f"systemctl enable {T3_CONNECT_RESTART_PATH_UNIT}")
    active = run(
        f"systemctl is-active {T3_CONNECT_RESTART_PATH_UNIT}",
        check=False,
    )
    if active.returncode != 0:
        run(f"systemctl start {T3_CONNECT_RESTART_PATH_UNIT}")


def _remove_connect_restart_units(state_dir: str | None = None) -> None:
    if os.geteuid() != 0:
        return
    if state_dir is not None and os.path.lexists(state_dir) and (
        os.path.islink(state_dir) or not os.path.isdir(state_dir)
    ):
        raise RuntimeError(f"Refusing unsafe T3 Connect state directory: {state_dir}")
    changed = False
    for unit_name, unit_path in (
        (T3_CONNECT_RESTART_PATH_UNIT, T3_CONNECT_RESTART_PATH_FILE),
        (T3_CONNECT_RESTART_SERVICE_UNIT, T3_CONNECT_RESTART_SERVICE_FILE),
    ):
        if os.path.lexists(unit_path):
            if os.path.islink(unit_path):
                raise RuntimeError(f"Refusing symlinked managed unit: {unit_path}")
            run(f"systemctl disable --now {unit_name}", check=False)
            os.remove(unit_path)
            changed = True
    if state_dir is not None:
        request_path = os.path.join(state_dir, "infra-tools-connect-restart")
        if os.path.lexists(request_path):
            if os.path.islink(request_path) or not os.path.isfile(request_path):
                raise RuntimeError(
                    f"Refusing unsafe T3 Connect restart request: {request_path}"
                )
            os.remove(request_path)
    if changed:
        run("systemctl daemon-reload")


def _configure_t3_https(
    config: SetupConfig,
    port: int,
    pairing_port: int | None,
) -> dict[str, tuple[str, int]]:
    """Publish T3 web and pairing pages through the shared internal HTTPS gateway."""

    if os.geteuid() != 0 or not os.path.isfile(
        "/opt/infra_tools/common/service_tools/infra_web.py"
    ):
        # Target setup is root-owned; keeping this a no-op makes dry unit tests
        # and installations without the managed gateway side-effect free.
        return {}

    from common.godot_web_steps import configure_internal_web_host, identities_for_config

    configure_internal_web_host(
        identities_for_config(config.host, config.system_hostname),
        [config.username],
        config.effective_web_interface_sources(),
        configure_static_site=True,
        install_utility=True,
    )
    utility = "/usr/local/bin/infra-web"
    endpoints: dict[str, tuple[str, int]] = {}
    routes = [("t3code", port, "50m")]
    if pairing_port is not None:
        routes.append(("t3code-pairing", pairing_port, None))
    for name, target_port, max_body_size in routes:
        body_size_argument = (
            " --max-body-size " + shlex.quote(max_body_size)
            if max_body_size is not None
            else ""
        )
        result = run(
            "SUDO_USER="
            + shlex.quote(config.username)
            + " "
            + shlex.quote(utility)
            + " forward add "
            + shlex.quote(name)
            + " --listen auto --to 127.0.0.1:"
            + str(target_port)
            + body_size_argument
            + " --json",
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "HTTPS forward failed").strip()
            raise RuntimeError(f"Could not configure HTTPS T3 endpoint: {detail}")
        try:
            payload = json.loads(result.stdout)
            url = payload.get("url")
            listen = payload.get("listen")
        except (TypeError, ValueError):
            url = None
            listen = None
        if (
            not isinstance(url, str)
            or not url.startswith("https://")
            or not isinstance(listen, int)
            or not 1024 <= listen <= 65535
        ):
            raise RuntimeError("HTTPS gateway returned an invalid T3 endpoint")
        endpoints[name] = (url, listen)
    return endpoints


def _set_pairing_t3_https_port(https_port: int) -> None:
    """Set the public HTTPS port used by links issued from the pairing portal."""

    if (
        not isinstance(https_port, int)
        or isinstance(https_port, bool)
        or not 1024 <= https_port <= 65535
    ):
        raise ValueError("T3 Code HTTPS port must be between 1024 and 65535")
    if os.path.islink(DEVICE_PAIRING_PROVIDERS_FILE):
        raise RuntimeError(
            f"Refusing symlinked device-pairing provider configuration: "
            f"{DEVICE_PAIRING_PROVIDERS_FILE}"
        )
    with open(DEVICE_PAIRING_PROVIDERS_FILE, encoding="utf-8") as file_obj:
        providers = json.load(file_obj)
    if not isinstance(providers, dict) or not isinstance(providers.get("providers"), dict):
        raise RuntimeError("Invalid device-pairing provider configuration")
    t3_provider = providers["providers"].get("t3code")
    if not isinstance(t3_provider, dict):
        raise RuntimeError("T3 Code pairing provider is missing")
    if t3_provider.get("https_public_port") == https_port:
        return
    t3_provider["https_public_port"] = https_port
    _write_text_if_changed(
        DEVICE_PAIRING_PROVIDERS_FILE,
        json.dumps(providers, indent=2, sort_keys=True) + "\n",
        0o640,
    )
    web_account = pwd.getpwnam("www-data")
    os.chown(DEVICE_PAIRING_PROVIDERS_FILE, 0, web_account.pw_gid)
    os.chmod(DEVICE_PAIRING_PROVIDERS_FILE, 0o640)
    run(f"systemctl restart {DEVICE_PAIRING_SERVICE_NAME}.service")


def _remove_t3_https(config: SetupConfig) -> None:
    utility = "/usr/local/bin/infra-web"
    for name in ("t3code-pairing", "t3code"):
        run(
            "SUDO_USER="
            + shlex.quote(config.username)
            + " "
            + shlex.quote(utility)
            + " forward remove "
            + shlex.quote(name)
            + " --json",
            check=False,
            capture_output=True,
        )


def install_t3code_web(config: SetupConfig) -> None:
    """Install a boot-persistent T3 Code headless service for direct pairing."""

    if is_dry_run():
        print("  [DRY-RUN] Would install the T3 Code headless web service")
        return

    home = _user_home(config)
    account = pwd.getpwnam(config.username)
    host = config.web_interface_host or "127.0.0.1"
    port = config.web_interface_port
    workspace = _workspace(config, home)
    _configure_firewall(config, port, host)

    os.makedirs(workspace, mode=0o755, exist_ok=True)
    os.makedirs(os.path.join(home, ".local", "bin"), mode=0o755, exist_ok=True)
    _install_t3_service(
        home,
        config.username,
        account.pw_uid,
        account.pw_gid,
        workspace,
        host,
        port,
        # A selected T3 Code interface is reconciled on every setup run so
        # infrequently used VMs receive upstream service updates as well.
        refresh=True,
    )
    pair_wrapper = os.path.join(home, ".local", "bin", "t3code-pair")
    t3_cli_wrapper = os.path.join(
        home, ".local", "bin", "infra-tools-t3code-pairing-provider"
    )
    if os.path.islink(T3_ADMIN_PAIR_SCRIPT) or not os.path.isfile(T3_ADMIN_PAIR_SCRIPT):
        raise RuntimeError(f"T3 administrative pairing helper is missing: {T3_ADMIN_PAIR_SCRIPT}")
    os.chmod(T3_ADMIN_PAIR_SCRIPT, 0o755)
    _write_passthrough_wrapper(t3_cli_wrapper, home)
    server_url, base_url = _t3_pairing_urls(config.host, host, port)
    _write_admin_pair_wrapper(
        pair_wrapper,
        home,
        workspace,
        t3_cli_wrapper,
        server_url,
        base_url,
    )
    os.chown(pair_wrapper, account.pw_uid, account.pw_gid)
    os.chown(t3_cli_wrapper, account.pw_uid, account.pw_gid)
    os.chown(workspace, account.pw_uid, account.pw_gid)
    _ensure_t3_agent_skill(config)

    if config.device_pairing_providers:
        _configure_device_pairing(config, home, t3_cli_wrapper, host, port)
        _configure_connect_restart_units(
            os.path.join(home, ".t3"),
            config.username,
            account.pw_uid,
        )
        https_endpoints = _configure_t3_https(
            config,
            port,
            config.device_pairing_port,
        )
        if https_endpoints:
            t3_https = https_endpoints.get("t3code")
            pairing_https = https_endpoints.get("t3code-pairing")
            if t3_https is None or pairing_https is None:
                raise RuntimeError(
                    "HTTPS gateway returned incomplete T3 Code endpoints"
                )
            _write_admin_pair_wrapper(
                pair_wrapper,
                home,
                workspace,
                t3_cli_wrapper,
                server_url,
                t3_https[0],
            )
            os.chown(pair_wrapper, account.pw_uid, account.pw_gid)
            _set_pairing_t3_https_port(t3_https[1])
    else:
        _remove_connect_restart_units(os.path.join(home, ".t3"))
        _remove_device_pairing()
        _remove_t3_https(config)
        https_endpoints = _configure_t3_https(config, port, None)
        if https_endpoints:
            t3_https = https_endpoints.get("t3code")
            if t3_https is None:
                raise RuntimeError("HTTPS gateway returned no T3 Code endpoint")
            _write_admin_pair_wrapper(
                pair_wrapper,
                home,
                workspace,
                t3_cli_wrapper,
                server_url,
                t3_https[0],
            )
            os.chown(pair_wrapper, account.pw_uid, account.pw_gid)
    print(f"  T3 Code web service listening on {host}:{port}")
    for name, label in (
        ("t3code", "T3 Code HTTPS endpoint"),
        ("t3code-pairing", "T3 Code pairing HTTPS endpoint"),
    ):
        endpoint = https_endpoints.get(name)
        if endpoint is not None:
            print(f"  {label}: {endpoint[0]}")
    print(
        "  T3 Code HTTP compatibility: "
        f"port {port} ({host}); use the printed HTTPS endpoint"
    )
    print("  Readiness check: infra-tools agent doctor --capability t3code")
    if config.device_pairing_providers:
        print(
            "  Protected device enrollment HTTP compatibility: "
            f"port {config.device_pairing_port} ({host}); use the printed HTTPS endpoint"
        )
    else:
        print(
            "  Pairing is required: run 'infra-tools agent web pair HOST USER' "
            "from the control system"
        )
    print(
        "  Use the full one-time pairing URL; the bare web address intentionally "
        "shows a pairing-key form"
    )
    if config.install_gh:
        print(
            "  GitHub authentication is server-side; use 'gh auth status' as the "
            "target user and keep repository URLs on HTTPS"
        )


__all__ = ["install_t3code_web"]
