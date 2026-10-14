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
import glob
import ipaddress
import re
import shlex
import shutil
import tempfile
from typing import Optional, Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '../..'))

from lib.types import StrDict, JSONDict
from lib.atomic_io import write_json_atomic, write_text_atomic
from lib.machine_state import can_manage_firewall
from lib.remote_utils import CommandTimeoutError, run as run_bounded_command
from lib.validators import validate_host
from lib.validation import validate_cloudflare_tunnel_name
from lib.setup_prompts import setup_input
from web.cloudflare_firewall import normalize_access_sources, reconcile_tunnel_firewall


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
    """Install cloudflared from Cloudflare's signed APT repository."""
    if shutil.which('cloudflared'):
        print("✓ cloudflared already installed")
        return

    print("Installing cloudflared...")

    # The old release download used a floating URL and wrote into the current
    # directory before invoking dpkg.  Use Cloudflare's signed repository so
    # APT verifies the package and no attacker-controlled working directory is
    # involved.
    run_command(["apt-get", "update"])
    run_command(["apt-get", "install", "-y", "ca-certificates", "curl"])
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
    run_command(["apt-get", "update"])
    run_command(["apt-get", "install", "-y", "cloudflared"])
    print("✓ cloudflared installed successfully")


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


def _nginx_hostnames(content: str) -> list[str]:
    """Read server_name directives while respecting Nginx comments and quotes."""
    lexer = shlex.shlex(content, posix=True, punctuation_chars=";{}")
    lexer.whitespace_split = True
    directive: list[str] = []
    hostnames: list[str] = []
    for token in lexer:
        if all(char in ";{}" for char in token):
            for separator in token:
                if separator == ";" and directive[:1] == ["server_name"]:
                    hostnames.extend(directive[1:])
                directive.clear()
        else:
            directive.append(token)
    return hostnames


def discover_nginx_sites() -> list[StrDict]:
    """Discover sites from nginx configuration."""
    sites: list[StrDict] = []
    seen: set[tuple[str, str]] = set()
    
    if not os.path.exists(NGINX_SITES_DIR):
        print(f"  ⚠ Nginx sites directory not found: {NGINX_SITES_DIR}")
        return sites
    
    for config_file in glob.glob(f"{NGINX_SITES_DIR}/*"):
        if os.path.islink(config_file):
            target = os.readlink(config_file)
            if not os.path.isabs(target):
                target = os.path.join(os.path.dirname(config_file), target)
        else:
            target = config_file
        
        if not os.path.exists(target):
            continue
        
        try:
            with open(target, 'r') as f:
                content = f.read()
            
            for domain in _nginx_hostnames(content):
                if not domain or domain == '_' or domain.startswith('*'):
                    continue
                if not validate_host(domain):
                    print(f"  ⚠ Skipping invalid Nginx hostname: {domain}")
                    continue
                if domain.lower() == "localhost":
                    continue
                try:
                    ipaddress.ip_address(domain)
                except ValueError:
                    pass
                else:
                    continue
                site = {
                    'hostname': domain,
                    'service': 'http://localhost:80'
                }
                site_key = (domain.lower(), site['service'])
                if site_key not in seen:
                    sites.append(site)
                    seen.add(site_key)
        except Exception as e:
            print(f"  ⚠ Error reading {target}: {e}")
    
    return sites


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
        if not isinstance(hostname, str) or not validate_host(hostname):
            raise ValueError(f"Invalid tunnel hostname: {hostname!r}")
        if service != 'http://localhost:80':
            raise ValueError(f"Unsupported tunnel origin service: {service!r}")
        config_lines.append(f"  - hostname: {json.dumps(hostname)}")
        config_lines.append(f"    service: {json.dumps(service)}")
    
    config_lines.append("  - service: http_status:404")
    config_lines.append("")
    
    return "\n".join(config_lines)


def save_state(
    tunnel: JSONDict, sites: list[StrDict], *, pending_activation: bool = False,
    allow_direct: bool = False, access_sources: list[str] | None = None,
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
        if site.get('service') != 'http://localhost:80':
            raise ValueError("Cloudflare tunnel state contains an unsupported origin")
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


def _config_is_valid(config_file: str) -> bool:
    """Return whether an existing config still passes cloudflared validation."""
    if not os.path.isfile(config_file):
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
    access_sources = normalize_access_sources(access_sources)
    if not can_manage_firewall():
        print("  ✓ Skipping Cloudflare firewall policy (machine cannot manage UFW)")
        return
    if not shutil.which("ufw"):
        run_command(["apt-get", "update"])
        run_command(["apt-get", "install", "-y", "ufw"])

    run_command(["ufw", "default", "deny", "incoming"])
    run_command(["ufw", "default", "allow", "outgoing"])
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


def main(
    interactive: bool = True, auto_update: bool = False, tunnel_name: Optional[str] = None,
    *, allow_direct: bool | None = None, access_sources: list[str] | None = None,
):
    """
    Main setup workflow.
    
    Args:
        interactive: If False, runs in non-interactive mode (auto-update only)
        auto_update: If True, automatically updates existing tunnel config
    """
    if interactive:
        print("=" * 50)
        print("Cloudflare Tunnel Setup")
        print("=" * 50)
        print()
    
    check_root()

    state = load_state()
    if allow_direct is None:
        allow_direct = state.get('allow_direct', False) if state else False
    if not isinstance(allow_direct, bool):
        raise ValueError("Cloudflare direct access must be a boolean")
    if access_sources is None:
        access_sources = state.get('access_sources', []) if state else []
    access_sources = normalize_access_sources(access_sources)
    policy = {'allow_direct': allow_direct, 'access_sources': access_sources}
    if not interactive:
        if not state:
            return False

    install_cloudflared()

    if not interactive:
        tunnel = state['tunnel']
        sites = discover_nginx_sites()
        if not sites:
            return False
        if not os.path.isfile(tunnel['credentials_file']):
            raise ValueError(f"Tunnel credentials are missing: {tunnel['credentials_file']}")

        old_sites = state.get('sites', [])
        old_site_keys = {
            (site['hostname'].lower(), site['service'])
            for site in old_sites
        }
        new_site_keys = {
            (site['hostname'].lower(), site['service'])
            for site in sites
        }
        config_file = f"{CONFIG_DIR}/config.yml"
        config_needs_update = (
            state.get('pending_activation', False)
            or old_site_keys != new_site_keys
            or not _config_is_valid(config_file)
        )

        if config_needs_update:
            # A config repair can require activation even when its route set
            # is unchanged. Persist that obligation before replacing the file.
            save_state(tunnel, old_sites, pending_activation=True, **policy)
            config_content = generate_config_yml(tunnel, sites)
            _write_config_file(config_file, config_content)
        # Commit discovered routes only after the daemon has loaded them. If
        # restart fails, pending_activation makes the next refresh retry it.
        if not install_and_start_service(restart=config_needs_update):
            return False
        _configure_firewall(**policy)
        if config_needs_update or any(state.get(key, default) != value for key, default, value in (
            ('allow_direct', False, allow_direct), ('access_sources', [], access_sources),
        )):
            save_state(tunnel, sites, **policy)
        return True
    
    # Interactive mode falls through to show configuration and return True on success
    if state:
        print("\n✓ Found existing tunnel configuration")
        tunnel = state['tunnel']
        
        print(f"  Tunnel: {tunnel['name']} (ID: {tunnel['id']})")
        
        if auto_update:
            choice = '1'
        else:
            print("\nOptions:")
            print("  1. Update tunnel configuration (discover new sites)")
            print("  2. Create a new tunnel")
            print("  3. Exit")
            
            while True:
                choice = setup_input("\nEnter choice (1-3): ").strip().lower()
                
                if choice in ['3', 'exit', 'quit', 'q']:
                    print("Exiting...")
                    return False
                elif choice in ['2', 'new']:
                    state = None
                    break
                elif choice in ['1', 'update']:
                    break
                else:
                    print("✗ Invalid choice. Please enter 1, 2, or 3.")
        
        if choice in ['1', 'update']:
            pass  # Continue with tunnel from state
        else:
            state = None
    
    if not state:
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
        # Preserve a newly created tunnel even if service installation is
        # deferred or fails. Empty applied routes force the next refresh to
        # restart with the generated ingress rather than trusting stale state.
        save_state(tunnel, [], pending_activation=True, **policy)
    else:
        tunnel = state['tunnel']
        print(f"\n✓ Using existing tunnel: {tunnel['name']}")
    
    print("\nDiscovering configured sites from nginx...")
    sites = discover_nginx_sites()
    
    if not sites:
        print("  ⚠ No sites discovered from nginx configuration")
        print("\nYou can manually add sites to the configuration.")
        
        manual = setup_input("Add a site manually? (y/n): ").strip().lower()
        if manual == 'y':
            while True:
                hostname = setup_input("Enter hostname (e.g., example.com): ").strip()
                if not hostname:
                    break
                if validate_host(hostname):
                    sites.append({
                        'hostname': hostname,
                        'service': 'http://localhost:80'
                    })
                    break
                print("✗ Invalid hostname; enter a DNS hostname such as example.com")
    
    if not sites:
        print("\n✗ No sites configured. Exiting.")
        sys.exit(1)
    
    print(f"\n✓ Found {len(sites)} site(s) to configure")
    
    config_content = generate_config_yml(tunnel, sites)
    config_file = f"{CONFIG_DIR}/config.yml"
    
    print(f"\nWriting configuration to {config_file}...")
    if state:
        save_state(tunnel, state['sites'], pending_activation=True, **policy)
    _write_config_file(config_file, config_content)
    print("✓ Configuration file created")
    
    show_tunnel_info(tunnel, sites)
    
    proceed = setup_input("Install and start the tunnel service? (y/n): ").strip().lower()
    
    if proceed == 'y':
        if not install_and_start_service(restart=True):
            raise RuntimeError(
                "cloudflared did not become active; direct HTTP/HTTPS ports remain open"
            )
        _configure_firewall(**policy)
        save_state(tunnel, sites, **policy)
        
        print("\n" + "=" * 50)
        print("Setup Complete!")
        print("=" * 50)
        print("\nNext steps:")
        print("1. Go to Cloudflare Zero Trust dashboard")
        print("2. Configure DNS for your domains to point to the tunnel")
        print("3. Monitor tunnel status: systemctl status cloudflared")
        print()
        return True
    else:
        print("\n✓ Configuration saved")
        print(f"  Config file: {config_file}")
        print("\nTo start the tunnel later, run:")
        print("  cloudflared service install")
        print("  systemctl start cloudflared")
        print("  systemctl enable cloudflared")
        return False


def run_non_interactive_update() -> bool:
    """
    Run tunnel configuration update in non-interactive mode.
    Only updates existing tunnel configurations with newly discovered sites.
    Returns True if successful, False otherwise.
    """
    try:
        result = main(interactive=False)
        return bool(result)
    except Exception:
        return False


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
                              help='Allow this direct-access IP/CIDR; repeat as needed, otherwise preserve saved sources')
    source_group.add_argument('--clear-access-sources', dest='access_sources', action='store_const', const=[],
                              help='Clear saved direct-access sources')
    
    args = parser.parse_args()
    
    try:
        if args.non_interactive:
            success = main(interactive=False, allow_direct=args.allow_direct, access_sources=args.access_sources)
            sys.exit(0 if success else 1)
        else:
            success = main(
                interactive=True, auto_update=args.auto_update, tunnel_name=args.tunnel_name,
                allow_direct=args.allow_direct, access_sources=args.access_sources,
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
