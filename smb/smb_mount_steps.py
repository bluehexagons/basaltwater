"""SMB/CIFS mount configuration with systemd."""

from __future__ import annotations
import errno
import hashlib
import os
import shlex
import pwd
import re
import secrets
import stat
from typing import Any, Optional

from lib.config import SetupConfig
from lib.remote_utils import run
from lib.unit_transaction import replace_units, snapshot_unit_file
from lib.validation import validate_smb_mount_specs
from lib.validators import validate_username
from lib.setup_directories import check_directory_path, directory_fd


SMB_CREDENTIALS_DIR = "/root/.smb"
SMB_UNIT_DIR = "/etc/systemd/system"


def _check_mountpoint_path(mountpoint: str) -> None:
    """Reject unsafe components without triggering an existing Linux autofs leaf."""
    try:
        with directory_fd(os.path.dirname(mountpoint)) as parent:
            # O_DIRECTORY triggers autofs even with O_PATH. Inspect the leaf
            # descriptor instead; O_NOFOLLOW keeps a symlink from being followed.
            descriptor = os.open(os.path.basename(mountpoint),
                                 os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=parent)
            try:
                if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise ValueError(f"Setup directory contains a symlink or non-directory: {mountpoint}")
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"Setup directory contains a symlink or non-directory: {mountpoint}") from exc
        raise


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
            # Publish without replacing an existing generation. The old unit
            # and a retained recovery snapshot may still reference it.
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _current_credentials(unit_name: str, prefix: str, endpoint: str, content: str) -> str | None:
    """Reuse an immutable generation only when the published bytes match."""
    unit = snapshot_unit_file(os.path.join(SMB_UNIT_DIR, unit_name))
    if unit is None or f"What={endpoint}" not in unit["content"].splitlines():
        return None
    directory = SMB_CREDENTIALS_DIR.replace("%", "%%")
    match = re.search(
        rf"^Options=credentials={re.escape(directory)}/({re.escape(prefix)}[0-9a-f]{{32}}),",
        unit["content"], re.MULTILINE,
    )
    if match is None:
        return None
    try:
        with directory_fd(SMB_CREDENTIALS_DIR) as parent:
            directory_info = os.fstat(parent)
            if directory_info.st_uid != 0 or stat.S_IMODE(directory_info.st_mode) != 0o700:
                return None
            descriptor = os.open(match[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                expected = content.encode("utf-8")
                if (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                        and stat.S_IMODE(info.st_mode) == 0o600
                        and stream.read(len(expected) + 1) == expected):
                    return match[1]
    except FileNotFoundError:
        pass
    return None


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
    _check_mountpoint_path(mountpoint)
    check_directory_path(SMB_CREDENTIALS_DIR)
    account = pwd.getpwnam(config.username)
    uid, gid = account.pw_uid, account.pw_gid
    
    result = run(f"systemd-escape -p {shlex.quote(mountpoint)}", capture_output=True, text=True)
    escaped_mountpoint = result.stdout.strip()
    if not escaped_mountpoint or "/" in escaped_mountpoint or any(
        ord(char) < 32 or ord(char) == 127 for char in escaped_mountpoint
    ):
        raise RuntimeError(f"Unable to derive a safe systemd unit name for mountpoint: {mountpoint}")

    unit_name = f"{escaped_mountpoint}.mount"
    automount_name = f"{escaped_mountpoint}.automount"

    creds_content = f"""username={username}
password={password}
"""
    unc_path = f"//{ip}/{share}{subdir}"

    def _escape_systemd_value(value: str) -> str:
        """Preserve literal specifiers and backslashes in unit values."""
        return value.replace("%", "%%").replace("\\", "\\\\")

    escaped_endpoint = _escape_systemd_value(unc_path)
    # Keep names bounded even for long escaped systemd names. The unpredictable
    # suffix never encodes credential bytes in a world-readable unit.
    prefix = "credentials-v2-" + hashlib.sha256(mountpoint.encode()).hexdigest() + "-"
    current = _current_credentials(unit_name, prefix, escaped_endpoint, creds_content)
    creds_name = current or prefix + secrets.token_hex(16)
    creds_file = os.path.join(SMB_CREDENTIALS_DIR, creds_name)

    def prepare_mount() -> None:
        # The transaction has stopped both the activator and mount. Only now
        # open/chown the local directory, never the active remote CIFS root.
        with directory_fd(mountpoint, create=True) as descriptor:
            os.fchown(descriptor, uid, gid)
        if current is None:
            _write_credentials(SMB_CREDENTIALS_DIR, creds_name, creds_content)
    
    escaped_desc = _escape_systemd_value(mountpoint).replace('"', "'")
    escaped_credentials = creds_file.replace("%", "%%")
    
    unit_content = f"""[Unit]
Description=SMB mount for {escaped_desc}
After=network-online.target
Wants=network-online.target

[Mount]
What={escaped_endpoint}
Where={_escape_systemd_value(mountpoint)}
Type=cifs
Options=credentials={escaped_credentials},uid={uid},gid={gid},forceuid,forcegid,file_mode=0600,dir_mode=0700,nounix,perm,vers=3,seal,nofail
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
        unit_dir=SMB_UNIT_DIR, prepare=prepare_mount,
    )
    print(f"  ✓ SMB automount configured: {mountpoint} → {unc_path}")

