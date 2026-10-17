"""SMB/CIFS mount configuration with systemd."""

from __future__ import annotations
import os
import shlex
import grp
import pwd
import secrets
from typing import Any, Optional

from lib.config import SetupConfig
from lib.remote_utils import run
from lib.systemd_service import cleanup_systemd_unit
from lib.validation import validate_smb_mount_specs
from lib.validators import validate_username
from lib.setup_directories import check_directory_path, directory_fd


SMB_CREDENTIALS_DIR = "/root/.smb"


def _write_credentials(directory: str, name: str, content: str) -> None:
    """Replace credentials through a private no-follow directory descriptor."""
    with directory_fd(directory, create=True, mode=0o700) as parent:
        os.fchown(parent, 0, 0)
        os.fchmod(parent, 0o700)
        temporary = ".credentials-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
                os.fchown(file_obj.fileno(), 0, 0)
                os.fchmod(file_obj.fileno(), 0o600)
                file_obj.write(content)
                file_obj.flush()
                os.fsync(file_obj.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def parse_smb_mount_spec(mount_spec: Optional[list[str]]) -> dict[str, Any]:
    """Parse SMB mount specification.
    
    Args:
        mount_spec: [mountpoint, ip, credentials, share, subdir]
        
    Returns:
        dict with mount configuration
    """
    if not mount_spec:
        raise ValueError("mount_spec is required")
    if len(mount_spec) != 5:
        raise ValueError(f"Invalid SMB mount spec: expected 5 arguments, got {len(mount_spec)}")
    
    mountpoint, ip, credentials, share, subdir = mount_spec
    
    if not mountpoint.startswith('/'):
        raise ValueError(f"Mountpoint must be absolute: {mountpoint}")
    
    if ':' not in credentials:
        raise ValueError(f"Credentials must be username:password format")
    
    username, password = credentials.split(':', 1)
    
    return {
        'mountpoint': mountpoint,
        'ip': ip,
        'username': username,
        'password': password,
        'share': share,
        'subdir': subdir
    }


def configure_smb_mount(config: SetupConfig, mount_spec: Optional[list[str]] = None, **_ : Any) -> None:
    """Configure persistent SMB mount using systemd.
    
    Args:
        config: SetupConfig object
        mount_spec: [mountpoint, ip, credentials, share, subdir]
    """
    # Custom steps can call this directly, so apply the same complete
    # preflight used by the normal setup flow before changing the filesystem.
    validate_smb_mount_specs([mount_spec])
    mount_config = parse_smb_mount_spec(mount_spec)
    
    mountpoint = mount_config['mountpoint']
    ip = mount_config['ip']
    username = mount_config['username']
    password = mount_config['password']
    share = mount_config['share']
    subdir = mount_config['subdir']
    if not validate_username(config.username):
        raise ValueError("Invalid SMB mount owner")
    check_directory_path(mountpoint)
    check_directory_path(SMB_CREDENTIALS_DIR)
    uid = pwd.getpwnam(config.username).pw_uid
    gid = grp.getgrnam(config.username).gr_gid
    
    result = run(f"systemd-escape -p {shlex.quote(mountpoint)}", capture_output=True, text=True)
    escaped_mountpoint = result.stdout.strip()
    if not escaped_mountpoint or "/" in escaped_mountpoint or any(
        ord(char) < 32 or ord(char) == 127 for char in escaped_mountpoint
    ):
        raise RuntimeError(f"Unable to derive a safe systemd unit name for mountpoint: {mountpoint}")

    unit_name = f"{escaped_mountpoint}.mount"
    unit_path = f"/etc/systemd/system/{unit_name}"

    with directory_fd(mountpoint, create=True) as descriptor:
        os.fchown(descriptor, uid, gid)

    credentials_dir = SMB_CREDENTIALS_DIR

    # Use the already collision-resistant systemd-escaped mount name. The old
    # slash-to-underscore conversion made /mnt/a_b and /mnt/a/b share one
    # credential file and could silently point a mount at the wrong server.
    creds_file = f"{credentials_dir}/credentials-{escaped_mountpoint}"
    
    creds_content = f"""username={username}
password={password}
"""
    
    _write_credentials(credentials_dir, os.path.basename(creds_file), creds_content)
    
    unc_path = f"//{ip}/{share}{subdir}"
    
    def _escape_systemd_description(value: str) -> str:
        """Escape value for safe use in a systemd Description field."""
        return value.replace("%", "%%").replace("\\", "\\\\").replace("\n", " ").replace('"', "'")
    
    escaped_desc = _escape_systemd_description(mountpoint)
    
    # Clean up existing mount unit before creating new one
    cleanup_systemd_unit(escaped_mountpoint, "mount")
    
    unit_content = f"""[Unit]
Description=SMB mount for {escaped_desc}
After=network-online.target
Wants=network-online.target

[Mount]
What={unc_path.replace('%', '%%')}
Where={mountpoint.replace('%', '%%')}
Type=cifs
Options=credentials={creds_file.replace('%', '%%')},uid={config.username},gid={config.username},file_mode=0644,dir_mode=0755,vers=3.0,seal,nofail,x-systemd.automount,x-systemd.idle-timeout=60

[Install]
WantedBy=multi-user.target
"""
    
    with open(unit_path, 'w') as f:
        f.write(unit_content)
    
    run("systemctl daemon-reload")
    run(f"systemctl enable {shlex.quote(unit_name)}")
    
    result = run(f"systemctl start {shlex.quote(unit_name)}", check=False)
    if result.returncode == 0:
        print(f"  ✓ SMB mount configured and mounted: {mountpoint} → {unc_path}")
    else:
        print(f"  ✓ SMB mount configured: {mountpoint} → {unc_path} (will mount at boot)")

