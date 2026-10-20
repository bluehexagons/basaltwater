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
from lib.unit_transaction import replace_units
from lib.validation import validate_smb_mount_specs
from lib.validators import validate_username
from lib.setup_directories import check_directory_path, directory_fd


SMB_CREDENTIALS_DIR = "/root/.smb"
SMB_UNIT_DIR = "/etc/systemd/system"


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
    automount_name = f"{escaped_mountpoint}.automount"

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
    
    def _escape_systemd_value(value: str) -> str:
        """Preserve literal specifiers and backslashes in unit values."""
        return value.replace("%", "%%").replace("\\", "\\\\")
    
    escaped_desc = _escape_systemd_value(mountpoint).replace('"', "'")
    
    unit_content = f"""[Unit]
Description=SMB mount for {escaped_desc}
After=network-online.target
Wants=network-online.target

[Mount]
What={_escape_systemd_value(unc_path)}
Where={_escape_systemd_value(mountpoint)}
Type=cifs
Options=credentials={_escape_systemd_value(creds_file)},uid={config.username},gid={config.username},file_mode=0644,dir_mode=0755,vers=3.0,seal,nofail
"""

    automount_content = f"""[Unit]
Description=SMB automount for {escaped_desc}

[Automount]
Where={_escape_systemd_value(mountpoint)}
TimeoutIdleSec=60

[Install]
WantedBy=multi-user.target
"""

    # Quiesce the activator before the mount. In particular, disable the old
    # boot-enabled .mount while retaining both definitions and activation state
    # for rollback. Only the automount starts now; network access is on demand.
    replace_units(
        {unit_name: unit_content, automount_name: automount_content},
        activate=(automount_name,), deactivate=(automount_name, unit_name),
        unit_dir=SMB_UNIT_DIR,
    )
    print(f"  ✓ SMB automount configured: {mountpoint} → {unc_path}")

