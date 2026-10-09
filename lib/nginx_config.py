"""Nginx configuration generator for deployed applications."""

from __future__ import annotations

import base64
import ipaddress
import os
import shlex
import shutil
import stat
import tempfile
from typing import Optional, Sequence

from lib.types import Deployments, StrList, PathPair
from lib.atomic_io import fsync_tree, remove_file_durable, write_bytes_atomic, write_json_atomic
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


SSL_PROTOCOLS = "TLSv1.2 TLSv1.3"
SSL_CIPHERS = "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384"
NGINX_SITES_AVAILABLE_DIR = "/etc/nginx/sites-available"
NGINX_SITES_ENABLED_DIR = "/etc/nginx/sites-enabled"
PRESERVED_SITE_PREFIXES = ("antistatic_", "gogs_")
GENERATED_CONFIG_MARKER = "# Managed by basaltwater deployment nginx generator"


def _config_name_for_domain(domain: Optional[str]) -> str:
    return domain.replace('.', '_') if domain else 'default'


def _remove_path(path: str) -> None:
    remove_file_durable(path)


def _read_config_file(path: str) -> tuple[bytes, int, int, int]:
    validate_filesystem_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Unsafe Nginx configuration file: {path}")
        content = handle.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError(f"Nginx configuration file exceeds 1 MiB: {path}")
    return content, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid


def _is_basaltwater_deployment_site(path: str) -> bool:
    read_path = os.path.realpath(path) if os.path.islink(path) else path
    if os.path.isdir(read_path):
        return False
    try:
        content = _read_config_file(read_path)[0].decode("utf-8")
    except (OSError, UnicodeError):
        return False

    if GENERATED_CONFIG_MARKER in content:
        return True

    # Legacy generated deployment configs predate the explicit marker. Keep the
    # heuristic narrow so unrelated nginx sites are not swept up.
    return all(token in content for token in (
        "map $uri $assets_expires_",
        "map $uri $assets_cc_",
        "location /.well-known/acme-challenge/",
        "add_header Strict-Transport-Security",
    ))


def _is_legacy_rails_site(config_name: str) -> bool:
    """Preserve an unsupported legacy Rails route owned by its old unit."""
    systemd_dir = "/etc/systemd/system"
    direct_unit = f"rails-{config_name}.service"
    subpath_prefix = f"rails-{config_name}__"
    try:
        return any(
            filename == direct_unit
            or (filename.startswith(subpath_prefix) and filename.endswith(".service"))
            for filename in os.listdir(systemd_dir)
        )
    except OSError:
        return False


def _assert_managed_config_names(current_config_names: set[str]) -> None:
    """Refuse to replace a same-named Nginx site not owned by basaltwater."""
    for directory in (NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR):
        for name in current_config_names:
            path = os.path.join(directory, name)
            if os.path.lexists(path) and not _is_basaltwater_deployment_site(path):
                raise RuntimeError(
                    f"Refusing to replace unmanaged Nginx configuration: {path}"
                )


def _reconcile_deployment_sites(current_config_names: set[str]) -> None:
    """Remove stale app deployment nginx sites before writing current ones.

    App deployments own unprefixed ``sites-available``/``sites-enabled`` names
    such as ``example_com`` and ``default``. Prefix-owned service configs (Gogs,
    antistatic) are managed by their own setup steps and must be left alone.
    """
    for directory in (NGINX_SITES_ENABLED_DIR, NGINX_SITES_AVAILABLE_DIR):
        if not os.path.isdir(directory):
            continue

        for name in os.listdir(directory):
            if (
                name in current_config_names
                or name.startswith(PRESERVED_SITE_PREFIXES)
                or _is_legacy_rails_site(name)
            ):
                continue

            path = os.path.join(directory, name)
            if not _is_basaltwater_deployment_site(path):
                continue

            try:
                _remove_path(path)
            except OSError as e:
                raise RuntimeError(f"Failed to remove stale nginx config {path}: {e}") from e
            else:
                print(f"  ✓ Removed stale nginx config: {path}")


def _snapshot_deployment_sites(current_config_names: set[str], extra_paths: tuple[str, ...]) -> dict[str, tuple[str, object]]:
    snapshot: dict[str, tuple[str, object]] = {}
    for directory in (NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if name not in current_config_names and not _is_basaltwater_deployment_site(path):
                continue
            if os.path.islink(path):
                info = os.lstat(path)
                snapshot[path] = ("symlink", (os.readlink(path), info.st_uid, info.st_gid))
            elif os.path.isfile(path):
                snapshot[path] = ("file", _read_config_file(path))
    for path in extra_paths:
        if os.path.islink(path):
            raise ValueError(f"Managed TLS file must not be a symlink: {path}")
        if os.path.lexists(path):
            snapshot[path] = ("file", _read_config_file(path))
    return snapshot


def _restore_deployment_sites(
    snapshot: dict[str, tuple[str, object]],
    current_config_names: set[str],
    extra_paths: tuple[str, ...],
) -> None:
    errors = []

    def attempt(path, action):
        try:
            action()
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}")

    desired_paths = {
        os.path.join(directory, name)
        for directory in (NGINX_SITES_ENABLED_DIR, NGINX_SITES_AVAILABLE_DIR)
        for name in current_config_names
    } | set(extra_paths)
    for path in sorted(desired_paths - snapshot.keys()):
        attempt(path, lambda path=path: _remove_path(path))
    # Restore regular files before enabling links; each file replacement is
    # durable and does not first delete its current contents.
    for path, (kind, value) in sorted(snapshot.items()):
        if kind == "file":
            content, mode, uid, gid = value
            attempt(path, lambda path=path, content=content, mode=mode, uid=uid, gid=gid:
                    write_bytes_atomic(path, content, mode=mode, uid=uid, gid=gid))
    for path, (kind, value) in sorted(snapshot.items()):
        if kind == "symlink":
            def restore_link(path=path, value=value):
                target, uid, gid = value
                _remove_path(path)
                os.symlink(target, path)
                os.lchown(path, uid, gid)
            attempt(path, restore_link)
    for directory in dict.fromkeys((NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR,
                                    *(os.path.dirname(path) for path in extra_paths))):
        if os.path.isdir(directory):
            attempt(directory, lambda directory=directory: fsync_tree(directory))
    if errors:
        raise RuntimeError("Nginx file restoration failed: " + "; ".join(errors))


def _self_signed_cert_path(name: str) -> PathPair:
    return (f"/etc/nginx/ssl/{name}.crt", f"/etc/nginx/ssl/{name}.key")


def certificate_is_usable(
    cert_path: str,
    key_path: str,
    identities: Sequence[str],
    *,
    minimum_valid_seconds: int = 86_400,
) -> bool:
    """Return whether a certificate is current, matched, and covers identities."""
    quoted_cert = shlex.quote(cert_path)
    quoted_key = shlex.quote(key_path)
    current = run(
        f"openssl x509 -checkend {minimum_valid_seconds} -noout -in {quoted_cert}",
        check=False,
        capture_output=True,
    )
    if current.returncode != 0:
        return False
    for identity in identities:
        try:
            ipaddress.ip_address(identity)
        except ValueError:
            check_option = "-checkhost"
        else:
            check_option = "-checkip"
        identity_result = run(
            f"openssl x509 -noout {check_option} {shlex.quote(identity)} "
            f"-in {quoted_cert}",
            check=False,
            capture_output=True,
        )
        if identity_result.returncode != 0:
            return False
    cert_digest = run(
        f"openssl x509 -in {quoted_cert} -pubkey -noout | "
        "openssl pkey -pubin -outform DER | openssl sha256",
        check=False,
        capture_output=True,
    )
    key_digest = run(
        f"openssl pkey -in {quoted_key} -pubout -outform DER | openssl sha256",
        check=False,
        capture_output=True,
    )
    return (
        cert_digest.returncode == 0
        and key_digest.returncode == 0
        and bool(cert_digest.stdout.strip())
        and cert_digest.stdout.strip() == key_digest.stdout.strip()
    )


def get_ssl_cert_path(domain: Optional[str]) -> PathPair:
    """Get SSL certificate paths, preferring Let's Encrypt over self-signed."""
    cert_name = domain or 'default'
    
    if domain:
        letsencrypt_cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        letsencrypt_key = f"/etc/letsencrypt/live/{domain}/privkey.pem"
        if (
            os.path.exists(letsencrypt_cert)
            and os.path.exists(letsencrypt_key)
            and certificate_is_usable(
                letsencrypt_cert,
                letsencrypt_key,
                [domain],
                minimum_valid_seconds=0,
            )
        ):
            return (letsencrypt_cert, letsencrypt_key)
    
    return _self_signed_cert_path(cert_name)


def generate_self_signed_cert(
    domain: str,
    additional_identities: Sequence[str] = (),
) -> PathPair:
    """Generate self-signed SSL certificate for a domain."""
    cert_file, key_file = _self_signed_cert_path(domain)
    identities = list(dict.fromkeys((domain, *additional_identities)))

    if (
        os.path.exists(cert_file)
        and os.path.exists(key_file)
        and certificate_is_usable(
            cert_file,
            key_file,
            identities,
            minimum_valid_seconds=30 * 24 * 60 * 60,
        )
    ):
        return (cert_file, key_file)
    
    cert_dir = os.path.dirname(cert_file)
    run(f"mkdir -p {cert_dir}")
    subject_alt_names: list[str] = []
    for identity in identities:
        try:
            ipaddress.ip_address(identity)
        except ValueError:
            subject_alt_names.append(f"DNS:{identity}")
        else:
            subject_alt_names.append(f"IP:{identity}")
    subject_alt_name = ",".join(subject_alt_names)
    run(f"openssl req -x509 -nodes -days 365 -newkey rsa:2048 "
             f"-keyout {shlex.quote(key_file)} -out {shlex.quote(cert_file)} "
             f"-subj {shlex.quote(f'/CN={domain}')} "
             f"-addext {shlex.quote(f'subjectAltName={subject_alt_name}')}")
    if not certificate_is_usable(cert_file, key_file, identities):
        raise RuntimeError(f"Generated TLS certificate failed validation: {cert_file}")

    return (cert_file, key_file)


def _make_cache_maps(domain_slug: str) -> tuple[str, str, str]:
    """Generate map blocks for caching and return variable names."""
    expires_var = f"$assets_expires_{domain_slug}"
    cc_var = f"$assets_cc_{domain_slug}"
    
    maps = fr"""
map $uri {expires_var} {{
    default                    off;
    ~*\.(jpg|jpeg|png|gif|webp|svg|ico)$  1y;
    ~*\.(mp4|webm|ogg|mov|avi|flv|wmv)$   1y;
    ~*\.(woff|woff2|ttf|eot|otf)$         1y;
    ~*\.(css|js)$                         1y;
    ~*\.(pdf|txt|xml|json)$               30d;
}}

map $uri {cc_var} {{
    default                    "";
    ~*\.(jpg|jpeg|png|gif|webp|svg|ico)$  "public, immutable";
    ~*\.(mp4|webm|ogg|mov|avi|flv|wmv)$   "public, immutable";
    ~*\.(woff|woff2|ttf|eot|otf)$         "public, immutable";
    ~*\.(css|js)$                         "public, immutable";
    ~*\.(pdf|txt|xml|json)$               "public";
}}
"""
    return maps, expires_var, cc_var


def _make_proxy_location(path: str, port: int, comment: str, enable_websocket: bool = False,
                        expires_var: Optional[str] = None, cc_var: Optional[str] = None,
                        forwarded_proto: str = "$scheme", enable_path_redirect: bool = True,
                        preserve_path: bool = False) -> str:
    """Generate a proxy_pass location block."""
    slash = "" if preserve_path or path == "/" else "/"
    
    content = [
        f"        proxy_pass http://127.0.0.1:{port}{slash};",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        f"        proxy_set_header X-Forwarded-Proto {forwarded_proto};",
        "",
        "        # Performance optimizations for dynamic backends",
        "        proxy_buffering on;",
        "        proxy_intercept_errors off;"
    ]
    
    if enable_websocket:
        content.extend([
            "",
            "        # WebSocket support for Vite HMR",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection \"upgrade\";"
        ])
    else:
        content.extend([
            "",
            "        # Keepalive for backend connections",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Connection \"\";"
        ])

    if expires_var:
        content.append(f"        expires {expires_var};")
    if cc_var:
        content.append(f"        add_header Cache-Control {cc_var};")
        
    body = "\n".join(content)
    
    if path == "/":
        return f"""    {comment}
    location / {{
{body}
    }}"""
    else:
        exact_location = f"""    # Redirect {path} to {path}/
    location = {path} {{
        return 301 {path}/;
    }}""" if enable_path_redirect else f"""    # Proxy exact {path} without redirect
    location = {path} {{
{body}
    }}"""
        return f"""    {comment}
    location {path}/ {{
{body}
    }}

{exact_location}"""


def _make_static_location(path: str, serve_path: str, index_file: str, try_files: str, comment: str,
                         expires_var: Optional[str] = None, cc_var: Optional[str] = None) -> str:
    """Generate a static file serving location block."""
    directive = "root" if path == "/" else "alias"
    
    content = [
        f"        {directive} {serve_path};",
        f"        index {index_file};",
        "        autoindex off;",
        "        charset utf-8;",
        f"        try_files {try_files};"
    ]

    if expires_var:
        content.append(f"        expires {expires_var};")
    if cc_var:
        content.append(f"        add_header Cache-Control {cc_var};")

    body = "\n".join(content)
    
    return f"""    {comment}
    location {path} {{
{body}
    }}"""


def _make_godot_location(path: str, serve_path: str) -> str:
    """Serve a complete Godot export without SPA fallback or immutable caching."""
    from lib.cicd_deploy_policy import validate_nginx_path

    validate_nginx_path(path)
    validate_nginx_path(serve_path)
    prefix = "/" if path == "/" else path.rstrip("/") + "/"
    directive = "root" if prefix == "/" else "alias"
    directory = serve_path.rstrip("/") + "/"
    redirect = "" if prefix == "/" else f"""    location = {path.rstrip('/')} {{
        return 301 {prefix};
    }}
"""
    # No nested add_header blocks: isolation must cover HTML, workers and errors.
    # Do not use ^~: the server's dotfile deny regex must still win.
    return redirect + f"""    # Godot web export: fixed filenames must revalidate together
    location {prefix} {{
        {directive} {directory};
        index index.html;
        autoindex off;
        disable_symlinks on;
        types {{
            text/html html;
            application/javascript js;
            application/wasm wasm;
            application/json json;
            application/manifest+json webmanifest;
            image/png png;
            image/svg+xml svg;
            text/css css;
            text/plain txt md;
        }}
        default_type application/octet-stream;
        expires off;
        add_header Cache-Control "no-cache" always;
        add_header Cross-Origin-Opener-Policy "same-origin" always;
        add_header Cross-Origin-Embedder-Policy "require-corp" always;
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    }}"""


def generate_merged_nginx_config(
    domain: Optional[str],
    deployments: Deployments,
    is_default: bool = False,
    enable_https_redirect: bool = True,
    disable_symlinks: bool = False,
) -> str:
    """Generate a merged nginx configuration for multiple deployments on the same domain."""
    from lib.cicd_deploy_policy import validate_nginx_path

    cert_file, key_file = get_ssl_cert_path(domain)
    server_name_directive = f"server_name {domain};" if domain else "server_name _;"
    default_server = " default_server" if is_default else ""
    symlink_policy = '\n    disable_symlinks on;' if disable_symlinks else ''
    
    domain_slug = _config_name_for_domain(domain)
    cache_maps, expires_var, cc_var = _make_cache_maps(domain_slug)
    forwarded_proto = "https" if not enable_https_redirect else "$scheme"
    enable_path_redirect = enable_https_redirect
    
    sorted_deployments = sorted(deployments, key=lambda d: len(d['path']), reverse=True)
    
    locations: StrList = []
    
    locations.append("""    location /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }""")
    
    for dep in sorted_deployments:
        path = dep['path']
        validate_nginx_path(path)
        location_path = path.rstrip('/') if path != '/' else '/'
        
        if dep['needs_proxy']:
            backend_port = dep.get('backend_port')
            proxy_port = dep.get('proxy_port') or backend_port or 3000
            locations.append(_make_proxy_location(
                location_path, proxy_port, f"# Proxy for {path}",
                expires_var=expires_var, cc_var=cc_var,
                forwarded_proto=forwarded_proto, enable_path_redirect=enable_path_redirect,
                preserve_path=dep.get('preserve_path', False)
            ))
        else:
            serve_path = dep['serve_path']
            validate_nginx_path(serve_path)
            if dep.get('project_type') == 'godot-web':
                locations.append(_make_godot_location(location_path, serve_path))
                continue
            index_file = "index.html index.htm"
            project_type = dep.get('project_type', 'static')
            
            try_files = "$uri $uri.html $uri.htm $uri/ =404"
            if project_type == 'node':
                 # Assume SPA
                 if location_path == '/':
                     try_files = "$uri $uri.html $uri/ /index.html"
                 else:
                     try_files = f"$uri $uri.html $uri/ {location_path}/index.html"
            
            locations.append(_make_static_location(
                location_path, serve_path, index_file, try_files, f"# Static site for {path}",
                expires_var=expires_var, cc_var=cc_var
            ))

    locations.append("""    location ~ /\\. {
        deny all;
        access_log off;
        log_not_found off;
    }""")

    acme_location = locations[0]
    http_content = f"""
{acme_location}

    location / {{
        return 301 https://$host$request_uri;
    }}
""" if enable_https_redirect else f"""
{chr(10).join(locations)}
"""

    main_config = f"""server {{
    listen 80{default_server};
    listen [::]:80{default_server};

    {server_name_directive}{symlink_policy}
{http_content}
}}

server {{
    listen 443 ssl{default_server};
    listen [::]:443 ssl{default_server};
    http2 on;

    {server_name_directive}{symlink_policy}

    ssl_certificate {cert_file};
    ssl_certificate_key {key_file};
    ssl_protocols {SSL_PROTOCOLS};
    ssl_prefer_server_ciphers on;
    ssl_ciphers {SSL_CIPHERS};

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

{chr(10).join(locations)}
}}
"""
    
    return "\n".join([GENERATED_CONFIG_MARKER, cache_maps, main_config])


def _write_nginx_sites_for_groups(
    grouped_deployments: dict[Optional[str], Deployments],
    enable_https_redirect: bool = True,
) -> None:
    """Create nginx site configurations for grouped deployments."""
    
    run("mkdir -p /var/www/letsencrypt/.well-known/acme-challenge")
    for directory in (NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR):
        os.makedirs(directory, exist_ok=True)
    current_config_names = {_config_name_for_domain(domain) for domain in grouped_deployments}
    _reconcile_deployment_sites(current_config_names)
    
    for domain, deployments in grouped_deployments.items():
        cert_domain = domain or 'default'
        generate_self_signed_cert(cert_domain)
        
        config_name = _config_name_for_domain(domain)

        config_file = os.path.join(NGINX_SITES_AVAILABLE_DIR, config_name)
        
        is_default = (domain is None)
        
        config_content = generate_merged_nginx_config(
            domain, deployments, is_default, enable_https_redirect=enable_https_redirect
        )
        
        try:
            write_bytes_atomic(config_file, config_content.encode("utf-8"), mode=0o644)
        except PermissionError as e:
            raise PermissionError(f"Failed to write nginx config to {config_file}: {e}") from e
        
        print(f"  ✓ Created nginx config: {config_file}")
        
        enabled_link = os.path.join(NGINX_SITES_ENABLED_DIR, config_name)
        if os.path.lexists(enabled_link) and (
            not os.path.islink(enabled_link) or os.path.realpath(enabled_link) != config_file
        ):
            _remove_path(enabled_link)

        if not os.path.lexists(enabled_link):
            os.symlink(config_file, enabled_link)
            print(f"  ✓ Enabled nginx site: {config_name}")


def create_nginx_sites_for_groups(
    grouped_deployments: dict[Optional[str], Deployments],
    enable_https_redirect: bool = True,
) -> None:
    """Recoverably reconcile deployment sites and their generated TLS files."""
    for domain in grouped_deployments:
        if domain is not None and (not isinstance(domain, str) or not validate_host(domain)):
            raise ValueError("Nginx deployment domain must be a valid host")
    for directory in (NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR):
        validate_filesystem_path(directory)
        if os.path.islink(directory):
            raise ValueError(f"Nginx site directory must not be a symlink: {directory}")
    current_config_names = {_config_name_for_domain(domain) for domain in grouped_deployments}
    if len(current_config_names) != len(grouped_deployments):
        raise ValueError("Nginx deployment domains produce duplicate configuration names")
    extra_paths = tuple(path for domain in grouped_deployments for path in _self_signed_cert_path(domain or "default"))
    for path in extra_paths:
        validate_filesystem_path(path)
        if os.path.islink(os.path.dirname(path)):
            raise ValueError(f"Managed TLS directory must not be a symlink: {os.path.dirname(path)}")
    if is_dry_run():
        print("  [DRY-RUN] Would reconcile and reload deployment Nginx sites")
        return
    store = OperationStateStore(os.path.join(
        os.path.dirname(NGINX_SITES_AVAILABLE_DIR), ".basaltwater-nginx-operation.json",
    ))
    record = None
    backup_dir = ""
    resolved = False
    modified = False
    try:
        record = store.begin("nginx-reconciliation", NGINX_SITES_AVAILABLE_DIR, "staging")
        try:
            _assert_managed_config_names(current_config_names)
            # The apply path requires reload, so refuse an inactive/uninspectable
            # daemon before changing files rather than trying to start it.
            if run("systemctl is-active --quiet nginx", check=False, capture_output=True).returncode != 0:
                raise RuntimeError("Nginx must be active before reconciling deployment sites")
            snapshot = _snapshot_deployment_sites(current_config_names, extra_paths)
            backup_dir = tempfile.mkdtemp(prefix=".basaltwater-nginx-", dir=os.path.dirname(store.path))
            paths = set(snapshot) | set(extra_paths) | {
                os.path.join(directory, name)
                for directory in (NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR)
                for name in current_config_names
            }
            previous = {}
            for path in sorted(paths):
                if path not in snapshot:
                    previous[path] = None
                    continue
                kind, value = snapshot[path]
                if kind == "symlink":
                    target, uid, gid = value
                    previous[path] = dict(kind=kind, target=target, uid=uid, gid=gid)
                else:
                    content, mode, uid, gid = value
                    previous[path] = dict(kind=kind, content_base64=base64.b64encode(content).decode("ascii"),
                                          mode=mode, uid=uid, gid=gid)
            write_json_atomic(os.path.join(backup_dir, "previous.json"), previous, mode=0o600)
            context = {"backup_dir": backup_dir, "config_names": sorted(current_config_names)}
            store.transition(record.operation_id, "applying", context=context)
            modified = True
            _write_nginx_sites_for_groups(grouped_deployments, enable_https_redirect=enable_https_redirect)
            for directory in dict.fromkeys((NGINX_SITES_AVAILABLE_DIR, NGINX_SITES_ENABLED_DIR,
                                            *(os.path.dirname(path) for path in extra_paths))):
                if os.path.isdir(directory):
                    fsync_tree(directory)
            store.transition(record.operation_id, "verifying")
            if run("nginx -t", check=False).returncode != 0:
                raise RuntimeError("nginx configuration test failed")
            run("systemctl reload nginx")
            if run("systemctl is-active --quiet nginx", check=False, capture_output=True).returncode != 0:
                raise RuntimeError("Nginx is not active after reload")
            store.complete(record.operation_id)
            resolved = True
        except BaseException as error:
            errors = []
            try:
                store.transition(record.operation_id, "rolling-back")
            except Exception as exc:
                errors.append(type(exc).__name__)
            if modified:
                try:
                    _restore_deployment_sites(snapshot, current_config_names, extra_paths)
                    if run("nginx -t", check=False).returncode != 0:
                        raise RuntimeError("Restored Nginx configuration test failed")
                    run("systemctl reload nginx")
                    if run("systemctl is-active --quiet nginx", check=False, capture_output=True).returncode != 0:
                        raise RuntimeError("Nginx is not active after restoration")
                except BaseException as exc:
                    errors.append(type(exc).__name__)
            if errors:
                try:
                    store.transition(record.operation_id, "recovery", status="recovery_required",
                                     context={"backup_dir": backup_dir, "errors": errors})
                except Exception:
                    pass
                raise RuntimeError(f"Nginx recovery was incomplete; inspect {store.path} and {backup_dir}") from error
            store.complete(record.operation_id, outcome="rolled_back" if modified else "failed")
            resolved = True
            raise
    finally:
        store.close()
        if backup_dir and resolved:
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                print(f"  ⚠ Nginx snapshot cleanup failed at {backup_dir}: {exc}")
    print("  ✓ nginx reloaded")
