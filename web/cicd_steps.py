"""CI/CD webhook system setup steps."""

from __future__ import annotations

import os
import secrets
import re
import stat
import pwd
import grp

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.cicd_build import BUILD_HOME, BUILD_USER
from lib.remote_utils import run, is_package_installed
from lib.unit_transaction import replace_units
from lib.setup_directories import check_directory_path, directory_fd
from lib.validators import validate_username
from web.service_tools.cicd_security import DEFAULT_BRANCHES
from web.service_tools.cicd_config import load_config_file, save_config_file


CICD_USER = "webhook"
CICD_HOME = "/var/lib/basaltwater/cicd"
SECRET_FILE = "/etc/basaltwater/cicd/webhook_secret"
ENV_FILE = "/etc/basaltwater/cicd/webhook.env"
WEBHOOK_NGINX_CONF = "/etc/nginx/conf.d/webhook.conf"


def _read_webhook_secret(path: str) -> str:
    """Read a bounded regular secret without following symlinks or FIFOs."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Webhook secret must be a regular file')
        with os.fdopen(fd, encoding='utf-8') as stream:
            fd = -1
            return stream.read(514).removesuffix('\n')
    finally:
        if fd >= 0:
            os.close(fd)


def _check_service_config() -> None:
    """Check schema and readability under the receiver's actual identity."""
    run(['runuser', '-u', CICD_USER, '--', '/usr/bin/python3', '-c',
         "import sys; sys.path.insert(0, '/opt/basaltwater'); "
         "from web.service_tools.cicd_config import load_config_file; "
         "load_config_file('/etc/basaltwater/cicd/webhook_config.json')"])


def _cicd_state_principals(parent: str) -> set[int]:
    """Identify builders from administrator-controlled account records, not ACLs."""
    principals = {pwd.getpwnam(user).pw_uid for user in (CICD_USER, BUILD_USER)}
    # DeploymentOrchestrator creates build-* accounts with this exact home and
    # shell convention. A latent named ACL alone is not evidence of authority.
    for account in pwd.getpwall():
        name = account.pw_name
        if (validate_username(name) and name.startswith('build-')
                and 6 < len(name) <= 31 and account.pw_uid > 0
                and account.pw_gid > 0 and account.pw_shell == '/usr/sbin/nologin'
                and account.pw_dir == os.path.join(parent, 'build-users', name)):
            principals.add(account.pw_uid)
    return principals


def _state_access_acl(target: str) -> dict[str, int]:
    """Read numeric access entries on the already opened state directory."""
    result = run(['getfacl', '--access', '--omit-header', '--numeric',
                  '--no-effective', '--', target], capture_output=True, timeout=30)
    entries = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'(user|group|mask|other):([0-9]*):([r-][w-][x-])', line.strip())
        if match is None:
            raise ValueError('Invalid state directory access ACL')
        kind, qualifier, permissions = match.groups()
        key = f'{kind}:{qualifier}'
        if key in entries or (qualifier and kind not in ('user', 'group')):
            raise ValueError('Invalid state directory access ACL')
        entries[key] = sum(bit for letter, bit in (('r', 4), ('w', 2), ('x', 1))
                           if letter in permissions)
    if not {'user:', 'group:', 'other:'} <= entries.keys():
        raise ValueError('Incomplete state directory access ACL')
    return entries


def reconcile_cicd_state_access() -> None:
    """Restore authorized traversal without unmasking unrelated permissions."""
    parent = os.path.dirname(CICD_HOME)
    principals = _cicd_state_principals(parent)
    with directory_fd(parent) as descriptor:
        # The child opens our live descriptor rather than resolving the state
        # pathname again. run() does not inherit descriptors across exec.
        target = f'/proc/{os.getpid()}/fd/{descriptor}'
        entries = _state_access_acl(target)
        mask = entries.get('mask:', entries['group:'])
        # Snapshot every group-class entry's effective permissions before
        # widening the mask. This also covers group:: and named groups.
        grants = {key: permissions & mask for key, permissions in entries.items()
                  if key.startswith(('user:', 'group:')) and key != 'user:'}
        for uid in sorted(principals):
            key = f'user:{uid}'
            grants[key] = grants.get(key, 0) | 0o1
        grants['mask:'] = mask | 0o1
        changes = ','.join(
            f'{key}:' + ''.join(letter if permissions & bit else '-'
                               for letter, bit in (('r', 4), ('w', 2), ('x', 1)))
            for key, permissions in grants.items()
        )
        run(['setfacl', '--no-mask', '-m', changes, '--', target],
            timeout=30)
    _check_cicd_state_access()


def _check_cicd_state_access() -> None:
    """Verify state access with the runtime UIDs and no supplementary grants."""
    for user, directories in (
        (CICD_USER, (CICD_HOME, f'{CICD_HOME}/jobs', f'{CICD_HOME}/logs')),
        (BUILD_USER, (BUILD_HOME, f'{BUILD_HOME}/workspaces')),
    ):
        account = pwd.getpwnam(user)
        for directory in directories:
            check_directory_path(directory)
            for permission in ('-x', '-w', '-r'):
                run([
                    '/usr/bin/setpriv', f'--reuid={account.pw_uid}',
                    f'--regid={account.pw_gid}', '--clear-groups',
                    '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all',
                    '--no-new-privs', '--', '/usr/bin/test', permission, directory,
                ], timeout=30)


def secure_cicd_directories(directories: list[str]) -> None:
    """Reconcile CI/CD directory ownership without changing file modes.

    Build-user homes contain SSH private keys and other files whose modes must
    remain narrower than the surrounding directories.  Applying ``chmod -R``
    here would make those files group-readable and can make OpenSSH reject the
    deploy key on a subsequent setup run.
    """

    user_check = run(["id", CICD_USER], check=False)
    if user_check.returncode != 0:
        raise RuntimeError(
            f"Cannot secure CI/CD directories because user '{CICD_USER}' does not exist"
        )

    for directory in directories:
        check_directory_path(directory)
    uid = pwd.getpwnam(CICD_USER).pw_uid
    gid = grp.getgrnam(CICD_USER).gr_gid
    for directory in directories:
        with directory_fd(directory, create=True, mode=0o750) as descriptor:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, 0o711 if directory == CICD_HOME else 0o750)


def create_isolated_build_directories() -> None:
    """Create build-owned directories without recursively touching credentials."""
    directories = (BUILD_HOME, f"{BUILD_HOME}/workspaces")
    for directory in directories:
        check_directory_path(directory)
    uid = pwd.getpwnam(BUILD_USER).pw_uid
    gid = grp.getgrnam(BUILD_USER).gr_gid
    for directory in directories:
        with directory_fd(directory, create=True, mode=0o700) as descriptor:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, 0o700)


def install_cicd_dependencies(config: SetupConfig) -> None:
    """Install dependencies required for CI/CD system."""
    packages = ['git', 'acl']
    
    def all_installed() -> bool:
        return all(is_package_installed(pkg) for pkg in packages)
    
    if all_installed():
        print("  ✓ CI/CD dependencies already installed")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "install", "-y", "-qq", *packages])
    
    if all_installed():
        print("  ✓ CI/CD dependencies installed")
        return
    raise RuntimeError("CI/CD dependencies were not present after installation")


def create_cicd_user(config: SetupConfig) -> None:
    """Create dedicated user for webhook receiver service."""
    result = run(["id", BUILD_USER], check=False)
    if result.returncode == 0:
        run(["usermod", "--home", BUILD_HOME, BUILD_USER])
    else:
        run(["useradd", "--system", "--user-group", "--home-dir", BUILD_HOME,
             "--no-create-home", "--shell", "/usr/sbin/nologin", BUILD_USER])
    user = CICD_USER
    
    result = run(["id", user], check=False)
    if result.returncode == 0:
        run(["usermod", "--home", CICD_HOME, user])
        print(f"  ✓ User '{user}' already exists")
        return
    
    run([
        "useradd",
        "--system",
        "--home-dir",
        CICD_HOME,
        "--no-create-home",
        "--shell",
        "/usr/sbin/nologin",
        user,
    ])
    print(f"  ✓ Created user '{user}'")


def create_cicd_directories(config: SetupConfig) -> None:
    """Create directories for CI/CD system."""
    del config
    state_directories = [
        CICD_HOME,
        f"{CICD_HOME}/jobs",
        f"{CICD_HOME}/logs",
    ]

    for directory in state_directories:
        check_directory_path(directory)
    with directory_fd("/etc/basaltwater/cicd", create=True):
        pass

    secure_cicd_directories(state_directories)
    create_isolated_build_directories()
    reconcile_cicd_state_access()
    
    print("  ✓ Created CI/CD directories")


def generate_webhook_secret(config: SetupConfig) -> str:
    """Generate a secure webhook secret and store it in an environment file."""
    secret_file = SECRET_FILE
    env_file = ENV_FILE
    
    if os.path.islink(secret_file) or os.path.islink(env_file):
        raise ValueError('Webhook secret and environment paths must not be symlinks')
    if os.path.exists(secret_file):
        secret = _read_webhook_secret(secret_file)
    else:
        secret = secrets.token_urlsafe(32)
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,512}', secret):
        raise ValueError('Webhook secret must be a nonempty single-line environment-safe value')
    
    write_text_atomic(secret_file, secret, mode=0o600, uid=0, gid=0)
    
    _create_env_file(env_file, secret)
    
    print("  ✓ Reconciled webhook secret and environment")
    print(f"  ℹ Secret stored in: {secret_file}")
    
    return secret


def _create_env_file(env_file: str, secret: str) -> None:
    """Create environment file for systemd service with restricted permissions."""
    write_text_atomic(
        env_file,
        f"WEBHOOK_SECRET={secret}\nWEBHOOK_PORT=8765\n",
        mode=0o600,
        uid=0,
        gid=0,
    )


def create_default_webhook_config(config: SetupConfig) -> None:
    """Create default webhook configuration file."""
    config_file = "/etc/basaltwater/cicd/webhook_config.json"
    
    if os.path.exists(config_file):
        save_config_file(config_file, load_config_file(config_file))
        print("  ✓ Webhook configuration already exists")
        return
    
    # Create default configuration
    default_config = {
        "repositories": [
            {
                "url": "https://github.com/example/repo.git",
                "branches": list(DEFAULT_BRANCHES),
                "scripts": {
                    "install": "scripts/install.sh",
                    "build": "scripts/build.sh",
                    "test": "scripts/test.sh",
                    "deploy": "scripts/deploy.sh"
                }
            }
        ]
    }
    
    save_config_file(config_file, default_config)
    
    print("  ✓ Created default webhook configuration")
    print(f"  ℹ Edit configuration: {config_file}")


def create_webhook_receiver_service(config: SetupConfig) -> None:
    """Create systemd service for webhook receiver."""
    service_name = "webhook-receiver"
    generate_webhook_secret(config)
    _check_cicd_state_access()
    _check_service_config()
    
    
    service_content = """[Unit]
Description=Webhook Receiver for CI/CD
After=network.target

[Service]
Type=simple
User=webhook
Group=webhook
WorkingDirectory=/opt/basaltwater/web/service_tools
Environment=HOME=/var/lib/basaltwater/cicd
Environment=BASALTWATER_WORKSPACE=/var/lib/basaltwater/cicd
EnvironmentFile=/etc/basaltwater/cicd/webhook.env
ExecStart=/usr/bin/python3 /opt/basaltwater/web/service_tools/webhook_receiver.py
Restart=always
RestartSec=10

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @mount
# SQLite also needs to create its rollback journal beside the delivery ledger.
ReadWritePaths=/var/lib/basaltwater/cicd
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=webhook-receiver

[Install]
WantedBy=multi-user.target
"""
    
    unit = f"{service_name}.service"
    replace_units({unit: service_content}, activate=(unit,))

    print(f"  ✓ Created and started {service_name}.service")


def create_cicd_executor_service(config: SetupConfig) -> None:
    """Create systemd service + path unit for CI/CD executor.
    
    The executor is triggered by a systemd path unit that watches the jobs
    directory for new files written by the unprivileged webhook receiver.
    This avoids requiring the webhook user to have systemctl/polkit privileges
    to start the executor service (which previously made jobs silently fail
    to run when the webhook user could not invoke ``systemctl start``).
    """
    service_name = "cicd-executor"
    _check_cicd_state_access()
    _check_service_config()
    
    
    service_content = """[Unit]
Description=CI/CD Job Executor
After=network.target

[Service]
Type=oneshot
# System services default to root. Keep User= unset: systemd 257's explicit-user
# seccomp setup drops non-ambient CAP_SETUID, which NNP prevents exec regaining.
# The broker needs it for receiver-state access and the build identity boundary.
Group=root
WorkingDirectory=/opt/basaltwater/web/service_tools
Environment=HOME=/var/lib/basaltwater/cicd
Environment=BASALTWATER_WORKSPACE=/var/lib/basaltwater/cicd
ExecStart=/usr/bin/python3 -I /opt/basaltwater/web/service_tools/cicd_executor.py
# Each job owns a four-hour budget; this process may drain several jobs.
TimeoutStartSec=infinity

# Security hardening (executor must run user-supplied scripts so we cannot
# apply MemoryDenyWriteExecute or SystemCallFilter without breaking common
# CI tooling such as Node and other JIT-compiled languages)
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
SystemCallArchitectures=native
ReadWritePaths=/var/lib/basaltwater/cicd /var/log/basaltwater
CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP CAP_DAC_OVERRIDE CAP_KILL
AmbientCapabilities=
UMask=0027

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cicd-executor
"""
    
    # Path activator: triggers the executor whenever a job file is written by
    # the webhook receiver. The receiver runs as an unprivileged user that
    # cannot call ``systemctl start`` directly, so this is required.
    path_content = """[Unit]
Description=Watch CI/CD jobs directory for new jobs
After=network.target

[Path]
DirectoryNotEmpty=/var/lib/basaltwater/cicd/jobs
PathChanged=/var/lib/basaltwater/cicd/jobs
Unit=cicd-executor.service

[Install]
WantedBy=multi-user.target
"""
    
    replace_units(
        {f"{service_name}.service": service_content, f"{service_name}.path": path_content},
        activate=(f"{service_name}.path",),
    )

    print(f"  ✓ Created {service_name}.service and {service_name}.path")


def configure_nginx_for_webhook(config: SetupConfig) -> None:
    """Configure nginx to reverse proxy webhook endpoint."""
    nginx_conf = WEBHOOK_NGINX_CONF
    
    if os.path.exists(nginx_conf):
        print("  ✓ Nginx webhook configuration already exists")
        return
    
    # Create nginx configuration with rate limiting
    nginx_content = """# Webhook receiver reverse proxy
# Rate limiting zone: default 10 requests per minute per IP.
# NOTE: This conservative default may be too restrictive for repositories with
# frequent commits or for instances handling multiple repositories. If you see
# HTTP 429 responses from the webhook endpoint under normal load, consider
# increasing the `rate` (and optionally `burst`) values below to better match
# your expected webhook traffic pattern.
limit_req_zone $binary_remote_addr zone=webhook_limit:10m rate=10r/m;

server {
    listen 127.0.0.1:8080;
    server_name _;
    
    location /webhook {
        # Rate limiting
        limit_req zone=webhook_limit burst=5 nodelay;
        limit_req_status 429;
        client_max_body_size 1m;
        
        # Proxy to webhook receiver
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Pass through GitHub webhook headers
        proxy_set_header X-Hub-Signature-256 $http_x_hub_signature_256;
        proxy_set_header X-GitHub-Event $http_x_github_event;
        proxy_set_header X-GitHub-Delivery $http_x_github_delivery;
        
        # Timeouts
        proxy_connect_timeout 5s;
        proxy_send_timeout 10s;
        proxy_read_timeout 10s;
        
        # No buffering for webhook responses
        proxy_buffering off;
    }
    
    location /webhook/health {
        proxy_pass http://127.0.0.1:8765/health;
        access_log off;
    }
}
"""
    
    write_text_atomic(nginx_conf, nginx_content, mode=0o644)
    reload_attempted = False
    try:
        result = run("nginx -t", check=False)
        if result.returncode != 0:
            raise RuntimeError("Nginx webhook configuration test failed")
        reload_attempted = True
        if run(["systemctl", "reload", "nginx"], check=False).returncode != 0:
            raise RuntimeError("Failed to reload nginx for webhook")
    except BaseException:
        os.remove(nginx_conf)
        if reload_attempted:
            if run("nginx -t", check=False).returncode != 0:
                raise RuntimeError("Restored nginx webhook configuration test failed")
            if run(["systemctl", "reload", "nginx"], check=False).returncode != 0:
                raise RuntimeError("Restored nginx webhook reload failed")
        raise
    
    print("  ✓ Configured nginx for webhook endpoint")


def update_cloudflare_tunnel_for_webhook(config: SetupConfig) -> None:
    """Update Cloudflare tunnel configuration to include webhook endpoint."""
    cloudflared_config = "/etc/cloudflared/config.yml"
    
    if not os.path.exists(cloudflared_config):
        print("  ℹ Cloudflare tunnel not configured, skipping")
        return
    
    # Read existing configuration
    with open(cloudflared_config, 'r') as f:
        content = f.read()
    
    # Check if webhook ingress already exists
    if 'service: http://localhost:8080' in content:
        print("  ✓ Cloudflare tunnel already configured for webhook")
        return
    
    print("  ℹ Cloudflare tunnel configuration needs manual update")
    print("  Add the following to your tunnel ingress rules:")
    print("    - hostname: webhook.yourdomain.com")
    print("      service: http://localhost:8080")


def install_webhook_manager_helper(config: SetupConfig) -> None:
    """Create symlink for webhook manager helper script."""
    helper_script = "/usr/local/bin/webhook-manager"
    source_script = "/opt/basaltwater/web/service_tools/webhook_manager.py"
    
    if os.path.exists(helper_script):
        print("  ✓ Webhook manager helper already available")
        return
    
    if not os.path.exists(source_script):
        print(f"  ⚠ Source script not found: {source_script}")
        return
    
    run(["ln", "-sf", source_script, helper_script])
    run(["chmod", "+x", source_script])
    
    print(f"  ✓ Installed webhook manager: {helper_script}")
    print(f"  Run 'sudo webhook-manager list' to manage configurations")
