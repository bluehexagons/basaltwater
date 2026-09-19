"""App server setup steps for receiving deployments from build servers."""

from __future__ import annotations

import os
import shutil

from lib.config import SetupConfig
from lib.remote_utils import run, is_package_installed, is_service_active


DEPLOY_ADMIN_SOURCE = "/opt/basaltwater/web/service_tools/deploy_admin.py"
DEPLOY_ADMIN_HELPER = "/usr/local/sbin/basaltwater-deploy-admin"


def install_app_server_dependencies(config: SetupConfig) -> None:
    """Install minimal dependencies for app server."""
    packages = ['nginx', 'rsync']
    
    def all_installed() -> bool:
        return all(is_package_installed(pkg) for pkg in packages)
    
    if all_installed():
        print("  ✓ App server dependencies already installed")
        return
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "install", "-y", "-qq", *packages])
    
    if all_installed():
        print("  ✓ App server dependencies installed")


def create_deploy_user(config: SetupConfig) -> None:
    """Create deploy user for receiving deployments from build server."""
    user = "deploy"
    
    result = run(["id", user], check=False)
    if result.returncode == 0:
        print("  ✓ Deploy user already exists")
        return
    
    run(["useradd", "--system", "--create-home", "--shell", "/bin/bash", user])
    
    ssh_dir = f"/home/{user}/.ssh"
    run(["mkdir", "-p", ssh_dir])
    run(["chmod", "700", ssh_dir])
    run(["chown", "-R", f"{user}:{user}", ssh_dir])
    
    print("  ✓ Created deploy user")


def configure_deploy_sudoers(config: SetupConfig) -> None:
    """Install the validated deploy helper and its minimal sudo permission."""
    sudoers_file = "/etc/sudoers.d/deploy-nginx"

    if not os.path.isfile(DEPLOY_ADMIN_SOURCE):
        raise RuntimeError(f"Deploy admin helper source not found: {DEPLOY_ADMIN_SOURCE}")

    os.makedirs(os.path.dirname(DEPLOY_ADMIN_HELPER), mode=0o755, exist_ok=True)
    shutil.copyfile(DEPLOY_ADMIN_SOURCE, DEPLOY_ADMIN_HELPER)
    os.chown(DEPLOY_ADMIN_HELPER, 0, 0)
    os.chmod(DEPLOY_ADMIN_HELPER, 0o755)

    sudoers_content = f"""# Validated privileged operations for the deploy account
deploy ALL=(root) NOPASSWD: {DEPLOY_ADMIN_HELPER} *
"""
    
    os.makedirs("/etc/sudoers.d", exist_ok=True)
    with open(sudoers_file, 'w') as f:
        f.write(sudoers_content)
    
    os.chmod(sudoers_file, 0o440)
    
    result = run(["visudo", "-c"], check=False)
    if result.returncode != 0:
        print("  ⚠ Sudoers validation failed, removing...")
        os.remove(sudoers_file)
        return
    
    print("  ✓ Configured deploy user sudoers")


def create_app_directories(config: SetupConfig) -> None:
    """Create directories for app deployments."""
    directories = [
        "/var/www",
        "/var/log/basaltwater/web",
        "/etc/nginx/sites-available",
        "/etc/nginx/sites-enabled",
    ]
    
    for directory in directories:
        os.makedirs(directory, mode=0o755, exist_ok=True)
    
    run(["chown", "-R", "deploy:deploy", "/var/www"])
    run(["chmod", "-R", "775", "/var/www"])
    
    print("  ✓ Created app directories")


def configure_deploy_ssh_access(config: SetupConfig) -> None:
    """Ensure SSH access is configured for deploy user."""
    ssh_dir = "/home/deploy/.ssh"
    auth_keys = f"{ssh_dir}/authorized_keys"
    
    if not os.path.exists(ssh_dir):
        os.makedirs(ssh_dir, mode=0o700)
        run(["chown", "deploy:deploy", ssh_dir])
    
    if not os.path.exists(auth_keys):
        run(["touch", auth_keys])
        run(["chmod", "600", auth_keys])
        run(["chown", "deploy:deploy", auth_keys])
    
    print("  ✓ Configured deploy SSH access")
    print(f"  ℹ Add build server public key to: {auth_keys}")


def configure_app_nginx(config: SetupConfig) -> None:
    """Configure nginx for app server."""
    if is_service_active("nginx"):
        print("  ✓ nginx already running")
        return
    
    run(["systemctl", "enable", "nginx"])
    run(["systemctl", "start", "nginx"])
    
    print("  ✓ nginx configured for app server")
