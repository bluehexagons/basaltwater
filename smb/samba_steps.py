from __future__ import annotations

import os
import shlex
import re
from typing import Any, Optional, cast

from common.storage_steps import assert_declared_storage_mount
from lib.config import SetupConfig
from lib.machine_state import can_manage_firewall
from lib.mount_utils import is_path_under_mnt, get_mount_ancestor
from lib.remote_utils import run, is_package_installed
from lib.validation import validate_samba_share_specs


SMB_CONF_PATH = "/etc/samba/smb.conf"
MANAGED_SHARES_BEGIN = "# BEGIN basaltwater managed Samba shares"
MANAGED_SHARES_END = "# END basaltwater managed Samba shares"
SAMBA_FIREWALL_COMMENT_PREFIX = "basaltwater Samba 445/tcp source"
SAMBA_FAIL2BAN_JAIL_PATH = "/etc/fail2ban/jail.d/samba-auth.local"
SAMBA_FAIL2BAN_FILTER_PATH = "/etc/fail2ban/filter.d/samba-auth.conf"
SAMBA_REMOVED_GLOBAL_SETTINGS = ("null passwords",)


def parse_share_credentials(
    credential_specs: Optional[list[list[str]]]
) -> dict[str, str]:
    credentials: dict[str, str] = {}
    if not credential_specs:
        return credentials

    for credential_spec in credential_specs:
        if not credential_spec or len(credential_spec) < 2:
            raise ValueError(
                f"Credential spec requires: username password (got: {credential_spec})"
            )

        username = credential_spec[0].strip()
        password = credential_spec[1].strip()
        if not username:
            raise ValueError("Credential username must not be empty")

        credentials[username] = password

    return credentials


def install_samba(config: SetupConfig) -> None:
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq samba samba-common-bin")
    
    run("systemctl enable smbd")
    run("systemctl start smbd")
    
    print("  ✓ Samba installed/updated and service started")


def configure_samba_firewall(config: SetupConfig) -> None:
    if not can_manage_firewall(config.machine_type):
        print("  ✓ Skipping Samba guest firewall (container host must enforce access)")
        return

    # Modern SMB only needs 445/tcp. NetBIOS (137/138/139) is disabled in the
    # global Samba config (`disable netbios = yes`), so opening 139/tcp would
    # only widen the attack surface for no benefit. Remove any pre-existing
    # 139/tcp rule that an earlier version of this tool may have added.
    cleanup_rules = [
        "ufw delete allow 139/tcp",
        "ufw delete allow 139",
    ]
    for rule in cleanup_rules:
        run(rule, check=False, capture_output=True)

    sources = config.effective_samba_sources()
    desired_comments: set[str] = set()
    if sources:
        for source in sources:
            comment = f"{SAMBA_FIREWALL_COMMENT_PREFIX} {source}"
            desired_comments.add(comment)
            result = run(
                "ufw allow from "
                f"{shlex.quote(source)} to any port 445 proto tcp "
                f"comment {shlex.quote(comment)}",
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to restrict Samba to access source {source}"
                )
        run("ufw delete allow 445/tcp", check=False, capture_output=True)
    else:
        result = run(
            "ufw allow 445/tcp comment 'Samba SMB'",
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Failed to add Samba firewall rule for TCP 445")

    status = run("ufw status numbered", check=False, capture_output=True)
    stale_numbers: list[int] = []
    if status.returncode == 0 and isinstance(status.stdout, str):
        for line in status.stdout.splitlines():
            match = re.match(r"^\[\s*(\d+)\]", line.strip())
            if not match or "#" not in line:
                continue
            comment = line.split("#", 1)[1].strip()
            if (
                comment.startswith(SAMBA_FIREWALL_COMMENT_PREFIX)
                and comment not in desired_comments
            ):
                stale_numbers.append(int(match.group(1)))
    for number in sorted(stale_numbers, reverse=True):
        run(f"ufw --force delete {number}", check=False, capture_output=True)

    reload_result = run("ufw reload", check=False, capture_output=True)
    if reload_result.returncode != 0:
        raise RuntimeError("Failed to reload UFW after Samba rule reconciliation")
    if sources:
        print(
            "  ✓ Firewall restricts Samba (445/tcp) to "
            + ", ".join(sources)
        )
    else:
        print("  ✓ Firewall configured for Samba (445/tcp)")


def parse_share_spec(
    share_spec: Optional[list[str]],
    credentials: Optional[dict[str, str]] = None
) -> dict[str, Any]:
    if not share_spec or len(share_spec) < 4:
        raise ValueError("Share spec requires: access_type share_name paths users")
    
    access_type = share_spec[0]
    if access_type not in ["read", "write"]:
        raise ValueError(f"Invalid access type: {access_type}. Must be 'read' or 'write'")
    
    share_name = share_spec[1]
    paths_str = share_spec[2]
    users_str = share_spec[3]
    
    paths = [p.strip() for p in paths_str.split(',') if p.strip()]
    if len(paths) > 1:
        raise ValueError("Samba shares support exactly one path; create one --share per directory")
    
    users: list[dict[str, str]] = []
    for user_spec in users_str.split(','):
        user_spec = user_spec.strip()
        if not user_spec:
            continue
        if ':' in user_spec:
            username, password = user_spec.split(':', 1)
        elif credentials and user_spec in credentials:
            username = user_spec
            password = credentials[user_spec]
        else:
            raise ValueError(
                f"Missing credential for share user: {user_spec}. "
                "Use 'username:password' or provide --credential USERNAME PASSWORD"
            )
        users.append({'username': username.strip(), 'password': password.strip()})
    
    return {
        'access_type': access_type,
        'share_name': share_name,
        'paths': paths,
        'users': users
    }


def validate_samba_share_credentials(config: SetupConfig) -> None:
    if not config.samba_shares:
        return

    credentials = parse_share_credentials(config.share_credentials)
    for share_spec in config.samba_shares:
        parse_share_spec(share_spec, credentials)


def create_samba_user(username: str, password: str) -> None:
    safe_username = shlex.quote(username)
    
    result = run(f"id {safe_username}", check=False)
    if result.returncode != 0:
        run(f"useradd -M -s /usr/sbin/nologin {safe_username}")
        print(f"  Created system user: {username}")
    
    result = run(f"pdbedit -L {safe_username}", check=False)
    if result.returncode != 0:
        run(
            f"smbpasswd -a -s {safe_username}",
            input_data=f"{password}\n{password}\n",
        )
        print(f"  Created Samba user: {username}")
    else:
        run(
            f"smbpasswd -s {safe_username}",
            input_data=f"{password}\n{password}\n",
        )
        print(f"  Updated Samba user password: {username}")
    
    run(f"smbpasswd -e {safe_username}", check=False)


def _get_veto_dirs_for_share(share_path: str, config: SetupConfig) -> list[str]:
    """Determine which internal directories should be hidden from a Samba share.
    
    Checks scrub specs to find database directories that are subdirectories of
    the share path. These should be hidden from Samba clients via veto files.
    
    Args:
        share_path: The primary path of the Samba share
        config: SetupConfig with scrub_specs
        
    Returns:
        List of directory basenames to veto (e.g. ['.pardatabase'])
    """
    veto_dirs: list[str] = []
    
    if not config.scrub_specs:
        return veto_dirs
    
    normalized_share = os.path.normpath(share_path)
    
    for spec in config.scrub_specs:
        if len(spec) < 2:
            continue
        scrub_dir = os.path.normpath(spec[0])
        db_path = spec[1]
        
        # Only relevant if the scrub directory matches or is under the share path
        if scrub_dir != normalized_share and not scrub_dir.startswith(normalized_share + '/'):
            continue
        
        # Resolve relative database path against the scrub directory
        if not db_path.startswith('/'):
            resolved_db = os.path.normpath(os.path.join(scrub_dir, db_path))
        else:
            resolved_db = os.path.normpath(db_path)
        
        # Check if the resolved database path is under the share path
        if resolved_db.startswith(normalized_share + '/'):
            # Extract the directory basename relative to share
            relative = os.path.relpath(resolved_db, normalized_share)
            # Only veto top-level directories within the share
            top_level = relative.split('/')[0]
            if top_level and top_level not in veto_dirs:
                veto_dirs.append(top_level)
    
    return veto_dirs


def _prepare_samba_share(
    config: SetupConfig,
    share_config: dict[str, Any],
) -> tuple[str, str]:
    """Provision one share's users, group, path, and return its config section."""

    share_name = cast(str, share_config["share_name"])
    access_type = cast(str, share_config["access_type"])
    primary_path = cast(list[str], share_config["paths"])[0]
    users = cast(list[dict[str, str]], share_config["users"])

    if is_path_under_mnt(primary_path):
        mount_ancestor = get_mount_ancestor(primary_path)
        if not mount_ancestor:
            raise RuntimeError(
                f"Share path {primary_path} is under /mnt but no mounted filesystem found. "
                f"Is the drive mounted?"
            )
    os.makedirs(primary_path, exist_ok=True)
    print(f"  Ensured path exists: {primary_path}")

    group_name = f"smb_{share_name}_{access_type}"
    safe_group = shlex.quote(group_name)

    result = run(f"getent group {safe_group}", check=False)
    if result.returncode != 0:
        run(f"groupadd {safe_group}")
        print(f"  Created group: {group_name}")

    for user_info in users:
        username = user_info['username']
        password = user_info['password']

        create_samba_user(username, password)

    member_list = ",".join(user_info["username"] for user_info in users)
    run(f"gpasswd -M {shlex.quote(member_list)} {safe_group}")
    print(f"  Reconciled {len(users)} member(s) in group {group_name}")

    safe_primary_path = shlex.quote(primary_path)
    run(f"chgrp -R --preserve-root {safe_group} {safe_primary_path}")

    if access_type == "write":
        run(f"chmod -R --preserve-root g+rwX {safe_primary_path}")
    else:
        run(f"chmod -R --preserve-root g+rX,g-w {safe_primary_path}")
    run(
        f"find {safe_primary_path} -xdev -type d "
        "-exec chmod g+s -- {} +"
    )

    print(f"  Set {'write' if access_type == 'write' else 'read-only'} permissions on {primary_path}")

    section_marker = f"[{share_name}_{access_type}]"

    share_lines = [
        section_marker,
        f"   comment = {share_name} ({access_type})",
        f"   path = {primary_path}",
        f"   valid users = @{group_name}",
        "   browseable = yes",
        f"   read only = {'yes' if access_type == 'read' else 'no'}",
    ]

    if access_type == "write":
        share_lines.append(f"   write list = @{group_name}")

    share_lines.extend([
        f"   create mask = {'0644' if access_type == 'read' else '0664'}",
        f"   directory mask = {'0755' if access_type == 'read' else '0775'}",
        f"   force group = {group_name}",
    ])

    # Veto internal directories that should not be visible to Samba clients.
    # Detect scrub database directories that fall within this share's path.
    veto_dirs = _get_veto_dirs_for_share(primary_path, config)
    if veto_dirs:
        # Samba's `veto files` syntax uses the first character as the
        # separator and matches against the on-disk filename verbatim
        # (with `*`/`?` globbing). Use the actual directory names from
        # disk; do not synthesise a leading dot, which would otherwise
        # make patterns like `.subdir` fail to match a directory named
        # `subdir`.
        veto_pattern = "/" + "/".join(veto_dirs) + "/"
        share_lines.append(f"   veto files = {veto_pattern}")
        share_lines.append("   delete veto files = no")
        print(f"  Hiding internal directories from share: {', '.join(veto_dirs)}")

    return group_name, "\n".join(share_lines) + "\n"


def _managed_share_group(section: str) -> Optional[str]:
    """Return the managed group for an basaltwater-generated share section."""

    header_match = re.match(r"(?m)^[ \t]*\[([^\]\r\n]+)\]", section)
    if not header_match:
        return None
    section_name = header_match.group(1)
    expected_group = f"smb_{section_name}"
    settings = dict(
        (key.lower(), value.strip())
        for key, value in re.findall(
            r"(?m)^[ \t]*([^#;=\r\n]+?)[ \t]*=[ \t]*([^\r\n]*)$",
            section,
        )
    )
    if (
        settings.get("valid users") == f"@{expected_group}"
        and settings.get("force group") == expected_group
    ):
        return expected_group
    return None


def _remove_managed_share_sections(content: str) -> tuple[str, set[str]]:
    """Remove current and legacy basaltwater-managed share sections."""

    managed_groups: set[str] = set()
    block_pattern = re.compile(
        r"(?ms)^[ \t]*" + re.escape(MANAGED_SHARES_BEGIN) + r"[ \t]*\r?\n"
        r".*?^[ \t]*" + re.escape(MANAGED_SHARES_END) + r"[ \t]*(?:\r?\n|\Z)"
    )
    for block in block_pattern.findall(content):
        for section in re.findall(r"(?ms)^[ \t]*\[[^\]\r\n]+\].*?(?=^[ \t]*\[|\Z)", block):
            group_name = _managed_share_group(section)
            if group_name:
                managed_groups.add(group_name)
    content = block_pattern.sub("", content)

    section_pattern = re.compile(r"(?ms)^[ \t]*\[[^\]\r\n]+\].*?(?=^[ \t]*\[|\Z)")

    def remove_legacy(match: re.Match[str]) -> str:
        group_name = _managed_share_group(match.group(0))
        if not group_name:
            return match.group(0)
        managed_groups.add(group_name)
        return ""

    return section_pattern.sub(remove_legacy, content).rstrip(), managed_groups


def reconcile_samba_shares(config: SetupConfig, **_: Any) -> None:
    """Make managed Samba shares exactly match ``config.samba_shares``."""

    validate_samba_share_specs(config.samba_shares, config.share_credentials)
    credentials = parse_share_credentials(config.share_credentials)
    share_configs = [
        parse_share_spec(share_spec, credentials)
        for share_spec in config.samba_shares or []
    ]

    if not os.path.exists(SMB_CONF_PATH):
        raise RuntimeError("Samba is not installed; run setup with --samba first")

    desired_groups: set[str] = set()
    sections: list[str] = []
    for share_config in share_configs:
        for share_path in cast(list[str], share_config["paths"]):
            assert_declared_storage_mount(config, share_path)
        group_name, section = _prepare_samba_share(config, share_config)
        desired_groups.add(group_name)
        sections.append(section)

    with open(SMB_CONF_PATH, "r", encoding="utf-8") as file_obj:
        previous_content = file_obj.read()
    unmanaged_content, previous_groups = _remove_managed_share_sections(previous_content)

    managed_block = ""
    if sections:
        managed_block = (
            f"{MANAGED_SHARES_BEGIN}\n"
            + "\n".join(sections)
            + f"{MANAGED_SHARES_END}\n"
        )
    desired_content = unmanaged_content
    if managed_block:
        desired_content += ("\n\n" if desired_content else "") + managed_block
    elif desired_content:
        desired_content += "\n"

    if desired_content != previous_content:
        if not _write_validated_smb_config(
            SMB_CONF_PATH,
            previous_content,
            desired_content,
        ):
            raise RuntimeError("Samba share configuration validation failed")
        run("systemctl reload smbd")
        print(f"  ✓ Reconciled {len(sections)} Samba share(s)")
    else:
        print(f"  ✓ {len(sections)} Samba share(s) already up to date")

    for obsolete_group in sorted(previous_groups - desired_groups):
        run(f"groupdel {shlex.quote(obsolete_group)}", check=False)


SAMBA_GLOBAL_HARDENED_SETTINGS: dict[str, str] = {
    # Pin to SMB3+ everywhere. SMB2 is allowed only by Vista/Server 2008.
    # Current Linux kernels, macOS, and Windows 8+ all speak SMB3.
    "server min protocol": "SMB3",
    "client min protocol": "SMB3",
    # Mandate signing and encryption to prevent tampering / sniffing on
    # the wire. Both are SMB3 features and should "just work" against
    # current Linux/macOS/Windows clients.
    "server signing": "mandatory",
    "client signing": "mandatory",
    "smb encrypt": "required",
    # Disable the legacy NetBIOS-over-TCP transport entirely; modern SMB
    # only uses 445/tcp. This also prevents nmbd from binding 137/138/139.
    "disable netbios": "yes",
    "workgroup": "WORKGROUP",
    "server string": "Samba Server",
    # Per-machine log files following the upstream `log.%m` template so
    # that the fail2ban `log.*` glob actually catches them; the previous
    # `%m.log` template produced names like `BUILDPC.log` that the glob
    # would not match.
    "log file": "/var/log/samba/log.%m",
    "max log size": "50",
    "log level": "1 auth:3",
    "security": "user",
    "map to guest": "Never",
    "guest account": "nobody",
    "restrict anonymous": "2",
    "obey pam restrictions": "yes",
    "unix password sync": "yes",
    "pam password change": "yes",
    # This cache contains disposable Samba TDB metadata, not share contents.
    # Keep an explicit default so clearing a custom SSD-backed path reliably
    # reconciles the service back to the distro location.
    "cache directory": "/var/cache/samba",
}


# Modern smbd (4.x) emits a single structured "Auth:" line for every
# authentication attempt that includes both the NT_STATUS_* result and
# `remote host [ipv4:<addr>:<port>]` / `[ipv6:[<addr>]:<port>]`. The
# filter targets only failure statuses so successful logins are not
# counted toward the ban threshold.
SAMBA_FAIL2BAN_FILTER = """[INCLUDES]
before = common.conf

[Definition]
_daemon = smbd
failregex = ^.*Auth: .*status \\[NT_STATUS_(?:WRONG_PASSWORD|NO_SUCH_USER|ACCOUNT_(?:DISABLED|LOCKED_OUT|RESTRICTION)|LOGON_FAILURE|ACCESS_DENIED)\\].*remote host \\[ipv[46]:?<HOST>(?::\\d+)?\\]
ignoreregex =

[Init]
journalmatch = _SYSTEMD_UNIT=smbd.service
"""


# 445/tcp only — port 139 is no longer opened by configure_samba_firewall
# and `disable netbios = yes` keeps nmbd from binding the legacy ports.
SAMBA_FAIL2BAN_JAIL = """[samba-auth]
enabled = true
port = 445
protocol = tcp
filter = samba-auth
logpath = /var/log/samba/log.smbd
          /var/log/samba/log.*
backend = auto
maxretry = 5
bantime = 3600
findtime = 600
"""


def _write_validated_smb_config(
    smb_conf: str,
    previous_content: str,
    desired_content: str,
) -> bool:
    """Write a Samba config candidate and restore it if testparm rejects it."""

    with open(smb_conf, "w") as file_obj:
        file_obj.write(desired_content)

    result = run(
        f"testparm -s {shlex.quote(smb_conf)}",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return True

    with open(smb_conf, "w") as file_obj:
        file_obj.write(previous_content)

    print("  ✗ Samba configuration has errors; restored the previous configuration")
    if result.stderr:
        print(f"  Error: {result.stderr[:200]}")
    return False


def _render_hardened_global_settings(
    content: str,
    settings: Optional[dict[str, str]] = None,
) -> str:
    """Update only the [global] section while preserving all share settings."""

    desired_settings = settings or SAMBA_GLOBAL_HARDENED_SETTINGS

    section_pattern = re.compile(
        r"(?ims)^(?P<header>[ \t]*\[global\][^\r\n]*)(?:\r?\n|$)"
        r"(?P<body>.*?)(?=^[ \t]*\[[^\]\r\n]+\][^\r\n]*(?:\r?\n|$)|\Z)"
    )
    match = section_pattern.search(content)
    hardened_lines = "".join(
        f"   {key} = {value}\n"
        for key, value in desired_settings.items()
    )

    if not match:
        return "[global]\n" + hardened_lines + "\n" + content

    body = match.group("body")
    # Samba 4.21 warns for the old `null passwords` setting even when it is
    # set to `no`. Authentication is already fail-closed through `map to
    # guest = Never`, so remove the obsolete option from managed globals.
    for key in (*desired_settings, *SAMBA_REMOVED_GLOBAL_SETTINGS):
        setting_pattern = re.compile(
            r"(?im)^[ \t]*" + re.escape(key) + r"[ \t]*=[^\r\n]*(?:\r?\n|$)"
        )
        body = setting_pattern.sub("", body)

    desired_section = match.group("header") + "\n" + hardened_lines + body
    return content[:match.start()] + desired_section + content[match.end():]


def configure_samba_global_settings(config: SetupConfig) -> None:
    smb_conf = SMB_CONF_PATH
    cache_path = config.samba_metadata_cache or "/var/cache/samba"
    settings = dict(SAMBA_GLOBAL_HARDENED_SETTINGS)
    settings["cache directory"] = cache_path

    run(
        "install -d -m 0750 -o root -g root -- "
        f"{shlex.quote(cache_path)}"
    )
    
    if not os.path.exists(smb_conf):
        run(f"touch {shlex.quote(smb_conf)}")
    
    with open(smb_conf, 'r') as f:
        content = f.read()
    
    desired_content = _render_hardened_global_settings(content, settings)
    if desired_content == content:
        print("  ✓ Global Samba configuration already up to date")
        return

    if not _write_validated_smb_config(smb_conf, content, desired_content):
        return

    run("systemctl reload smbd")
    print("  ✓ Updated global Samba configuration with security hardening")



def configure_samba_fail2ban(config: SetupConfig) -> None:
    jail_path = SAMBA_FAIL2BAN_JAIL_PATH
    filter_path = SAMBA_FAIL2BAN_FILTER_PATH

    if not can_manage_firewall(config.machine_type):
        removed_managed_config = False
        for path in (jail_path, filter_path):
            if not os.path.exists(path):
                continue
            try:
                os.remove(path)
                removed_managed_config = True
            except OSError as exc:
                print(f"  ⚠ Could not remove container-inapplicable {path}: {exc}")

        if removed_managed_config and is_package_installed("fail2ban"):
            result = run(
                "systemctl try-restart fail2ban",
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                print("  ⚠ Could not unload the old Samba fail2ban jail")
        print("  ✓ Samba fail2ban skipped (container cannot enforce firewall bans)")
        return

    # Older versions of this tool wrote /etc/fail2ban/jail.d/samba.local and
    # /etc/fail2ban/filter.d/samba.conf. The latter clobbered the distro
    # filter shipped by the fail2ban package (a dpkg conffile), which is
    # both rude and confusing on upgrades. Remove the legacy files so the
    # distro-shipped samba filter is restored on next package install.
    legacy_files = [
        "/etc/fail2ban/jail.d/samba.local",
        "/etc/fail2ban/filter.d/samba.conf",
    ]
    for path in legacy_files:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    if not is_package_installed("fail2ban"):
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq fail2ban")

    # Modern smbd (4.x) emits a single structured "Auth:" line for every
    # authentication attempt that includes both the NT_STATUS_* result and
    # `remote host [ipv4:<addr>:<port>]` / `[ipv6:[<addr>]:<port>]`. The
    # filter targets only failure statuses so successful logins are not
    # counted toward the ban threshold.
    fail2ban_samba_filter = SAMBA_FAIL2BAN_FILTER

    # 445/tcp only — port 139 is no longer opened by configure_samba_firewall
    # and `disable netbios = yes` keeps nmbd from binding the legacy ports.
    fail2ban_samba_jail = SAMBA_FAIL2BAN_JAIL

    os.makedirs("/etc/fail2ban/filter.d", exist_ok=True)
    os.makedirs("/etc/fail2ban/jail.d", exist_ok=True)

    with open(filter_path, "w") as f:
        f.write(fail2ban_samba_filter)

    with open(jail_path, "w") as f:
        f.write(fail2ban_samba_jail)

    run("systemctl enable fail2ban", check=False)
    run("systemctl restart fail2ban")

    print("  ✓ fail2ban configured for Samba (5 failed attempts = 1 hour ban)")
