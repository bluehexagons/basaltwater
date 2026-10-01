"""Security hardening steps."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import shutil
from urllib.parse import quote

from lib.atomic_io import write_text_atomic
from lib.maintenance_systemd import configure_maintenance_timer
from lib.kernel_restart import install_kernel_restart_hook
from lib.config import SetupConfig
from lib.maintenance_defaults import JOURNAL_MAX_USE
from lib.machine_state import (
    can_manage_mdns,
    can_modify_kernel,
    is_container,
    is_hardware,
    is_vm,
)
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_network_ip_or_cidr
from lib.validators import validate_ip_address, validate_username

_LEGACY_UNATTENDED_ORIGINS_FILE = "/etc/apt/apt.conf.d/52basaltwater-unattended-upgrades"
_LEGACY_MANAGED_ORIGINS_FILE = "/etc/basaltwater/unattended_upgrades_origins.list"
_JOURNAL_CONF_DIR = "/etc/systemd/journald.conf.d"
_JOURNAL_CONF_FILE = f"{_JOURNAL_CONF_DIR}/basaltwater.conf"
_SSHD_DROPIN_DIR = "/etc/ssh/sshd_config.d"
_SSHD_DROPIN_FILE = f"{_SSHD_DROPIN_DIR}/00-basaltwater-hardening.conf"
_LEGACY_SSHD_DROPIN_FILE = f"{_SSHD_DROPIN_DIR}/99-basaltwater-hardening.conf"
_SYSCTL_HARDENING_FILE = "/etc/sysctl.d/99-security-hardening.conf"
_FAIL2BAN_SSHD_JAIL = "/etc/fail2ban/jail.d/sshd.local"
_FAIL2BAN_XRDP_JAIL = "/etc/fail2ban/jail.d/xrdp.local"
_FAIL2BAN_XRDP_FILTER = "/etc/fail2ban/filter.d/xrdp.conf"
_AUDIT_RULES_FILE = "/etc/audit/rules.d/99-basaltwater.rules"
_FAILLOCK_CONF = "/etc/security/faillock.conf"
_PAM_FAILLOCK_PROFILE = "/usr/share/pam-configs/faillock-basaltwater"
_PAM_COMMON_AUTH = "/etc/pam.d/common-auth"
_PAM_COMMON_ACCOUNT = "/etc/pam.d/common-account"
_ISSUE_BANNER = "Authorized access only. All activity is monitored and logged.\n"
_SECURITY_MONITOR_SCRIPT = "/opt/basaltwater/security/service_tools/security_monitor.py"
_SSH_RULE_COMMENT_PREFIX = "basaltwater SSH"
_RDP_RULE_COMMENT_PREFIX = "basaltwater RDP"
_WEB_RULE_COMMENT_PREFIX = "basaltwater web TCP"
_MDNS_RULE_COMMENT_PREFIX = "basaltwater mDNS UDP"
_PROXMOX_MANAGEMENT_COMMENT_PREFIX = "basaltwater access source"
_UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]")
_UFW_BROAD_INBOUND_RE = re.compile(
    r"^\[\s*\d+\]\s+(.+?)\s+(?:ALLOW|LIMIT)\s+IN\s+Anywhere\b"
)
_PAM_ACTIVE_LINES = (
    re.compile(r"^\s*auth\s+\[default=die\]\s+pam_faillock\.so\s+authfail\b"),
    re.compile(r"^\s*account\s+required\s+pam_faillock\.so\b"),
)
_APPARMOR_USERNS_PROFILE = "/etc/apparmor.d/unprivileged_userns"
_APPARMOR_USERNS_RESTRICTION = (
    "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
)


def _run_ufw(command: str, *, check: bool = False):
    """Run one UFW command without printing routine success messages."""

    return run(command, check=check, capture_output=True)


def _ufw_failure(message: str, result: object) -> RuntimeError:
    """Include captured UFW diagnostics when a required mutation fails."""

    detail = (
        getattr(result, "stderr", None)
        or getattr(result, "stdout", None)
        or ""
    ).strip()
    return RuntimeError(f"{message}: {detail}" if detail else message)


def _apparmor_userns_restriction_enabled() -> bool:
    """Return whether AppArmor mediates unprivileged user namespaces."""
    try:
        with open(_APPARMOR_USERNS_RESTRICTION, "r", encoding="utf-8") as setting:
            return setting.read().strip() == "1"
    except OSError:
        return False


def _ensure_browser_automation_userns_profile() -> bool:
    """Load Debian's capability-stripping profile for browser sandboxes.

    AppArmor 4 can transition otherwise-unconfined applications into the
    ``unprivileged_userns`` profile when they create a user namespace.  This
    supports browsers downloaded by Playwright and similar tools without a
    broad attachment rule over user-writable cache directories.
    """
    if not _apparmor_userns_restriction_enabled():
        return True
    if not os.path.isfile(_APPARMOR_USERNS_PROFILE):
        print(
            "  ⚠ AppArmor restricts user namespaces but its compatibility "
            "profile is missing"
        )
        return False

    result = run(
        f"apparmor_parser -r -W {shlex.quote(_APPARMOR_USERNS_PROFILE)}",
        check=False,
    )
    if result.returncode != 0:
        print(
            "  ⚠ Could not load AppArmor's unprivileged-user-namespace "
            "profile; browser automation sandboxes may not start"
        )
        return False
    return True


def create_remoteusers_group(config: SetupConfig) -> None:
    """Create remoteusers group for SSH and RDP access control."""
    result = run("getent group remoteusers", check=False)
    group_exists = result.returncode == 0
    
    if not group_exists:
        run("groupadd remoteusers")
    
    result = run("id -nG root | grep -qw remoteusers", check=False)
    if result.returncode != 0:
        run("usermod -aG remoteusers root")
        print("  ✓ remoteusers group created and root user added")
    else:
        print("  ✓ remoteusers group already exists with root user")


def _rdp_firewall_rules(config: SetupConfig) -> list[tuple[str, str]]:
    """Return validated UFW comment/command pairs for the requested RDP policy."""
    sources = [
        validate_network_ip_or_cidr(source, "RDP source")
        for source in config.effective_rdp_sources()
    ]
    if not sources:
        comment = f"{_RDP_RULE_COMMENT_PREFIX} global"
        return [(comment, f"ufw limit 3389/tcp comment {shlex.quote(comment)}")]

    rules: list[tuple[str, str]] = []
    for source in sources:
        comment = f"{_RDP_RULE_COMMENT_PREFIX} source {source}"
        rules.append(
            (
                comment,
                "ufw limit from "
                f"{shlex.quote(source)} to any port 3389 proto tcp "
                f"comment {shlex.quote(comment)}",
            )
        )
    return rules


def _remove_stale_managed_rules(
    comment_prefix: str,
    desired_comments: set[str],
) -> None:
    """Remove obsolete comment-tagged rules in descending UFW order."""
    result = run("ufw status numbered", check=False, capture_output=True)
    stdout = getattr(result, "stdout", None)
    if result.returncode != 0 or not isinstance(stdout, str):
        return

    stale_rule_numbers: list[int] = []
    for line in stdout.splitlines():
        if "#" not in line:
            continue
        comment = line.split("#", 1)[1].strip()
        if comment != comment_prefix and not comment.startswith(f"{comment_prefix} "):
            continue
        if comment in desired_comments:
            continue
        match = _UFW_NUMBERED_RULE_RE.match(line.strip())
        if match:
            stale_rule_numbers.append(int(match.group(1)))

    for rule_number in sorted(stale_rule_numbers, reverse=True):
        _run_ufw(f"ufw --force delete {rule_number}")


def _configure_rdp_firewall(config: SetupConfig) -> None:
    """Apply RDP rules without removing broad access before replacements exist."""
    rules = _rdp_firewall_rules(config)
    has_restricted_sources = bool(config.effective_rdp_sources())

    if not has_restricted_sources:
        # A legacy untagged limit rule is indistinguishable from the desired
        # global rule to UFW. Replace it so future reruns can reconcile by tag.
        _run_ufw("ufw delete allow 3389/tcp")
        _run_ufw("ufw delete limit 3389/tcp")

    for _comment, command in rules:
        result = _run_ufw(command)
        if result.returncode != 0:
            if not has_restricted_sources:
                # Preserve the pre-existing reachability contract if tagging
                # the replacement global rule unexpectedly fails.
                _run_ufw("ufw limit 3389/tcp")
            raise _ufw_failure(
                "Failed to install the requested RDP firewall rule",
                result,
            )

    if has_restricted_sources:
        # Replacements are active before broad legacy access is removed.
        _run_ufw("ufw delete allow 3389/tcp")
        _run_ufw("ufw delete limit 3389/tcp")

    _remove_stale_managed_rules(
        _RDP_RULE_COMMENT_PREFIX,
        {comment for comment, _command in rules},
    )


def _configure_ssh_firewall(config: SetupConfig) -> None:
    """Allow trusted SSH sources or rate-limit unrestricted SSH access."""

    sources = [
        validate_network_ip_or_cidr(source, "SSH source")
        for source in config.effective_access_sources()
    ]
    if not sources:
        result = _run_ufw("ufw limit ssh")
        if result.returncode != 0:
            raise _ufw_failure(
                "Failed to install the requested SSH firewall rule",
                result,
            )
        _remove_stale_managed_rules(_SSH_RULE_COMMENT_PREFIX, set())
        return

    desired_comments: set[str] = set()
    for source in sources:
        comment = f"{_SSH_RULE_COMMENT_PREFIX} trusted source {source}"
        result = _run_ufw(
            "ufw allow from "
            f"{shlex.quote(source)} to any port 22 proto tcp "
            f"comment {shlex.quote(comment)}"
        )
        if result.returncode != 0:
            raise _ufw_failure(
                "Failed to install the requested SSH firewall rule",
                result,
            )
        desired_comments.add(comment)

    for broad_rule in ("allow ssh", "limit ssh", "allow 22/tcp", "limit 22/tcp"):
        _run_ufw(f"ufw delete {broad_rule}")
    _remove_stale_managed_rules(_SSH_RULE_COMMENT_PREFIX, desired_comments)


def _configure_managed_web_ports(config: SetupConfig) -> list[int]:
    """Reconcile basaltwater-managed TCP web ports."""

    ports = config.effective_web_ports()
    sources = [
        validate_network_ip_or_cidr(source, "web port source")
        for source in config.effective_access_sources()
    ]
    desired_comments: set[str] = set()
    for port in ports:
        if sources:
            for source in sources:
                comment = f"{_WEB_RULE_COMMENT_PREFIX} {port} source {source}"
                result = _run_ufw(
                    "ufw allow from "
                    f"{shlex.quote(source)} to any port {port} proto tcp "
                    f"comment {shlex.quote(comment)}"
                )
                if result.returncode != 0:
                    raise _ufw_failure(
                        f"Failed to install web firewall rule for TCP {port}",
                        result,
                    )
                desired_comments.add(comment)
            # Replacements are active before the old broad rule is removed.
            _run_ufw(f"ufw delete allow {port}/tcp")
        else:
            comment = f"{_WEB_RULE_COMMENT_PREFIX} {port}"
            result = _run_ufw(
                f"ufw allow {port}/tcp comment {shlex.quote(comment)}"
            )
            if result.returncode != 0:
                raise _ufw_failure(
                    f"Failed to install web firewall rule for TCP {port}",
                    result,
                )
            desired_comments.add(comment)

    _remove_stale_managed_rules(_WEB_RULE_COMMENT_PREFIX, desired_comments)
    return ports


def _configure_mdns_firewall(config: SetupConfig) -> None:
    """Allow Avahi's local-network multicast traffic through UFW."""

    if not (config.enable_mdns or config.clear_mdns):
        return

    if config.enable_mdns and not can_manage_mdns():
        print(
            "  ✓ Skipping mDNS firewall rule "
            "(OCI containers cannot run a target system service)"
        )
        return

    if not config.enable_mdns:
        _remove_stale_managed_rules(_MDNS_RULE_COMMENT_PREFIX, set())
        return

    comment = _MDNS_RULE_COMMENT_PREFIX
    result = _run_ufw(
        f"ufw allow 5353/udp comment {shlex.quote(comment)}"
    )
    if result.returncode != 0:
        raise _ufw_failure("Failed to install the mDNS firewall rule", result)
    _remove_stale_managed_rules(_MDNS_RULE_COMMENT_PREFIX, {comment})


def _verify_source_restricted_firewall(
    config: SetupConfig, *, web_ports: list[int], include_rdp: bool
) -> None:
    """Fail setup if an active UFW rule still opens a restricted port globally."""
    restricted_ports: dict[str, str] = {}
    if config.effective_access_sources():
        restricted_ports.update({"22": "SSH", "OpenSSH": "SSH"})
        restricted_ports.update({str(port): f"web TCP {port}" for port in web_ports})
    if include_rdp and config.effective_rdp_sources():
        restricted_ports["3389"] = "RDP"
    if not restricted_ports:
        return

    result = run("ufw status numbered", check=False, capture_output=True)
    stdout = getattr(result, "stdout", None)
    if result.returncode != 0 or not isinstance(stdout, str) or "Status: active" not in stdout:
        raise _ufw_failure("Could not verify source-restricted firewall policy", result)

    for line in stdout.splitlines():
        match = _UFW_BROAD_INBOUND_RE.match(line.strip())
        if not match:
            continue
        destination = re.sub(r"\s+\(v6\)$", "", match.group(1))
        destination = destination.removesuffix("/tcp")
        service = restricted_ports.get(destination)
        if service:
            raise RuntimeError(
                f"Source-restricted {service} firewall policy still has a broad UFW rule: "
                f"{line.strip()}"
            )


def configure_firewall(config: SetupConfig) -> None:
    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    firewall_active = result.returncode == 0

    if not firewall_active:
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq ufw")
        for command in ("ufw default deny incoming", "ufw default allow outgoing"):
            policy_result = _run_ufw(command, check=True)
            if policy_result.returncode != 0:
                raise _ufw_failure("Failed to configure firewall defaults", policy_result)
    _configure_ssh_firewall(config)
    if config.enable_rdp:
        _configure_rdp_firewall(config)
    web_ports = _configure_managed_web_ports(config)
    _configure_mdns_firewall(config)

    if firewall_active:
        _verify_source_restricted_firewall(
            config, web_ports=web_ports, include_rdp=config.enable_rdp
        )
        if web_ports:
            print(
                "  ✓ Firewall already active; web ports reconciled: "
                + ", ".join(str(port) for port in web_ports)
            )
        else:
            print("  ✓ Firewall already configured")
        return

    result = _run_ufw("ufw --force enable")
    if result.returncode != 0:
        if is_container():
            print("  ⚠ Firewall could not be enabled (container may lack capabilities)")
        else:
            raise RuntimeError("Firewall could not be enabled (check command output)")
        return

    _verify_source_restricted_firewall(
        config, web_ports=web_ports, include_rdp=config.enable_rdp
    )
    ssh_policy = (
        "SSH source-restricted"
        if config.effective_access_sources()
        else "SSH rate-limited"
    )
    if web_ports:
        print(
            f"  ✓ Firewall configured ({ssh_policy}; web TCP ports: "
            + ", ".join(str(port) for port in web_ports)
            + ")"
        )
    elif config.enable_rdp:
        if config.effective_rdp_sources():
            print(
                f"  ✓ Firewall configured ({ssh_policy}; RDP source-restricted)"
            )
        else:
            print(
                f"  ✓ Firewall configured ({ssh_policy}; global RDP rate-limited)"
            )
    else:
        print(f"  ✓ Firewall configured ({ssh_policy})")


def configure_fail2ban(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would configure fail2ban jails")
        return

    if is_container():
        print("  ✓ Skipping fail2ban configuration (limited functionality in containers)")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq fail2ban")

    os.makedirs("/etc/fail2ban/filter.d", exist_ok=True)
    os.makedirs("/etc/fail2ban/jail.d", exist_ok=True)

    # SSH jail uses the upstream-shipped /etc/fail2ban/filter.d/sshd.conf which
    # is kept up to date by the Debian package.
    sshd_jail = """[sshd]
enabled = true
port = ssh
filter = sshd
backend = systemd
maxretry = 5
findtime = 600
bantime = 3600
"""

    with open(_FAIL2BAN_SSHD_JAIL, "w") as f:
        f.write(sshd_jail)

    if config.enable_rdp:
        # Filter modeled on upstream xrdp/instfiles/fail2ban/xrdp.conf and
        # adapted for xrdp 0.10 on Debian Trixie. AUTHFAIL is the canonical
        # failed-login marker emitted by xrdp-sesman.
        fail2ban_xrdp_filter = """# Fail2Ban filter for xrdp authentication failures
# Matches xrdp-sesman AUTHFAIL events emitted by xrdp >= 0.9.
[INCLUDES]
before = common.conf

[Definition]
_daemon = xrdp(-sesman)?

failregex = ^.*AUTHFAIL: user=\\S+ ip=<HOST>(?::\\d+)?\\s.*$
            ^.*\\[INFO \\]\\s+login failed for user \\S+ from <HOST>.*$
            ^.*\\[INFO \\]\\s+connection refused for user \\S+ from <HOST>.*$
ignoreregex =
"""

        fail2ban_xrdp_jail = """[xrdp]
enabled = true
port = 3389
protocol = tcp
filter = xrdp
logpath = /var/log/xrdp-sesman.log
           /var/log/xrdp.log
maxretry = 3
bantime = 3600
findtime = 600
"""

        with open(_FAIL2BAN_XRDP_FILTER, "w") as f:
            f.write(fail2ban_xrdp_filter)

        with open(_FAIL2BAN_XRDP_JAIL, "w") as f:
            f.write(fail2ban_xrdp_jail)

    run("systemctl enable fail2ban")
    run("systemctl restart fail2ban")

    if config.enable_rdp:
        print("  ✓ fail2ban configured (sshd + xrdp jails, 1 hour ban)")
    else:
        print("  ✓ fail2ban configured (sshd jail, 1 hour ban)")


def _verify_ssh_policy(sshd_path: str, config: SetupConfig) -> None:
    """Check effective authentication for root and the setup identity."""
    address = "127.0.0.1"
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if connection:
        if len(connection) != 4 or not validate_ip_address(connection[0]):
            raise RuntimeError("Cannot determine the SSH setup peer")
        address = connection[0]
    for username in dict.fromkeys(("root", config.username)):
        if not validate_username(username):
            raise ValueError("Cannot verify SSH policy for an invalid username")
        context = f"user={username},host={address},addr={address}"
        effective = run(
            f"{shlex.quote(sshd_path)} -T -C {shlex.quote(context)}",
            check=False,
            capture_output=True,
        )
        if effective.returncode != 0:
            raise RuntimeError("Could not inspect effective SSH configuration")
        settings = {}
        for line in (effective.stdout or "").splitlines():
            key, separator, value = line.partition(" ")
            if separator:
                settings[key] = value.strip()
        required = {
            "passwordauthentication": {"no"},
            "kbdinteractiveauthentication": {"no"},
            "pubkeyauthentication": {"yes"},
            "permitrootlogin": {"prohibit-password", "without-password"},
            "allowgroups": {"remoteusers"},
            "authenticationmethods": {"publickey"},
        }
        if config.harden_user and username == config.username:
            required.update({"disableforwarding": {"yes"}, "permituserrc": {"no"}})
        conflicts = [key for key, values in required.items() if settings.get(key) not in values]
        if conflicts:
            raise RuntimeError(
                f"Effective SSH policy for {username} conflicts with hardening: "
                + ", ".join(conflicts)
            )
        groups = run(f"id -nG {shlex.quote(username)}", check=False, capture_output=True)
        if groups.returncode != 0 or "remoteusers" not in (groups.stdout or "").split():
            raise RuntimeError(f"SSH identity {username} is not a member of remoteusers")


def harden_ssh(config: SetupConfig) -> None:
    """Apply SSH hardening via a drop-in file under /etc/ssh/sshd_config.d/.

    Using a drop-in keeps the distro-shipped sshd_config untouched and makes
    the hardening idempotent across reruns and OpenSSH upgrades that move
    settings between files. An early filename takes precedence over normal
    distro drop-ins; effective-policy checks detect any earlier overrides.
    """
    hardening_content = """# Managed by basaltwater - SSH hardening drop-in.
# Drop-ins under /etc/ssh/sshd_config.d/*.conf are read before the main
# sshd_config; the first-match-wins rule means these directives override
# anything later in /etc/ssh/sshd_config.
PermitRootLogin prohibit-password
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
PermitEmptyPasswords no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
LoginGraceTime 30
AllowGroups remoteusers
"""
    if config.harden_user:
        if not validate_username(config.username):
            raise ValueError(
                "Cannot apply hardened SSH user policy to an invalid username"
            )
        hardening_content += f"""
# Restrict the coding identity without disabling authorized-key shell access.
Match User {config.username}
    DisableForwarding yes
    PermitUserRC no
Match all
"""

    if is_dry_run():
        print("  [DRY-RUN] Would apply the managed SSH hardening drop-in")
        return

    sshd_path = shutil.which("sshd")
    if sshd_path is None:
        for candidate in ("/usr/sbin/sshd", "/usr/lib/openssh/sshd"):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                sshd_path = candidate
                break
    if sshd_path is None:
        print("  ✓ Skipping SSH hardening (openssh-server is not installed)")
        return

    try:
        os.makedirs(_SSHD_DROPIN_DIR, exist_ok=True)
    except OSError as exc:
        print(
            "  ⚠ Skipping SSH hardening (cannot access "
            f"{_SSHD_DROPIN_DIR}: {exc})"
        )
        return

    existing: str | None = None
    if os.path.exists(_SSHD_DROPIN_FILE):
        try:
            with open(_SSHD_DROPIN_FILE, "r") as f:
                existing = f.read()
        except OSError as exc:
            print(f"  ⚠ Could not read existing SSH hardening drop-in; leaving it unchanged: {exc}")
            return
    changed = existing != hardening_content

    try:
        if changed:
            write_text_atomic(_SSHD_DROPIN_FILE, hardening_content, mode=0o600)
    except OSError as exc:
        print(
            "  ⚠ Skipping SSH hardening (cannot write "
            f"{_SSHD_DROPIN_FILE}: {exc})"
        )
        return

    # Validate the resulting config before reloading so we do not lock out
    # access if a future change introduces a typo.
    try:
        validate = run(f"{shlex.quote(sshd_path)} -t", check=False)
        if validate.returncode != 0:
            raise RuntimeError("sshd -t failed after hardening")
        _verify_ssh_policy(sshd_path, config)
    except Exception:
        try:
            if changed:
                if existing is None:
                    os.remove(_SSHD_DROPIN_FILE)
                else:
                    write_text_atomic(_SSHD_DROPIN_FILE, existing, mode=0o600)
        except OSError as exc:
            raise RuntimeError("Failed to restore previous SSH configuration") from exc
        raise

    # Remove only our former drop-in after the replacement is validated.
    try:
        with open(_LEGACY_SSHD_DROPIN_FILE, encoding="utf-8") as legacy:
            legacy_content = legacy.read()
    except FileNotFoundError:
        legacy_content = ""
    if legacy_content.startswith("# Managed by basaltwater - SSH hardening drop-in."):
        os.remove(_LEGACY_SSHD_DROPIN_FILE)
        changed = True
    if changed:
        run("systemctl reload sshd || systemctl reload ssh", check=True)

    details = "key-only auth, timeouts, AllowGroups remoteusers"
    if config.harden_user:
        details += ", coding-user forwarding disabled"
    print(f"  ✓ SSH hardening verified (drop-in: {details})")


def harden_kernel(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would apply kernel hardening parameters")
        return

    if not can_modify_kernel():
        print("  ✓ Skipping kernel hardening (host kernel manages these settings)")
        return

    rp_filter_hardening = """net.ipv4.conf.default.rp_filter=1
net.ipv4.conf.all.rp_filter=1
"""
    if config.system_type == "server_proxmox":
        # A Proxmox host may route, NAT, or bridge traffic for guests. Strict
        # reverse-path filtering drops valid asymmetric guest traffic, so keep
        # the kernel's permissive default for this control-plane role.
        rp_filter_hardening = """# Proxmox hosts may route, NAT, or bridge guest traffic.
# Keep reverse-path filtering disabled to allow asymmetric guest paths.
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.all.rp_filter=0
"""

    kernel_hardening = f"""# Managed by basaltwater - kernel security hardening.
# Network security
{rp_filter_hardening}net.ipv4.tcp_syncookies=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.secure_redirects=0
net.ipv4.conf.default.secure_redirects=0
net.ipv6.conf.all.accept_redirects=0
net.ipv6.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.default.send_redirects=0
net.ipv4.icmp_echo_ignore_broadcasts=1
net.ipv4.icmp_ignore_bogus_error_responses=1
net.ipv4.conf.all.log_martians=1
net.ipv4.conf.default.log_martians=1
net.ipv4.conf.all.accept_source_route=0
net.ipv4.conf.default.accept_source_route=0
net.ipv6.conf.all.accept_source_route=0
net.ipv6.conf.default.accept_source_route=0

# Kernel security
kernel.dmesg_restrict=1
kernel.kptr_restrict=2
kernel.yama.ptrace_scope=1
kernel.unprivileged_bpf_disabled=1
net.core.bpf_jit_harden=2
fs.suid_dumpable=0
fs.protected_hardlinks=1
fs.protected_symlinks=1
fs.protected_fifos=2
fs.protected_regular=2
kernel.core_uses_pid=1
"""

    if os.path.exists(_SYSCTL_HARDENING_FILE):
        try:
            with open(_SYSCTL_HARDENING_FILE, "r") as f:
                existing = f.read()
        except OSError:
            existing = None
        if existing == kernel_hardening:
            print("  ✓ Kernel already hardened")
            return

    with open(_SYSCTL_HARDENING_FILE, "w") as f:
        f.write(kernel_hardening)

    result = run(f"sysctl -p {_SYSCTL_HARDENING_FILE}", check=False)
    if result.returncode != 0:
        print("  ⚠ Some kernel parameters may not have applied (check logs)")

    print("  ✓ Kernel hardened (network protection, security restrictions)")


def configure_login_banners(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would configure login banners")
        return

    changed = False
    for path in ("/etc/issue", "/etc/issue.net"):
        try:
            with open(path) as f:
                existing = f.read()
        except OSError:
            existing = None
        if existing != _ISSUE_BANNER:
            with open(path, "w") as f:
                f.write(_ISSUE_BANNER)
            changed = True

    if changed:
        print("  ✓ Login banners configured (authorized-use notice)")
    else:
        print("  ✓ Login banners already configured")


def configure_apparmor(config: SetupConfig) -> None:
    if not (is_vm() or is_hardware()):
        print("  ✓ Skipping AppArmor setup (privileged containers inherit host AppArmor)")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq apparmor apparmor-utils")
    run("systemctl enable apparmor", check=False)

    enabled = run("aa-enabled -q", check=False).returncode == 0
    if not enabled:
        # Starting is safe for the oneshot AppArmor service. Avoid `restart`,
        # which systemd normally maps to stop/start and which AppArmor's unit
        # deliberately does not support as an unload/reload operation.
        run("systemctl start apparmor", check=False)
        enabled = run("aa-enabled -q", check=False).returncode == 0

    if not enabled:
        print(
            "  ✓ AppArmor configured for next boot (kernel policy is not "
            "currently active)"
        )
        return

    # The distro service is the canonical loader. It preserves each source
    # profile's enforce, complain, or unconfined mode and does not rewrite
    # package profiles or child profiles.
    reload_result = run("systemctl reload apparmor", check=False)
    if reload_result.returncode != 0:
        print("  ⚠ One or more AppArmor profiles failed to reload; check the journal")

    if not _ensure_browser_automation_userns_profile():
        raise RuntimeError(
            "AppArmor browser-sandbox compatibility profile failed to load"
        )

    if reload_result.returncode == 0:
        print(
            "  ✓ AppArmor enabled (package modes preserved; browser sandbox "
            "support verified)"
        )
    else:
        print("  ✓ AppArmor enabled (browser sandbox support verified)")


def configure_auditd(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would configure auditd rules")
        return

    if not (is_vm() or is_hardware()):
        print("  ✓ Skipping auditd (not applicable to containers)")
        return

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run("apt-get install -y -qq auditd audispd-plugins")

    audit_rules = """# Managed by basaltwater - audit rules.
# Identity and authentication files
-a always,exit -F arch=b64 -F path=/etc/passwd -F perm=wa -k identity
-a always,exit -F arch=b32 -F path=/etc/passwd -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/shadow -F perm=wa -k identity
-a always,exit -F arch=b32 -F path=/etc/shadow -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/group -F perm=wa -k identity
-a always,exit -F arch=b32 -F path=/etc/group -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/gshadow -F perm=wa -k identity
-a always,exit -F arch=b32 -F path=/etc/gshadow -F perm=wa -k identity
-a always,exit -F arch=b64 -F path=/etc/sudoers -F perm=wa -k sudoers
-a always,exit -F arch=b32 -F path=/etc/sudoers -F perm=wa -k sudoers
-a always,exit -F arch=b64 -F dir=/etc/sudoers.d/ -F perm=wa -k sudoers
-a always,exit -F arch=b32 -F dir=/etc/sudoers.d/ -F perm=wa -k sudoers

# SSH configuration changes
-a always,exit -F arch=b64 -F path=/etc/ssh/sshd_config -F perm=wa -k sshd_config
-a always,exit -F arch=b32 -F path=/etc/ssh/sshd_config -F perm=wa -k sshd_config
-a always,exit -F arch=b64 -F dir=/etc/ssh/sshd_config.d/ -F perm=wa -k sshd_config
-a always,exit -F arch=b32 -F dir=/etc/ssh/sshd_config.d/ -F perm=wa -k sshd_config

# Privileged command execution (setuid/setgid by non-root sessions)
-a always,exit -F arch=b64 -S execve -F euid=0 -F auid>=1000 -F auid!=-1 -k privileged
-a always,exit -F arch=b32 -S execve -F euid=0 -F auid>=1000 -F auid!=-1 -k privileged

# Kernel module loading/unloading
-a always,exit -F arch=b64 -F path=/sbin/insmod -F perm=x -k modules
-a always,exit -F arch=b32 -F path=/sbin/insmod -F perm=x -k modules
-a always,exit -F arch=b64 -F path=/sbin/rmmod -F perm=x -k modules
-a always,exit -F arch=b32 -F path=/sbin/rmmod -F perm=x -k modules
-a always,exit -F arch=b64 -F path=/sbin/modprobe -F perm=x -k modules
-a always,exit -F arch=b32 -F path=/sbin/modprobe -F perm=x -k modules
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -k modules

# Login and session tracking
-a always,exit -F arch=b64 -F path=/var/run/utmp -F perm=wa -k session
-a always,exit -F arch=b32 -F path=/var/run/utmp -F perm=wa -k session
-a always,exit -F arch=b64 -F path=/var/log/wtmp -F perm=wa -k session
-a always,exit -F arch=b32 -F path=/var/log/wtmp -F perm=wa -k session
-a always,exit -F arch=b64 -F path=/var/log/btmp -F perm=wa -k session
-a always,exit -F arch=b32 -F path=/var/log/btmp -F perm=wa -k session

# Enable audit (not immutable - allows future rule updates)
-e 1
"""

    os.makedirs("/etc/audit/rules.d", exist_ok=True)

    existing = None
    if os.path.exists(_AUDIT_RULES_FILE):
        try:
            with open(_AUDIT_RULES_FILE) as f:
                existing = f.read()
        except OSError:
            pass

    rules_changed = existing != audit_rules
    if rules_changed:
        write_text_atomic(_AUDIT_RULES_FILE, audit_rules, mode=0o640)

    run("systemctl enable auditd")
    # Debian's auditd unit deliberately refuses manual stop/restart. Starting
    # reconciles an inactive service; augenrules performs the supported live
    # rule reload below whether or not the on-disk rules changed.
    service_result = run("systemctl start auditd", check=False)
    load_result = run("augenrules --load", check=False)

    if service_result.returncode != 0:
        raise RuntimeError("auditd is required on this host but its service did not start")
    if load_result.returncode != 0:
        raise RuntimeError("auditd is required on this host but its rules did not load")
    if rules_changed:
        print("  ✓ auditd configured (monitoring identity, sudoers, SSH config, modules)")
    else:
        print("  ✓ auditd already configured; service and rules reconciled")


def _managed_text_matches(path: str, expected: str) -> bool:
    try:
        with open(path, encoding="utf-8") as file_obj:
            return file_obj.read() == expected
    except OSError:
        return False


def _pam_lockout_active() -> bool:
    """Confirm the generated PAM auth and account stacks use faillock."""

    for path, pattern in zip(
        (_PAM_COMMON_AUTH, _PAM_COMMON_ACCOUNT), _PAM_ACTIVE_LINES
    ):
        try:
            with open(path, encoding="utf-8") as file_obj:
                if not any(
                    pattern.search(line)
                    for line in file_obj
                    if not line.lstrip().startswith("#")
                ):
                    return False
        except OSError:
            return False
    return True


def configure_pam_lockout(config: SetupConfig) -> None:
    if is_dry_run():
        print("  [DRY-RUN] Would configure PAM account lockout")
        return

    if not (is_vm() or is_hardware()):
        print("  ✓ Skipping PAM lockout (not applicable to containers)")
        return

    faillock_conf = """# Managed by basaltwater - account lockout settings.
deny = 5
fail_interval = 900
unlock_time = 600
"""

    pam_profile = """Name: basaltwater account lockout (pam_faillock)
Default: yes
Priority: 0
Auth-Type: Primary
Auth:
\t[default=die] pam_faillock.so authfail
Auth-Initial:
\trequired pam_faillock.so preauth
Account-Type: Primary
Account:
\trequired pam_faillock.so
"""

    profile_matches = _managed_text_matches(_PAM_FAILLOCK_PROFILE, pam_profile)
    settings_match = _managed_text_matches(_FAILLOCK_CONF, faillock_conf)
    if profile_matches and settings_match and _pam_lockout_active():
        print("  ✓ PAM lockout already configured")
        return

    os.makedirs(os.path.dirname(_PAM_FAILLOCK_PROFILE), exist_ok=True)
    if not profile_matches:
        write_text_atomic(_PAM_FAILLOCK_PROFILE, pam_profile, mode=0o644)
    if not settings_match:
        write_text_atomic(_FAILLOCK_CONF, faillock_conf, mode=0o644)

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    result = run("pam-auth-update --enable faillock-basaltwater", check=False)
    if result.returncode != 0:
        raise RuntimeError("PAM lockout profile could not be enabled")
    if not _pam_lockout_active():
        raise RuntimeError("PAM lockout is absent from the active auth or account stack")

    print("  ✓ PAM account lockout configured (5 failures in 15 min → 10 min lockout)")


def configure_security_monitor(config: SetupConfig) -> None:
    """Set up a systemd timer that checks security logs every 15 minutes.

    Monitors fail2ban ban events, auditd key events (identity, sudoers, SSH
    config, kernel modules, privileged execs), and SSH auth failures, then
    sends notifications via the configured basaltwater targets.
    """
    if not (is_vm() or is_hardware()):
        print("  ✓ Skipping security monitor (not applicable to containers)")
        return

    configured = configure_maintenance_timer(
        service_name="security-monitor",
        service_desc="Security event monitor",
        timer_desc="Security event monitor (every 15 minutes)",
        script_path=_SECURITY_MONITOR_SCRIPT,
        schedule="*:0/15",
        check_name="Security event monitor",
        randomized_delay="2min",
        timeout="10min",
        purpose="monitor",
        environment={
            "BASALTWATER_AUDIT_REQUIRED": (
                "0" if config.system_type == "server_proxmox" else "1"
            ),
        },
    )
    if not configured:
        raise RuntimeError("Security event monitor timer failed verification")


def _cleanup_legacy_unattended_upgrades() -> None:
    """Remove legacy unattended-upgrades config files created by older versions."""
    for path in (_LEGACY_UNATTENDED_ORIGINS_FILE, _LEGACY_MANAGED_ORIGINS_FILE):
        if os.path.exists(path):
            os.remove(path)


def configure_auto_updates(config: SetupConfig) -> None:
    """Configure automatic package updates using a custom systemd service.

    This replaces the legacy unattended-upgrades approach. The new service
    runs ``apt-get update && apt-get dist-upgrade --no-remove`` which:
    - Does not require any hardcoded origins or codenames
    - Automatically handles all configured repositories
    - Supports dependency additions while refusing automated package removals
    """
    if is_dry_run():
        print("  [DRY-RUN] Would configure automatic package updates")
        return

    # Remove legacy unattended-upgrades config files from older setups
    _cleanup_legacy_unattended_upgrades()

    configured = configure_maintenance_timer(
        service_name="auto-update-apt",
        service_desc="Auto-update APT packages",
        timer_desc="Auto-update APT packages daily",
        script_path="/opt/basaltwater/common/service_tools/auto_update_apt.py",
        schedule="*-*-* 06:00:00",
        check_name="APT packages",
        purpose="auto-update",
    )
    if not configured:
        print("  ⚠ Replacement APT update timer was not verified; retaining distro APT timers")
        raise RuntimeError("APT update timer failed verification")

    # The distro timers can invoke unattended-upgrades even when its service is
    # disabled. Retire those competing activators only after the replacement is
    # active so a failed setup cannot leave the host without automatic updates.
    for unit in (
        "unattended-upgrades.service",
        "apt-daily.timer",
        "apt-daily-upgrade.timer",
    ):
        run(f"systemctl stop {unit}", check=False)
        run(f"systemctl disable {unit}", check=False)


def configure_firewall_web(config: SetupConfig) -> None:
    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    firewall_active = result.returncode == 0
    if not firewall_active:
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq ufw")
        for command in ("ufw default deny incoming", "ufw default allow outgoing"):
            policy_result = _run_ufw(command, check=True)
            if policy_result.returncode != 0:
                raise _ufw_failure("Failed to configure firewall defaults", policy_result)
    _configure_ssh_firewall(config)
    web_ports = _configure_managed_web_ports(config)
    _configure_mdns_firewall(config)

    if firewall_active:
        _verify_source_restricted_firewall(
            config, web_ports=web_ports, include_rdp=False
        )
        print(
            "  ✓ Firewall already active; web ports reconciled: "
            + ", ".join(str(port) for port in web_ports)
        )
        return

    result = _run_ufw("ufw --force enable")
    if result.returncode != 0:
        if is_container():
            print("  ⚠ Firewall could not be enabled (container may lack capabilities)")
        else:
            raise RuntimeError("Firewall could not be enabled (check command output)")
        return
    _verify_source_restricted_firewall(
        config, web_ports=web_ports, include_rdp=False
    )
    
    print(
        "  ✓ Firewall configured (SSH; web TCP ports: "
        + ", ".join(str(port) for port in web_ports)
        + ")"
    )


def configure_firewall_ssh_only(config: SetupConfig) -> None:
    """Configure firewall to allow only SSH (for servers without web/RDP)."""
    result = run("ufw status 2>/dev/null | grep -q 'Status: active'", check=False)
    firewall_active = result.returncode == 0

    if not firewall_active:
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run("apt-get install -y -qq ufw")
        for command in ("ufw default deny incoming", "ufw default allow outgoing"):
            policy_result = _run_ufw(command, check=True)
            if policy_result.returncode != 0:
                raise _ufw_failure("Failed to configure firewall defaults", policy_result)
    _configure_ssh_firewall(config)
    _configure_mdns_firewall(config)

    if firewall_active:
        _verify_source_restricted_firewall(config, web_ports=[], include_rdp=False)
        print("  ✓ Firewall already configured")
        return
    
    result = _run_ufw("ufw --force enable")
    if result.returncode != 0:
        if is_container():
            print("  ⚠ Firewall could not be enabled (container may lack capabilities)")
        else:
            raise RuntimeError("Firewall could not be enabled (check command output)")
        return

    _verify_source_restricted_firewall(config, web_ports=[], include_rdp=False)
    ssh_policy = (
        "SSH source-restricted"
        if config.effective_access_sources()
        else "SSH rate-limited"
    )
    print(f"  ✓ Firewall configured ({ssh_policy})")


def _proxmox_management_entries() -> list[dict[str, object]] | None:
    result = run(
        "pvesh get /cluster/firewall/ipset/management --output-format json",
        check=False,
        capture_output=True,
    )
    stdout = getattr(result, "stdout", None)
    if result.returncode != 0 or not isinstance(stdout, str):
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse the Proxmox management IP set") from exc
    if not isinstance(payload, list) or not all(
        isinstance(entry, dict) for entry in payload
    ):
        raise RuntimeError("Proxmox returned an invalid management IP set")
    return payload


def _proxmox_firewall_options(endpoint: str) -> dict[str, object]:
    result = run(f"pvesh get {endpoint}/options --output-format json", check=False, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("Could not inspect Proxmox firewall options")
    try:
        options = json.loads(result.stdout or "")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not parse Proxmox firewall options") from exc
    if not isinstance(options, dict):
        raise RuntimeError("Proxmox returned invalid firewall options")
    return options


def _check_proxmox_firewall(*, require_enabled: bool) -> dict[str, object]:
    """Check native node policy and its chosen backend without changing either."""
    cluster = _proxmox_firewall_options("/cluster/firewall")
    node = _proxmox_firewall_options("/nodes/$(hostname -s)/firewall")
    if node.get("enable", 1) not in (1, True):
        raise RuntimeError("The node firewall is disabled; enable it before applying management sources")
    if cluster.get("policy_in", "DROP") not in ("DROP", "REJECT"):
        raise RuntimeError("Proxmox input policy must be DROP or REJECT to filter management access")
    if require_enabled and cluster.get("enable", 0) not in (1, True):
        raise RuntimeError("Proxmox cluster firewall enablement did not persist")
    nftables = node.get("nftables", 0) in (1, True)
    service = "proxmox-firewall" if nftables else "pve-firewall"
    active = run(f"systemctl is-active --quiet {service}", check=False, capture_output=True)
    if active.returncode != 0:
        raise RuntimeError(f"Selected Proxmox firewall backend {service} is not active")
    compiler = "/usr/libexec/proxmox/proxmox-firewall" if nftables else "pve-firewall"
    compiled = run(f"{compiler} compile", check=False, capture_output=True)
    if compiled.returncode != 0:
        raise RuntimeError("Proxmox firewall configuration could not be compiled")
    # The legacy compiler warns about invalid rules while still exiting zero.
    if not nftables and (getattr(compiled, "stderr", "") or "").strip():
        raise RuntimeError("Proxmox firewall compilation warnings require operator review")
    return cluster


def _proxmox_source_key(source: str) -> str:
    validated = validate_network_ip_or_cidr(source, "Proxmox management source")
    return str(ipaddress.ip_network(validated, strict=False))


def _verify_proxmox_sources(
    desired_sources: list[str], *, allow_stale: bool = False,
) -> list[dict[str, object]]:
    entries = _proxmox_management_entries()
    if entries is None:
        raise RuntimeError("Could not verify the Proxmox management IP set")
    desired = {_proxmox_source_key(source) for source in desired_sources}
    actual = set()
    stale = []
    for entry in entries:
        cidr = entry.get("cidr")
        if not isinstance(cidr, str):
            raise RuntimeError("Proxmox management entry has no CIDR")
        key = _proxmox_source_key(cidr)
        if not entry.get("nomatch", False):
            actual.add(key)
        if str(entry.get("comment", "")).startswith(_PROXMOX_MANAGEMENT_COMMENT_PREFIX) and key not in desired:
            stale.append(cidr)
    if not desired.issubset(actual) or (stale and not allow_stale):
        raise RuntimeError("Proxmox management source reconciliation did not persist")
    return entries


def configure_proxmox_management_firewall(config: SetupConfig) -> None:
    """Reconcile native Proxmox management sources and enable its firewall."""

    desired_sources = [
        validate_network_ip_or_cidr(source, "Proxmox management source")
        for source in config.effective_access_sources()
    ]
    if is_dry_run():
        print("  [DRY-RUN] Would verify native firewall policy and reconcile managed Proxmox sources")
        return
    cluster_options = _check_proxmox_firewall(require_enabled=False) if desired_sources else None
    existing_entries = _proxmox_management_entries()
    if existing_entries is None:
        if not desired_sources:
            print("  ✓ Proxmox management access filter not requested")
            return
        result = run(
            "pvesh create /cluster/firewall/ipset --name management "
            "--comment 'Proxmox standard management access set'",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not create the Proxmox management IP set")
        existing_entries = []

    existing_by_cidr = {
        str(entry.get("cidr")): entry
        for entry in existing_entries
        if isinstance(entry.get("cidr"), str)
    }
    existing_by_key = {_proxmox_source_key(cidr): entry for cidr, entry in existing_by_cidr.items()}
    desired_set = {_proxmox_source_key(source) for source in desired_sources}
    for source in desired_sources:
        requested = ipaddress.ip_network(_proxmox_source_key(source))
        for cidr, entry in existing_by_cidr.items():
            if entry.get("nomatch", False):
                excluded = ipaddress.ip_network(_proxmox_source_key(cidr))
                if requested.version == excluded.version and requested.overlaps(excluded):
                    raise RuntimeError("An excluded management IP-set entry overlaps a requested source")

    connection = os.environ.get("SSH_CONNECTION", "").split()
    if desired_sources and connection:
        if len(connection) != 4 or not validate_ip_address(connection[0]):
            raise RuntimeError("Cannot verify the current SSH peer before enabling the firewall")
        peer = ipaddress.ip_address(connection[0])
        retained = [cidr for cidr, entry in existing_by_cidr.items()
                    if not str(entry.get("comment", "")).startswith(_PROXMOX_MANAGEMENT_COMMENT_PREFIX)
                    and not entry.get("nomatch", False)]
        allowed = [ipaddress.ip_network(_proxmox_source_key(cidr)) for cidr in desired_sources + retained]
        excluded = [ipaddress.ip_network(_proxmox_source_key(cidr)) for cidr, entry in existing_by_cidr.items()
                    if entry.get("nomatch", False)]
        if not any(peer in network for network in allowed) or any(peer in network for network in excluded):
            raise RuntimeError("Current SSH peer is outside the requested or retained management sources")

    for source in desired_sources:
        if _proxmox_source_key(source) in existing_by_key:
            continue
        comment = f"{_PROXMOX_MANAGEMENT_COMMENT_PREFIX} {source}"
        result = run(
            "pvesh create /cluster/firewall/ipset/management "
            f"--cidr {shlex.quote(source)} --comment {shlex.quote(comment)}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Could not add Proxmox management source {source}"
            )

    # Keep old access sources until new entries and native enablement are verified.
    if desired_sources:
        _verify_proxmox_sources(desired_sources, allow_stale=True)
        try:
            result = run("pvesh set /cluster/firewall/options --enable 1", check=False)
            if result.returncode != 0:
                raise RuntimeError("Could not enable the Proxmox cluster firewall")
            _check_proxmox_firewall(require_enabled=True)
        except Exception:
            if cluster_options is not None and cluster_options.get("enable", 0) not in (1, True):
                restored = run("pvesh set /cluster/firewall/options --enable 0", check=False)
                if restored.returncode != 0:
                    raise RuntimeError("Firewall activation failed and previous enablement could not be restored")
            raise

    for cidr, entry in existing_by_cidr.items():
        comment = entry.get("comment")
        if (
            not isinstance(comment, str)
            or not comment.startswith(_PROXMOX_MANAGEMENT_COMMENT_PREFIX)
            or _proxmox_source_key(cidr) in desired_set
        ):
            continue
        encoded_cidr = quote(cidr, safe="")
        result = run(
            f"pvesh delete /cluster/firewall/ipset/management/{encoded_cidr}",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Could not remove stale Proxmox management source {cidr}"
            )

    verified_entries = _verify_proxmox_sources(desired_sources)
    if not desired_sources:
        print("  ✓ Proxmox managed access sources cleared; firewall state preserved")
        return

    print(
        "  ✓ Proxmox managed access sources verified: "
        + ", ".join(desired_sources)
    )
    retained_sources = [str(entry["cidr"]) for entry in verified_entries
                        if _proxmox_source_key(str(entry["cidr"])) not in desired_set]
    if retained_sources:
        print("  Retained operator management entries: " + ", ".join(retained_sources))
    print("  Proxmox's implicit cluster access and existing firewall rules remain in effect")


def configure_auto_restart(config: SetupConfig) -> None:
    """Configure automatic restart at 2 AM when updates require it."""
    if not can_modify_kernel():
        print("  ✓ Skipping automatic restart service (container)")
        return

    if not is_dry_run():
        install_kernel_restart_hook()

    configured = configure_maintenance_timer(
        service_name="auto-restart-if-needed",
        service_desc="Auto-restart system if needed",
        timer_desc="Auto-restart system if needed (daily at 2 AM)",
        script_path="/opt/basaltwater/common/service_tools/auto_restart_if_needed.py",
        schedule="*-*-* 02:00:00",
        on_boot_sec="30min",
        check_name="Automatic restart",
        randomized_delay="10min",
        timeout="10min",
        network_online=False,
        purpose="check",
    )
    if not configured:
        raise RuntimeError("Automatic restart timer failed verification")


def configure_cleanup_maintenance(config: SetupConfig) -> None:
    """Configure separate system and user-scoped recurring cleanup jobs."""
    if is_dry_run():
        print("  [DRY-RUN] Would configure cleanup maintenance")
        return

    os.makedirs(_JOURNAL_CONF_DIR, exist_ok=True)
    with open(_JOURNAL_CONF_FILE, "w") as f:
        f.write(
            f"""[Journal]
SystemMaxUse={JOURNAL_MAX_USE}
RuntimeMaxUse={JOURNAL_MAX_USE}
"""
        )

    journal_result = run("systemctl restart systemd-journald", check=False)
    if journal_result.returncode != 0:
        print("  ⚠ Journal limits written but journald could not be restarted")

    configured = configure_maintenance_timer(
        service_name="cleanup-maintenance",
        service_desc="Cleanup temporary files and package caches",
        timer_desc="Cleanup temporary files and package caches (weekly)",
        script_path="/opt/basaltwater/common/service_tools/cleanup_maintenance.py",
        schedule="Sun *-*-* 03:30:00",
        check_name="Cleanup maintenance",
        randomized_delay="30min",
        timeout="1h",
        network_online=False,
        purpose="job",
    )
    if not configured:
        raise RuntimeError("Cleanup maintenance timer failed verification")

    if config.username == "root":
        print("  ℹ User cache maintenance skipped for the root account")
        return

    user_cache_configured = configure_maintenance_timer(
        service_name="user-cache-maintenance",
        service_desc="Prune configured user developer-tool caches",
        timer_desc="Prune configured user developer-tool caches (daily)",
        script_path="/opt/basaltwater/common/service_tools/user_cache_maintenance.py",
        schedule="*-*-* 07:00:00",
        check_name="User cache maintenance",
        user=config.username,
        randomized_delay="30min",
        timeout="1h",
        network_online=False,
        purpose="job",
    )
    if not user_cache_configured:
        raise RuntimeError("User cache maintenance timer failed verification")
