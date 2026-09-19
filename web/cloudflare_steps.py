"""Cloudflare tunnel preconfiguration steps."""

from __future__ import annotations

import os
import shlex

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.remote_utils import run


NGINX_CLOUDFLARE_CONF = "/etc/nginx/conf.d/cloudflare.conf"
NGINX_CLOUDFLARE_CONF_DIR = "/etc/nginx/conf.d"


def _allow_antistatic_direct_access_for_cloudflare(config: SetupConfig) -> bool:
    """Preserve antistatic direct-access ports that Cloudflare tunnels cannot proxy."""
    if not config.antistatic_server:
        return False

    from game.antistatic_steps import (
        DEFAULT_STUN_PORT,
        get_antistatic_public_firewall_rules,
        parse_antistatic_spec,
    )

    domain, port = parse_antistatic_spec(config.antistatic_server)
    rules = get_antistatic_public_firewall_rules(domain, port)
    allowed_ports = ", ".join(f"{rule_port}/{protocol}" for rule_port, protocol, _ in rules)
    for rule_port, protocol, comment in rules:
        run(
            f"ufw allow {rule_port}/{protocol} comment {shlex.quote(comment)}",
            check=False,
        )
    print(f"  ✓ Preserved direct antistatic access: {allowed_ports}")
    print(
        "  ℹ Cloudflare tunnels do not proxy UDP; antistatic still needs "
        f"direct IP reachability on {DEFAULT_STUN_PORT}/udp"
    )
    return True


def configure_cloudflare_firewall(config: SetupConfig) -> None:
    """Configure firewall for Cloudflare tunnel and preserve required direct-access rules."""
    tunnel_status = run(
        "systemctl is-active --quiet cloudflared",
        check=False,
        capture_output=True,
    )
    if tunnel_status.returncode != 0:
        raise RuntimeError(
            "Refusing to close public HTTP/HTTPS ports before cloudflared is active"
        )

    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    if result.returncode != 0:
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq ufw")

    run("ufw default deny incoming")
    run("ufw default allow outgoing")
    # Remove the unrestricted rules a prior version created before applying
    # UFW's SSH rate limit.  This preserves the intended brute-force control.
    run("ufw delete allow ssh", check=False)
    run("ufw delete allow 22/tcp", check=False)
    run("ufw limit ssh")

    # Explicitly remove web ports if they were added by previous steps
    run("ufw delete allow 80/tcp", check=False)
    run("ufw delete allow 443/tcp", check=False)
    run("ufw delete allow 80", check=False)
    run("ufw delete allow 443", check=False)

    antistatic_direct_access = _allow_antistatic_direct_access_for_cloudflare(config)
    run("ufw --force enable")

    if antistatic_direct_access:
        print("  ✓ Firewall configured for Cloudflare tunnel with antistatic direct access")
    else:
        print("  ✓ Firewall configured for Cloudflare tunnel (SSH only)")


def create_cloudflared_config_directory(config: SetupConfig) -> None:
    """Create cloudflared configuration directory structure."""
    config_dir = "/etc/cloudflared"
    
    if os.path.exists(config_dir):
        print(f"  ✓ Cloudflared config directory already exists")
        return
    
    os.makedirs(config_dir, mode=0o755, exist_ok=True)
    
    config_template_dir = os.path.join(os.path.dirname(__file__), '..', 'web', 'config')
    template_path = os.path.join(config_template_dir, 'cloudflare_tunnel_readme.md')
    with open(template_path, 'r', encoding='utf-8') as f:
        readme_content = f.read()
    
    with open(os.path.join(config_dir, "README.md"), "w") as f:
        f.write(readme_content)
    
    print(f"  ✓ Created {config_dir} with setup instructions")


def configure_nginx_for_cloudflare(config: SetupConfig) -> None:
    """Configure nginx to trust Cloudflare IPs and use real visitor IPs."""
    del config
    cloudflare_conf = NGINX_CLOUDFLARE_CONF
    previous_config = None
    if os.path.exists(cloudflare_conf):
        with open(cloudflare_conf, 'r', encoding='utf-8') as f:
            previous_config = f.read()

    config_template_dir = os.path.join(os.path.dirname(__file__), '..', 'web', 'config')
    template_path = os.path.join(config_template_dir, 'cloudflare_ips.conf')
    with open(template_path, 'r', encoding='utf-8') as f:
        cloudflare_config = f.read()

    os.makedirs(NGINX_CLOUDFLARE_CONF_DIR, exist_ok=True)
    write_text_atomic(cloudflare_conf, cloudflare_config, mode=0o644)

    validation = run("nginx -t", check=False, capture_output=True)
    if validation.returncode != 0:
        if previous_config is None:
            os.unlink(cloudflare_conf)
        else:
            write_text_atomic(cloudflare_conf, previous_config, mode=0o644)
        detail = getattr(validation, "stderr", "") or getattr(validation, "stdout", "")
        raise RuntimeError(
            f"Nginx rejected the Cloudflare configuration: {detail.strip() or 'nginx -t failed'}"
        )

    reload_result = run("systemctl reload nginx", check=False, capture_output=True)
    if reload_result.returncode != 0:
        if previous_config is None:
            os.unlink(cloudflare_conf)
        else:
            write_text_atomic(cloudflare_conf, previous_config, mode=0o644)
        detail = getattr(reload_result, "stderr", "") or getattr(reload_result, "stdout", "")
        raise RuntimeError(
            f"Nginx could not reload the Cloudflare configuration: "
            f"{detail.strip() or 'reload failed'}"
        )

    print("  ✓ Nginx configured and reloaded to trust Cloudflare IPs")


def install_cloudflared_service_helper(config: SetupConfig) -> None:
    """Create symlink for Cloudflare tunnel setup script."""
    helper_script = "/usr/local/bin/setup-cloudflare-tunnel"
    source_script = "/opt/basaltwater/web/service_tools/setup_cloudflare_tunnel.py"
    
    if os.path.exists(helper_script):
        print("  ✓ Cloudflare tunnel setup script already available")
        return
    
    if not os.path.exists(source_script):
        print(f"  ⚠ Source script not found: {source_script}")
        return
    
    run(f"ln -sf {source_script} {helper_script}")
    
    print(f"  ✓ Linked setup script: {helper_script}")
    print(f"  Run 'sudo setup-cloudflare-tunnel' to configure the tunnel")


def run_cloudflare_tunnel_setup(config: SetupConfig) -> bool:
    """Update and verify an existing tunnel; return False when none exists."""
    del config
    helper_script = "/opt/basaltwater/web/service_tools/setup_cloudflare_tunnel.py"
    
    if not os.path.exists(helper_script):
        raise RuntimeError(f"Cloudflare setup script not found: {helper_script}")
    
    state_file = "/etc/cloudflared/tunnel-state.json"
    if not os.path.exists(state_file):
        print("  ⚠ No existing Cloudflare tunnel found")
        print("  Run 'sudo setup-cloudflare-tunnel' interactively to create a tunnel first")
        return False
    
    print("  Updating Cloudflare tunnel configuration...")
    
    result = run(
        f"python3 {shlex.quote(helper_script)} --non-interactive",
        check=False,
        capture_output=True,
    )
    
    if result.returncode == 0:
        print("  ✓ Cloudflare tunnel configuration updated")
        return True

    detail = getattr(result, "stderr", "") or getattr(result, "stdout", "")
    raise RuntimeError(
        "Cloudflare tunnel update or activation failed: "
        f"{detail.strip() or f'exit code {result.returncode}'}"
    )
