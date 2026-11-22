#!/usr/bin/env python3
"""
Cloudflare Tunnel Setup Script

This script automates the complete setup of Cloudflare tunnels for deployed sites.
It runs during interactive --cloudflare setup, or directly on the server.
"""

from __future__ import annotations

import os
import sys
import json
import subprocess
from pathlib import Path
import re
import shlex
import shutil
import tempfile
from typing import Optional, Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '../..'))

from lib.types import StrDict, JSONDict
from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.machine_state import can_manage_firewall
from lib.maintenance_defaults import APT_LOCK_OPTIONS, APT_UPDATE_OPTIONS
from lib.remote_utils import CommandTimeoutError, run as run_bounded_command
from lib.validators import validate_host, validate_ip_address
from lib.validation import validate_cloudflare_tunnel_name
from lib.setup_prompts import setup_input
from web.cloudflare_firewall import normalize_access_sources, reconcile_tunnel_firewall
from web.cloudflared_runtime import refresh_cloudflared_service


CONFIG_DIR = "/etc/cloudflared"
STATE_FILE = "/etc/cloudflared/tunnel-state.json"
NGINX_SITES_DIR = "/etc/nginx/sites-enabled"
CLOUDFLARE_APT_KEY_URL = "https://pkg.cloudflare.com/cloudflare-main.gpg"
CLOUDFLARE_APT_KEYRING = "/usr/share/keyrings/cloudflare-main.gpg"
CLOUDFLARE_APT_SOURCE = "/etc/apt/sources.list.d/cloudflared.list"
CLOUDFLARE_APT_SOURCE_CONTENT = (
    "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
    "https://pkg.cloudflare.com/cloudflared any main\n"
)
SERVICE_STARTUP_DROP_IN = "/etc/systemd/system/cloudflared.service.d/50-basaltwater-startup.conf"
SERVICE_STARTUP_CONTENT = (
    "# Allow DNS retries and automatic tunnel transport fallback before readiness.\n"
    "[Service]\n"
    "TimeoutStartSec=120s\n"
)
SERVICE_COMMAND_TIMEOUT = 240
ORIGIN_SERVICE = "http://localhost:80"


def run_command(
    cmd: list[str], check: bool = True, capture_output: bool = False,
    *, timeout: Optional[float] = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and optionally capture output."""
    if timeout is not None:
        # Service installation spawns systemctl. Bound the entire process group
        # so a timed-out child cannot keep captured output pipes open forever.
        return run_bounded_command(
            cmd, check=check, capture_output=capture_output, timeout=timeout,
        )
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=True,
        )
        return result
    except subprocess.CalledProcessError as e:
        if check:
            print(f"✗ Command failed: {' '.join(cmd)}")
            if e.stderr:
                print(f"  Error: {e.stderr}")
            sys.exit(1)
        raise



def check_root():
    """Ensure script is run as root."""
    if os.geteuid() != 0:
        print("✗ This script must be run as root")
        print("  Please run: sudo python3 setup_cloudflare_tunnel.py")
        sys.exit(1)


def install_cloudflared():
    """Reconcile the signed APT source and install or upgrade cloudflared."""
    print("Installing or updating cloudflared...")

    # The old release download used a floating URL and wrote into the current
    # directory before invoking dpkg.  Use Cloudflare's signed repository so
    # APT verifies the package and no attacker-controlled working directory is
    # involved.
    # Repair the signing key before refreshing indexes on existing hosts:
    # an expired key must not prevent setup from installing its replacement.
    if not shutil.which("curl") or not os.path.isfile("/etc/ssl/certs/ca-certificates.crt"):
        run_command(["apt-get", "update", *APT_LOCK_OPTIONS, *APT_UPDATE_OPTIONS])
        run_command(["apt-get", "install", "-y", *APT_LOCK_OPTIONS, "ca-certificates", "curl"])
    os.makedirs(os.path.dirname(CLOUDFLARE_APT_KEYRING), mode=0o755, exist_ok=True)

    file_descriptor, temporary_key = tempfile.mkstemp(
        prefix=".cloudflare-main-",
        dir=os.path.dirname(CLOUDFLARE_APT_KEYRING),
    )
    os.close(file_descriptor)
    try:
        run_command([
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--tlsv1.2",
            "--output",
            temporary_key,
            CLOUDFLARE_APT_KEY_URL,
        ])
        os.chmod(temporary_key, 0o644)
        os.replace(temporary_key, CLOUDFLARE_APT_KEYRING)
    finally:
        if os.path.exists(temporary_key):
            os.unlink(temporary_key)

    write_text_atomic(CLOUDFLARE_APT_SOURCE, CLOUDFLARE_APT_SOURCE_CONTENT, mode=0o644)
    run_command(["apt-get", "update", *APT_LOCK_OPTIONS, *APT_UPDATE_OPTIONS])
    run_command(["apt-get", "install", "-y", *APT_LOCK_OPTIONS, "cloudflared"])
    print("✓ cloudflared package is up to date")


def authenticate_cloudflare():
    """Guide user through Cloudflare authentication."""
    print("\nAuthentication Required")
    print("=" * 50)
    print("You need to authenticate with Cloudflare.")
    print("Open the login URL printed below in your own browser and authorize this server.")
    print()
    
    setup_input("Press Enter to continue with authentication...")
    
    print("\nRunning: cloudflared tunnel login")
    result = run_command(['cloudflared', 'tunnel', 'login'], check=False)
    
    if result.returncode != 0:
        print("✗ Authentication failed")
        sys.exit(1)
    
    cert_file = '/root/.cloudflared/cert.pem'
    if not os.path.exists(cert_file):
        print(f"✗ Certificate file not found at {cert_file}")
        sys.exit(1)
    
    print("✓ Authentication successful")
    return cert_file


def create_tunnel(tunnel_name: str) -> dict[str, Any]:
    """Create a new Cloudflare tunnel."""
    print(f"\nCreating tunnel: {tunnel_name}")
    
    result = run_command(
        ['cloudflared', 'tunnel', 'create', tunnel_name],
        capture_output=True
    )
    
    output = result.stdout
    tunnel_id_match = re.search(r'Created tunnel .+ with id ([a-f0-9-]+)', output)
    
    if not tunnel_id_match:
        print("✗ Failed to extract tunnel ID from output")
        sys.exit(1)
    
    tunnel_id = tunnel_id_match.group(1)
    credentials_file = f'/root/.cloudflared/{tunnel_id}.json'
    
    if not os.path.exists(credentials_file):
        print(f"✗ Credentials file not found: {credentials_file}")
        sys.exit(1)
    
    dest_credentials = f"{CONFIG_DIR}/{tunnel_id}.json"
    
    os.makedirs(CONFIG_DIR, mode=0o755, exist_ok=True)
    
    with open(credentials_file, 'r') as src:
        credentials_data = json.load(src)
    
    write_json_atomic(dest_credentials, credentials_data, mode=0o600)
    
    print(f"✓ Tunnel created: {tunnel_name} (ID: {tunnel_id})")
    
    return {
        'name': tunnel_name,
        'id': tunnel_id,
        'credentials_file': dest_credentials
    }


def normalize_hostname(hostname: str) -> str:
    """Require a literal, qualified DNS hostname for public ingress."""
    if not validate_host(hostname):
        raise ValueError(f"Invalid tunnel hostname: {hostname!r}")
    normalized = hostname.lower().removesuffix(".")
    if "." not in normalized or validate_ip_address(normalized):
        raise ValueError(f"Invalid tunnel hostname: {hostname!r}")
    return normalized


def _normalize_hostnames(hostnames: list[str]) -> list[str]:
    if not isinstance(hostnames, list):
        raise ValueError("Tunnel hostname selection must be a list")
    return sorted({normalize_hostname(hostname) for hostname in hostnames})


def _nginx_http_hostnames(content: str) -> set[str]:
    """Read exact names from HTTP vhosts that can serve the loopback origin."""
    lexer = shlex.shlex(content, posix=True, punctuation_chars=";{}")
    lexer.whitespace_split = True
    directive: list[str] = []
    hostnames: set[str] = set()
    names: list[str] = []
    listeners: list[list[str]] = []
    blocks: list[str] = []
    for token in lexer:
        if not all(char in ";{}" for char in token):
            directive.append(token)
            continue
        for separator in token:
            if separator == "{":
                blocks.append(directive[0] if directive else "")
            elif blocks == ["server"]:
                if separator == "}":
                    if not listeners or any(
                        listen[0] in {"80", "*:80", "0.0.0.0:80", "[::]:80", "127.0.0.1:80", "[::1]:80"}
                        and not {"ssl", "quic", "proxy_protocol"}.intersection(listen[1:])
                        for listen in listeners
                    ):
                        for name in names:
                            try:
                                hostnames.add(normalize_hostname(name))
                            except ValueError:
                                continue
                    names.clear()
                    listeners.clear()
                elif directive[:1] == ["server_name"]:
                    names.extend(directive[1:])
                elif directive[:1] == ["listen"] and len(directive) > 1:
                    listeners.append(directive[1:])
            if separator == "}":
                if not blocks:
                    raise ValueError("Unmatched Nginx configuration block")
                blocks.pop()
            directive.clear()
    return hostnames


def enabled_nginx_hostnames() -> set[str]:
    """Inspect enabled sites only to check an independently selected hostname."""
    names: set[str] = set()
    for config_file in Path(NGINX_SITES_DIR).glob("*"):
        if config_file.is_file():
            try:
                names.update(_nginx_http_hostnames(config_file.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise ValueError(f"Could not inspect Nginx site {config_file}: {exc}") from exc
    return names


def generate_config_yml(tunnel: JSONDict, sites: list[StrDict]) -> str:
    """Generate cloudflared config.yml content."""
    tunnel_id = tunnel.get('id')
    credentials_file = tunnel.get('credentials_file')
    if not isinstance(tunnel_id, str) or not tunnel_id:
        raise ValueError("Tunnel state is missing a tunnel ID")
    if not isinstance(credentials_file, str) or not os.path.isabs(credentials_file):
        raise ValueError("Tunnel state must contain an absolute credentials path")

    config_lines = [
        f"tunnel: {json.dumps(tunnel_id)}",
        f"credentials-file: {json.dumps(credentials_file)}",
        "",
        "ingress:"
    ]

    for site in sites:
        hostname = site.get('hostname')
        service = site.get('service')
        hostname = normalize_hostname(hostname)
        if service != ORIGIN_SERVICE:
            raise ValueError(f"Unsupported tunnel origin service: {service!r}")
        config_lines.append(f"  - hostname: {json.dumps(hostname)}")
        config_lines.append(f"    service: {json.dumps(service)}")
        config_lines.append("    originRequest:")
        config_lines.append(f"      httpHostHeader: {json.dumps(hostname)}")
    
    config_lines.append("  - service: http_status:404")
    config_lines.append("")
    
    return "\n".join(config_lines)


def save_state(
    tunnel: JSONDict, sites: list[StrDict], *, pending_activation: bool = False,
    allow_direct: bool = False, access_sources: list[str] | None = None,
    managed_hostnames: list[str] | None = None, hostnames: list[str] | None = None,
):
    """Save tunnel state for future runs."""
    if not isinstance(allow_direct, bool):
        raise ValueError("Cloudflare direct access must be a boolean")
    state: JSONDict = {
        'tunnel': tunnel,
        'sites': sites,
        'allow_direct': allow_direct,
        'access_sources': normalize_access_sources(access_sources),
    }
    if pending_activation:
        state['pending_activation'] = True
    if managed_hostnames is not None or hostnames is not None:
        state['managed_hostnames'] = [] if managed_hostnames is None else _normalize_hostnames(managed_hostnames)
        state['hostnames'] = [] if hostnames is None else _normalize_hostnames(hostnames)
    
    os.makedirs(CONFIG_DIR, mode=0o755, exist_ok=True)
    
    write_json_atomic(STATE_FILE, state, mode=0o600)


def load_state() -> Optional[dict[str, Any]]:
    """Load saved tunnel state."""
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
    except Exception as e:
        raise ValueError(f"Could not load Cloudflare tunnel state: {e}") from e

    if not isinstance(state, dict):
        raise ValueError("Cloudflare tunnel state must be a JSON object")
    if not isinstance(state.get('pending_activation', False), bool):
        raise ValueError("Cloudflare tunnel state has an invalid pending_activation flag")
    if not isinstance(state.get('allow_direct', False), bool):
        raise ValueError("Cloudflare tunnel state has an invalid allow_direct flag")
    if 'access_sources' in state and not isinstance(state['access_sources'], list):
        raise ValueError("Cloudflare tunnel state has an invalid access_sources list")
    normalize_access_sources(state.get('access_sources', []))
    tunnel = state.get('tunnel')
    if not isinstance(tunnel, dict):
        raise ValueError("Cloudflare tunnel state is missing its tunnel object")
    for key in ('name', 'id', 'credentials_file'):
        if not isinstance(tunnel.get(key), str) or not tunnel[key]:
            raise ValueError(f"Cloudflare tunnel state has an invalid {key}")
    if not os.path.isabs(tunnel['credentials_file']):
        raise ValueError("Cloudflare tunnel credentials path must be absolute")

    sites = state.get('sites')
    if not isinstance(sites, list):
        raise ValueError("Cloudflare tunnel state is missing its sites list")
    for site in sites:
        if not isinstance(site, dict):
            raise ValueError("Cloudflare tunnel state contains an invalid site")
        if not isinstance(site.get('hostname'), str) or not validate_host(site['hostname']):
            raise ValueError("Cloudflare tunnel state contains an invalid hostname")
        if site.get('service') != ORIGIN_SERVICE:
            raise ValueError("Cloudflare tunnel state contains an unsupported origin")
    if 'managed_hostnames' in state or 'hostnames' in state:
        for key in ('managed_hostnames', 'hostnames'):
            state[key] = _normalize_hostnames(state.get(key))
    return state


def _write_config_file(config_file: str, content: str) -> None:
    """Validate a generated config before atomically installing it."""
    os.makedirs(os.path.dirname(config_file), mode=0o755, exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=".cloudflared-config-",
        dir=os.path.dirname(config_file),
        text=True,
    )
    os.close(file_descriptor)
    try:
        write_text_atomic(temporary_path, content, mode=0o600)
        result = run_command(
            ['cloudflared', 'tunnel', '--config', temporary_path, 'ingress', 'validate'],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise ValueError(
                f"cloudflared rejected the generated configuration: {detail or 'validation failed'}"
            )
        os.replace(temporary_path, config_file)
        os.chmod(config_file, 0o600)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _config_is_current(config_file: str, expected: str) -> bool:
    """Require the exact selected ingress even when another config is valid."""
    try:
        if Path(config_file).read_text(encoding="utf-8") != expected:
            return False
    except FileNotFoundError:
        return False
    result = run_command(
        ['cloudflared', 'tunnel', '--config', config_file, 'ingress', 'validate'],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        print(f"  ⚠ Existing Cloudflare config is invalid: {detail or 'validation failed'}")
        return False
    return True


def _run_service_command(cmd: list[str], *, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    """Bound service operations and retain failures for diagnostic reporting."""
    try:
        return run_command(cmd, check=False, capture_output=True, timeout=timeout)
    except (CommandTimeoutError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(cmd, 124, "", f"Command timed out after {timeout}s")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _redact_service_output(output: str) -> str:
    """Avoid printing cloudflared's settings or inline authentication secrets."""
    lines = []
    for line in output.splitlines():
        if re.search(r'\bSettings:', line, re.IGNORECASE):
            lines.append("[cloudflared settings omitted]")
            continue
        lines.append(re.sub(
            r'''(?i)((?:--token(?:=|\s+)|["']?\b(?:token|tunnel[_-]?secret|authorization)'''
            r'''["']?\s*[:=]\s*)["']?(?:Bearer\s+)?)([^\s"',}\]]+)''',
            r'\1[redacted]',
            line,
        ))
    return "\n".join(lines)


def _show_service_diagnostics() -> None:
    """Include the daemon's errors rather than only systemctl's timeout summary."""
    print("\nCloudflared startup diagnostics:")
    commands = (
        ['cloudflared', '--version'],
        ['systemctl', 'show', 'cloudflared',
         '--property=LoadState,ActiveState,SubState,Type,TimeoutStartUSec,Result,ExecMainStatus'],
        ['journalctl', '-u', 'cloudflared.service', '-n', '40', '--no-pager', '--output=cat'],
    )
    for command in commands:
        result = _run_service_command(command, timeout=10)
        detail = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part and part.strip()
        )
        if detail:
            print(_redact_service_output(detail))
    print(
        "Tunnel startup needs working DNS and outbound Cloudflare connectivity on "
        "port 7844 (UDP for QUIC or TCP for HTTP/2). Check the VM/provider firewall "
        "and the journal errors above; hosts-file entries for your sites do not establish this connection."
    )


def _ensure_service_running(*, restart: bool = False) -> bool:
    """Start the installed service when possible and verify it is active."""
    if restart:
        result = _run_service_command(
            ['systemctl', 'restart', 'cloudflared'],
            timeout=SERVICE_COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            print(f"  ⚠ Cloudflared restart failed: {_redact_service_output(detail) or 'restart failed'}")
            _show_service_diagnostics()
            return False
    result = _run_service_command(
        ['systemctl', 'is-active', 'cloudflared'],
    )
    if result.returncode != 0:
        result = _run_service_command(
            ['systemctl', 'start', 'cloudflared'],
            timeout=SERVICE_COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            print(f"  ⚠ Cloudflared service is not running: {_redact_service_output(detail) or 'start failed'}")
            _show_service_diagnostics()
            return False
        result = _run_service_command(
            ['systemctl', 'is-active', 'cloudflared'],
        )
    if result.returncode != 0:
        print("  ⚠ Cloudflared service did not become active")
        _show_service_diagnostics()
        return False
    return True


def _configure_firewall(*, allow_direct: bool, access_sources: list[str]) -> None:
    """Apply the requested direct web policy after verified tunnel activation."""
    if not can_manage_firewall():
        print("  ✓ Skipping Cloudflare firewall policy (machine cannot manage UFW)")
        return
    reconcile_tunnel_firewall(
        allow_direct=allow_direct, sources=access_sources, command_runner=run_command,
    )


def install_and_start_service(*, restart: bool = False) -> bool:
    """Recover incomplete installs and verify readiness without restarting a new service."""
    print("\nInstalling cloudflared service...")

    # cloudflared service install starts the daemon itself. Install the drop-in
    # first so both that initial start and later retries receive a finite grace
    # period, retaining Type=notify's connection readiness check.
    os.makedirs(os.path.dirname(SERVICE_STARTUP_DROP_IN), mode=0o755, exist_ok=True)
    write_text_atomic(SERVICE_STARTUP_DROP_IN, SERVICE_STARTUP_CONTENT, mode=0o644)
    result = _run_service_command(['systemctl', 'daemon-reload'])
    if result.returncode != 0:
        print(f"  ⚠ Could not reload cloudflared service configuration: {_redact_service_output(result.stderr or result.stdout)}")
        _show_service_diagnostics()
        return False

    existing = _run_service_command(
        ['systemctl', 'show', 'cloudflared', '--property=LoadState', '--value'],
    )
    load_state = existing.stdout.strip()
    if existing.returncode != 0 or load_state not in ('loaded', 'not-found'):
        print(f"  ⚠ Could not inspect cloudflared service: {_redact_service_output(existing.stderr or load_state)}")
        _show_service_diagnostics()
        return False
    newly_installed = load_state == 'not-found'
    if newly_installed:
        result = _run_service_command(
            ['cloudflared', '--config', f'{CONFIG_DIR}/config.yml', 'service', 'install'],
            timeout=SERVICE_COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            print(f"  ⚠ Cloudflared service installation or initial startup failed: {_redact_service_output(result.stderr or result.stdout)}")
            _show_service_diagnostics()
            return False
    
    print("✓ Service installed")
    
    print("\nVerifying cloudflared service...")
    result = _run_service_command(['systemctl', 'enable', 'cloudflared'])
    if result.returncode != 0:
        print(f"  ⚠ Could not enable cloudflared service: {_redact_service_output(result.stderr or result.stdout)}")
        _show_service_diagnostics()
        return False
    if not _ensure_service_running(restart=restart and not newly_installed):
        return False
    try:
        if refresh_cloudflared_service():
            print("✓ Restarted cloudflared to load the updated package")
    except (OSError, TimeoutError, RuntimeError) as exc:
        print(f"  ⚠ Could not activate the cloudflared package: {exc}")
        _show_service_diagnostics()
        return False

    print("✓ Service started and enabled")
    return True


def show_tunnel_info(tunnel: JSONDict, sites: list[StrDict]):
    """Display tunnel configuration information."""
    print("\n" + "=" * 50)
    print("Tunnel Configuration Summary")
    print("=" * 50)
    print(f"Tunnel Name: {tunnel['name']}")
    print(f"Tunnel ID: {tunnel['id']}")
    print(f"\nConfigured Sites ({len(sites)}):")
    for site in sites:
        print(f"  • {site['hostname']} -> {site['service']}")
    print()


def _prompt_hostnames() -> list[str]:
    """Ask the operator to name the entire manually selected hostname set."""
    while True:
        value = setup_input("Public HTTP hostnames to publish (comma-separated, blank for none): ")
        try:
            return _normalize_hostnames([name.strip() for name in value.split(",")] if value.strip() else [])
        except ValueError as exc:
            print(f"✗ {exc}")


def main(
    interactive: bool = True, auto_update: bool = False, tunnel_name: Optional[str] = None,
    *, allow_direct: bool | None = None, access_sources: list[str] | None = None,
    managed_hostnames: list[str] | None = None, hostnames: list[str] | None = None,
) -> bool:
    """Create or refresh a tunnel using only operator-selected origin hostnames."""
    check_root()
    state = load_state()
    if not state and not interactive:
        return False

    old_policy = {
        "allow_direct": state.get("allow_direct", False) if state else False,
        "access_sources": state.get("access_sources", []) if state else [],
    }
    if allow_direct is None:
        allow_direct = old_policy["allow_direct"]
    if not isinstance(allow_direct, bool):
        raise ValueError("Cloudflare direct access must be a boolean")
    if access_sources is None:
        access_sources = old_policy["access_sources"]
    policy = {"allow_direct": allow_direct, "access_sources": normalize_access_sources(access_sources)}

    if state and interactive and not auto_update:
        print(f"\nExisting tunnel: {state['tunnel']['name']}")
        while True:
            choice = setup_input("1. Update selected routes  2. Create a new tunnel  3. Exit: ").strip()
            if choice == "3":
                return False
            if choice in ("1", "2"):
                break
        if choice == "2":
            # Retain the approved selection when replacing the tunnel identity.
            if managed_hostnames is None:
                managed_hostnames = state.get("managed_hostnames")
            if hostnames is None:
                hostnames = state.get("hostnames")
            state = None

    if hostnames is None and (
        (state is None and not managed_hostnames)
        or (state is not None and "hostnames" not in state and not managed_hostnames)
    ):
        if not interactive or auto_update:
            raise ValueError(
                "Review the legacy tunnel routes and select its public hostnames with "
                "setup-cloudflare-tunnel --non-interactive --hostname DOMAIN, or rerun managed setup"
            )
        hostnames = _prompt_hostnames()

    managed = _normalize_hostnames(
        managed_hostnames if managed_hostnames is not None else (state or {}).get("managed_hostnames", [])
    )
    explicit = _normalize_hostnames(hostnames if hostnames is not None else (state or {}).get("hostnames", []))
    requested = set(managed) | set(explicit)
    enabled = enabled_nginx_hostnames() if requested else set()
    if hostnames is not None and (missing := set(explicit) - enabled):
        raise ValueError("No enabled Nginx HTTP origin for: " + ", ".join(sorted(missing)))
    if missing := requested - enabled:
        print("  ⚠ Omitting hostnames without an enabled Nginx HTTP origin: " + ", ".join(sorted(missing)))
    selection = {
        "managed_hostnames": sorted(set(managed) & enabled),
        "hostnames": sorted(set(explicit) & enabled),
    }
    sites = [{"hostname": hostname, "service": ORIGIN_SERVICE} for hostname in sorted(requested & enabled)]
    if not state and not sites:
        raise ValueError("Select at least one enabled Nginx HTTP hostname to create a tunnel")

    if state and not os.path.isfile(state["tunnel"]["credentials_file"]):
        raise ValueError(f"Tunnel credentials are missing: {state['tunnel']['credentials_file']}")
    install_cloudflared()
    if state:
        tunnel = state["tunnel"]
    else:
        authenticate_cloudflare()
        while True:
            default = f" [{tunnel_name}]" if tunnel_name else ""
            chosen_name = setup_input(f"\nEnter tunnel name{default}: ").strip() or tunnel_name
            try:
                validate_cloudflare_tunnel_name(chosen_name)
            except ValueError as exc:
                print(f"✗ {exc}")
                continue
            break
        tunnel = create_tunnel(chosen_name)
        # A failed first run must be able to resume the already created tunnel.
        save_state(tunnel, [], pending_activation=True, **selection, **policy)

    if interactive and not auto_update:
        show_tunnel_info(tunnel, sites)
        if setup_input("Apply these routes and start the tunnel service? (y/n): ").strip().lower() != "y":
            return False

    config_file = f"{CONFIG_DIR}/config.yml"
    config_content = generate_config_yml(tunnel, sites)
    old_sites = state["sites"] if state else []
    config_needs_update = (
        not state or state.get("pending_activation", False)
        or old_sites != sites or not _config_is_current(config_file, config_content)
    )
    if config_needs_update:
        if state:
            # Record the route activation obligation with the last applied
            # firewall policy. New firewall settings commit only after activation.
            save_state(tunnel, old_sites, pending_activation=True, **selection, **old_policy)
        _write_config_file(config_file, config_content)
    if not install_and_start_service(restart=config_needs_update):
        if interactive:
            raise RuntimeError("cloudflared did not become active; the origin firewall policy was retained")
        return False
    _configure_firewall(**policy)
    if config_needs_update or any(
        state.get(key) != value for key, value in (selection | policy).items()
    ):
        save_state(tunnel, sites, **selection, **policy)
    if interactive:
        print("\nTunnel active. Configure DNS in Cloudflare for the selected hostnames.")
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Cloudflare Tunnel Setup")
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run in non-interactive mode (only updates existing tunnels)')
    parser.add_argument('--auto-update', action='store_true',
                       help='Automatically update existing tunnel without prompts')
    parser.add_argument('--tunnel-name', type=validate_cloudflare_tunnel_name,
                       help='Suggested tunnel name for initial creation')
    parser.add_argument('--allow-direct', action=argparse.BooleanOptionalAction, default=None,
                        help='Allow unrestricted direct HTTP/HTTPS alongside the tunnel; otherwise preserve saved policy')
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument('--access-source', dest='access_sources', action='append',
                              help='Allow direct HTTP/HTTPS from this IP/CIDR; repeat to replace saved web sources')
    source_group.add_argument('--clear-access-sources', dest='access_sources', action='store_const', const=[],
                              help='Clear saved direct-access sources')
    hostname_group = parser.add_mutually_exclusive_group()
    hostname_group.add_argument('--hostname', dest='hostnames', action='append', type=normalize_hostname,
                                help='Select a public HTTP hostname; repeat for the complete manual list')
    hostname_group.add_argument('--clear-hostnames', dest='hostnames', action='store_const', const=[],
                                help='Remove all manually selected public hostnames')
    managed_group = parser.add_mutually_exclusive_group()
    managed_group.add_argument('--managed-hostname', dest='managed_hostnames', action='append', help=argparse.SUPPRESS)
    managed_group.add_argument('--clear-managed-hostnames', dest='managed_hostnames', action='store_const',
                               const=[], help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    
    try:
        from fcntl import LOCK_EX, LOCK_NB, flock
        from lib.setup_directories import directory_fd

        check_root()
        with directory_fd(CONFIG_DIR, create=True) as config_directory:
            try:
                flock(config_directory, LOCK_EX | LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Cloudflare tunnel setup is active") from exc
            success = main(
                interactive=not args.non_interactive, auto_update=args.auto_update, tunnel_name=args.tunnel_name,
                allow_direct=args.allow_direct, access_sources=args.access_sources,
                managed_hostnames=args.managed_hostnames, hostnames=args.hostnames,
            )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n✗ Setup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
