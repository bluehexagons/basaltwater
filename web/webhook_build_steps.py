"""Install signed GitHub intake as an adapter to the managed builder."""

from __future__ import annotations

import os
import pwd
import secrets

from common.build_host_steps import _ensure_directory, _read_file, _write_file
from lib.atomic_io import write_json_atomic
from lib.build_webhook_service import ACCOUNT, CONFIG, DATA, ENTRY, INBOX, PORT, PREPARING, RECEIPTS, TOKEN, load_settings
from lib.remote_utils import is_dry_run, run
from lib.setup_directories import directory_fd
from lib.unit_transaction import remove_units, replace_units
from lib.webhook_builds import WEBHOOK_PATH, validate_webhook_settings

UNITS = ("basaltwater-build-webhooks.service", "basaltwater-build-webhook-admission.service")
MARKER = "# Managed by Basaltwater build webhooks"


def reject_legacy_build_services() -> None:
    """Never install another worker beside the retired executor or rsync grant."""
    for path in ("/etc/basaltwater/cicd", "/var/lib/basaltwater/cicd",
                 "/etc/sudoers.d/deploy-nginx", "/etc/sudoers.d/basaltwater-deploy",
                 "/usr/local/sbin/basaltwater-deploy-admin",
                 "/etc/systemd/system/webhook-receiver.service",
                 "/etc/systemd/system/cicd-executor.service", "/etc/systemd/system/cicd-executor.path"):
        if os.path.lexists(path):
            raise ValueError("Retired build services require migration before setup; preserve their state and follow docs/services/build-systems.md")
    if os.path.isdir("/run/systemd/system"):
        for unit in ("webhook-receiver.service", "cicd-executor.service", "cicd-executor.path"):
            result = run(["systemctl", "show", unit, "--property=LoadState,ActiveState"],
                         capture_output=True, text=True, check=False)
            state = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
            if (state.get("LoadState") != "not-found"
                    or state.get("ActiveState") not in {"inactive", "failed"}):
                raise ValueError("Retired build units remain loaded; stop them and reload systemd before migration")


def render_unit(*, receive: bool) -> str:
    from lib.build_coordinator import LOCK
    identity = f"User={ACCOUNT}\nGroup={ACCOUNT}\n" if receive else "User=root\nGroup=root\n"
    writable = INBOX if receive else DATA + " /var/lib/basaltwater/build-host " + LOCK
    return f"""{MARKER}
[Unit]
Description=Basaltwater signed build {'receiver' if receive else 'source admission'}
After=network-online.target basaltwater-build-capacity.service basaltwater-build-host.service
Wants=network-online.target basaltwater-build-capacity.service

[Service]
Type=simple
{identity}ExecStart=/usr/bin/python3 -I {ENTRY} {'--receive' if receive else '--admit'}
WorkingDirectory=/
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LimitCORE=0
MemoryMax=512M
TasksMax=64
ReadWritePaths={writable}

[Install]
WantedBy=multi-user.target
"""


def _units_owned() -> None:
    for name in UNITS:
        raw = _read_file("/etc/systemd/system/" + name, mode=0o644, maximum=32768)
        if raw is not None and not raw.startswith(MARKER.encode()):
            raise ValueError("Refusing an unmanaged build webhook unit")


def configure_webhook_builds(config) -> None:
    validate_webhook_settings(config)
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would reconcile signed webhook intake for the managed builder")
        return
    reject_legacy_build_services()
    _units_owned()
    previous = load_settings() if os.path.lexists(CONFIG) else None
    if not config.enable_webhook_builds:
        remove_units(UNITS)
        if previous is not None:
            from lib.nginx_config import create_nginx_sites_for_groups

            create_nginx_sites_for_groups({}, managed_domains={previous["domain"]})
        return
    from lib.build_coordinator import ensure_coordinator, maintenance

    ensure_coordinator()
    with maintenance():
        if config.build_host_enrollment is None or len(config.build_host_enrollment["keys"]) != 1:
            raise ValueError("Webhook setup currently requires exactly one enrolled controller principal")
        principal, = config.build_host_enrollment["keys"]
        try:
            account = pwd.getpwnam(ACCOUNT)
        except KeyError:
            run(["useradd", "--system", "--user-group", "--home-dir", "/nonexistent",
                 "--no-create-home", "--shell", "/usr/sbin/nologin", ACCOUNT])
            account = pwd.getpwnam(ACCOUNT)
        if (account.pw_uid <= 0 or account.pw_gid <= 0 or account.pw_dir != "/nonexistent"
                or account.pw_shell != "/usr/sbin/nologin"
                or os.getgrouplist(ACCOUNT, account.pw_gid) != [account.pw_gid]):
            raise ValueError("Webhook intake requires a dedicated unprivileged account")
        _ensure_directory(DATA, mode=0o711)
        _ensure_directory(RECEIPTS, mode=0o700)
        _ensure_directory(PREPARING, mode=0o700)
        # This is the only receiver-writable directory. The admission process
        # treats every entry as untrusted and authenticates the raw bytes again.
        with directory_fd(INBOX, create=True, mode=0o700) as descriptor:
            info = os.fstat(descriptor)
            if info.st_uid not in {0, account.pw_uid} or info.st_mode & 0o077:
                raise ValueError("Unsafe webhook intake directory")
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
            os.fchmod(descriptor, 0o700)
        _ensure_directory(os.path.dirname(CONFIG), mode=0o755)
        secret = config.webhook_secret or (previous["secret"] if previous else secrets.token_hex(32))
        settings = dict(schema=1, secret=secret, repositories=config.webhook_repositories,
                        branches=config.webhook_branches or ["main"], principal=principal, domain=config.webhook_domain)
        # root-controlled but readable by the intake account; no build identity
        # or Actions job is a member of this group.
        write_json_atomic(CONFIG, settings, mode=0o640, uid=0, gid=account.pw_gid)
        if config.webhook_github_token is not None:
            _write_file(TOKEN, config.webhook_github_token.encode("utf-8"), mode=0o600)
        from web.web_steps import install_nginx
        from web.ssl_steps import install_certbot, setup_ssl_for_deployments
        from lib.nginx_config import create_nginx_sites_for_groups

        install_nginx(config)
        domain = config.webhook_domain
        domains = {domain} | ({previous["domain"]} if previous else set())
        deployments = [dict(domain=domain, path="/", needs_proxy=True, backend_port=PORT, preserve_path=True)]
        create_nginx_sites_for_groups({domain: deployments}, managed_domains=domains,
                                      enable_https_redirect=not config.enable_cloudflare)
        if not config.enable_cloudflare:
            install_certbot(config)
            setup_ssl_for_deployments(deployments, config.ssl_email, managed_domains=domains)
        replace_units({name: render_unit(receive=index == 0) for index, name in enumerate(UNITS)}, activate=UNITS)
        print(f"  GitHub push webhook: https://{domain}{WEBHOOK_PATH}")
        print(f"  Signing secret retained on builder in {CONFIG}; use the supplied value or retrieve it over root SSH")
        print(f"  Build receipts: {RECEIPTS}; execution, logs and artifacts use the managed build host")
