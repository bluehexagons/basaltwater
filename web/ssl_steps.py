"""SSL/TLS and Let's Encrypt certificate management."""

from __future__ import annotations
import os
import shlex
from typing import Optional

from lib.atomic_io import write_text_atomic
from lib.nginx_config import certificate_is_usable
from lib.types import StrList, Deployments

from lib.config import SetupConfig
from lib.remote_utils import run, install_package


CERTBOT_NGINX_DEPLOY_HOOK = (
    "/etc/letsencrypt/renewal-hooks/deploy/50-basaltwater-reload-nginx"
)


def _certificate_is_usable(cert_path: str, key_path: str, domains: StrList) -> bool:
    """Return whether a certificate is current, matches its key, and covers every domain."""
    return certificate_is_usable(cert_path, key_path, domains)


def install_certbot(config: SetupConfig) -> None:
    print("Installing certbot...")
    run("apt-get update -qq", check=False)
    install_package("certbot", "certbot", "apt-get install -y -qq certbot python3-certbot-nginx")


def obtain_letsencrypt_certificate(domains: StrList, email: Optional[str] = None, cert_name: Optional[str] = None) -> bool:
    """
    Obtain a Let's Encrypt certificate for multiple domains using SANs.
    Let's Encrypt allows up to 100 Subject Alternative Names per certificate.
    """
    if not domains:
        return False
    
    if cert_name is None:
        cert_name = domains[0]
    
    cert_path = f"/etc/letsencrypt/live/{cert_name}/fullchain.pem"
    key_path = f"/etc/letsencrypt/live/{cert_name}/privkey.pem"
    complete_existing_certificate = os.path.exists(cert_path) and os.path.exists(key_path)
    if complete_existing_certificate and _certificate_is_usable(cert_path, key_path, domains):
        print(f"  Certificate '{cert_name}' already exists and is valid, skipping...")
        return True
    if os.path.exists(cert_path) or os.path.exists(key_path):
        print(f"  ⚠ Certificate '{cert_name}' is incomplete or invalid; requesting replacement")
    
    print(f"  Obtaining Let's Encrypt certificate for {len(domains)} domain(s): {', '.join(domains)}")
    
    run("mkdir -p /var/www/letsencrypt/.well-known/acme-challenge")
    
    cmd_parts = [
        "certbot certonly",
        "--webroot",
        "-w /var/www/letsencrypt",
        "--non-interactive",
        "--agree-tos",
    ]
    
    cmd_parts.append(f"--cert-name {shlex.quote(cert_name)}")
    if complete_existing_certificate:
        cmd_parts.append("--force-renewal")
    
    for domain in domains:
        cmd_parts.append(f"-d {shlex.quote(domain)}")
    
    if email:
        cmd_parts.append(f"--email {shlex.quote(email)}")
    else:
        cmd_parts.append("--register-unsafely-without-email")
    
    cmd = " ".join(cmd_parts)
    
    result = run(cmd, check=False)
    
    if result.returncode == 0:
        if _certificate_is_usable(cert_path, key_path, domains):
            print(f"  ✓ Certificate '{cert_name}' obtained")
            return True
        print(f"  ⚠ Certificate '{cert_name}' failed post-issuance validation")
        return False
    else:
        print(f"  ⚠ Failed to obtain certificate '{cert_name}'")
        return False


def create_domain_cert_links(domains: list[str], cert_name: str) -> None:
    """Create symlinks for each domain to shared certificate for easy SSL status checking."""
    cert_dir = f"/etc/letsencrypt/live/{cert_name}"
    
    if not os.path.exists(cert_dir):
        print(f"  ⚠ Certificate directory {cert_dir} not found")
        return
    
    for domain in domains:
        if domain == cert_name:
            continue
        
        link_path = f"/etc/letsencrypt/live/{domain}"
        
        if os.path.exists(link_path) or os.path.islink(link_path):
            if os.path.islink(link_path):
                target = os.readlink(link_path)
                if target == cert_name:
                    continue
                target_abs = os.path.join(os.path.dirname(link_path), target) if not os.path.isabs(target) else target
                if os.path.normpath(target_abs) == os.path.normpath(cert_dir):
                    continue
            else:
                print(f"  ⚠ {link_path} exists but is not a symlink, skipping")
                continue
        
        try:
            os.symlink(cert_name, link_path)
            print(f"  ✓ Created symlink: {domain} -> {cert_name}")
        except Exception as e:
            print(f"  ⚠ Failed to create symlink for {domain}: {e}")


def setup_certificate_renewal() -> None:
    print("  Setting up automatic certificate renewal...")

    run("mkdir -p /etc/letsencrypt/renewal-hooks/deploy")
    write_text_atomic(
        CERTBOT_NGINX_DEPLOY_HOOK,
        """#!/bin/sh
set -eu
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
""",
        mode=0o755,
    )
    enable_result = run("systemctl enable certbot.timer", check=False)
    start_result = run("systemctl start certbot.timer", check=False)
    if enable_result.returncode != 0 or start_result.returncode != 0:
        raise RuntimeError("Failed to enable automatic certificate renewal")
    
    print("  ✓ Automatic renewal configured")


def setup_ssl_for_deployments(
    deployments: Deployments,
    email: Optional[str] = None,
    enable_https_redirect: bool = True,
) -> None:
    """
    Set up Let's Encrypt SSL using SANs for all deployed domains.
    Requests single certificate covering all domains for efficiency.
    """
    print("\n" + "=" * 60)
    print("Setting up Let's Encrypt SSL certificates...")
    print("=" * 60)
    
    domains: set[str] = set()
    for dep in deployments:
        domain = dep.get('domain')
        if domain:
            domains.add(domain)
            
            if dep.get('backend_port') and (dep.get('frontend_port') or dep.get('frontend_serve_path')):
                if not dep.get('path') or dep.get('path') == '/':
                    domains.add(f"api.{domain}")
    
    # grouped deployments for nginx generation
    grouped_deployments: dict[Optional[str], Deployments] = {}
    for dep in deployments:
        key = dep.get('domain')
        grouped_deployments.setdefault(key, []).append(dep)
    
    if not domains:
        print("  No domains to configure SSL for (only local path deployments)")
        return
    
    domain_list = sorted(domains)
    
    print(f"  Requesting certificate for {len(domain_list)} domain(s) using Subject Alternative Names")
    
    cert_name = domain_list[0]
    
    if len(domain_list) > 100:
        print(f"  ⚠ Warning: {len(domain_list)} domains exceeds Let's Encrypt limit of 100 SANs")
        print(f"  ⚠ Only the first 100 domains will be included in the certificate")
        domain_list = domain_list[:100]
    
    success = obtain_letsencrypt_certificate(domain_list, email, cert_name)
    
    if not success:
        raise RuntimeError("Failed to obtain a trusted certificate for deployments")
    
    print(f"  Creating symbolic links for domain certificate references...")
    create_domain_cert_links(domain_list, cert_name)
    
    print("\n  Regenerating nginx configurations to use Let's Encrypt certificates...")
    
    from lib.nginx_config import create_nginx_sites_for_groups
    
    grouped_deployments: dict[Optional[str], Deployments] = {}
    for dep in deployments:
        key = dep.get('domain')
        grouped_deployments.setdefault(key, []).append(dep)
    
    # Create nginx sites from grouped deployments
    create_nginx_sites_for_groups(grouped_deployments, enable_https_redirect=enable_https_redirect)
    
    setup_certificate_renewal()
    
    print("  ✓ SSL setup complete")
    print(f"  ✓ Single certificate covers all {len(domain_list)} domain(s)")
