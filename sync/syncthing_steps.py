"""Managed Syncthing service with GUI-owned sharing configuration."""

from __future__ import annotations

import copy
import grp
import json
import os
import pwd
import re
import shlex
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from common.storage_steps import assert_declared_storage_mount
from lib.atomic_io import write_text_atomic
from lib.config import DEFAULT_SYNCTHING_ROOT, SetupConfig
from lib.credentials import get_runtime_credential
from lib.local_http import open_loopback
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, is_package_installed, run
from lib.validation import validate_filesystem_path


SYNCTHING_HOME = "/var/lib/basaltwater/syncthing"
SYNCTHING_SERVICE_NAME = "basaltwater-syncthing"
SYNCTHING_SERVICE_FILE = f"/etc/systemd/system/{SYNCTHING_SERVICE_NAME}.service"
SYNCTHING_GUI_ADDRESS = "127.0.0.1:8384"
SYNCTHING_API_URL = f"http://{SYNCTHING_GUI_ADDRESS}/rest/config"
_DEVICE_ID_PATTERN = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$")


def _account(username: str) -> tuple[int, int, str]:
    """Return the target user's UID, GID, and primary group name."""

    try:
        account = pwd.getpwnam(username)
        group_name = grp.getgrgid(account.pw_gid).gr_name
    except KeyError as exc:
        raise RuntimeError(f"Syncthing setup user does not exist: {username}") from exc
    return account.pw_uid, account.pw_gid, group_name


def _systemd_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def _share_root(config: SetupConfig) -> str:
    return config.syncthing_root or DEFAULT_SYNCTHING_ROOT


def _render_service(config: SetupConfig, group_name: str) -> str:
    share_root = _share_root(config)
    writable_lines = "\n".join(
        f"ReadWritePaths={_systemd_quote(path)}"
        for path in (SYNCTHING_HOME, share_root)
    )
    return f"""[Unit]
Description=Managed Syncthing file synchronization
Documentation=https://docs.syncthing.net/
Wants=network-online.target
After=network-online.target
RequiresMountsFor={_systemd_quote(share_root)}

[Service]
Type=simple
User={config.username}
Group={group_name}
UMask=0007
ExecStart=/usr/bin/syncthing serve --home={SYNCTHING_HOME} --gui-address=http://{SYNCTHING_GUI_ADDRESS} --no-browser --no-restart --no-upgrade
Restart=on-failure
RestartSec=5s
SuccessExitStatus=3 4
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
{writable_lines}

[Install]
WantedBy=multi-user.target
"""


def _run_as_user(
    username: str,
    syncthing_args: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
    input_data: str | None = None,
) -> Any:
    return run(
        ["runuser", "-u", username, "--", "/usr/bin/syncthing", *syncthing_args],
        capture_output=capture_output,
        check=check,
        input_data=input_data,
    )


def _cli(
    username: str,
    arguments: list[str],
    *,
    capture_output: bool = False,
    check: bool = True,
) -> Any:
    return _run_as_user(
        username,
        ["cli", f"--home={SYNCTHING_HOME}", *arguments],
        capture_output=capture_output,
        check=check,
    )


def _wait_for_api(username: str, attempts: int = 30) -> None:
    for _attempt in range(attempts):
        result = _cli(username, ["show", "system"], capture_output=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Syncthing API did not become ready on 127.0.0.1:8384")


def _load_current_config(username: str) -> dict[str, Any]:
    result = _cli(username, ["config", "dump-json"], capture_output=True)
    try:
        loaded = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Syncthing returned an invalid configuration") from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("Syncthing returned a non-object configuration")
    return loaded


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Syncthing configuration is missing {name}")
    return copy.deepcopy(value)


def _staggered_versioning(default: object) -> dict[str, Any]:
    versioning = _mapping(default, "folder versioning defaults")
    versioning.update(
        {
            "type": "staggered",
            "params": {"cleanInterval": "3600", "maxAge": "31536000"},
            "cleanupIntervalS": 3600,
            "fsPath": "",
            "fsType": "basic",
        }
    )
    return versioning


def _validate_folder_paths(current: dict[str, Any], share_root: str) -> None:
    """Reject GUI-managed folders that escape the writable storage root."""

    folders = current.get("folders")
    if not isinstance(folders, list):
        raise RuntimeError("Syncthing configuration is missing folders")
    resolved_root = os.path.realpath(share_root)
    for folder in folders:
        path = folder.get("path") if isinstance(folder, dict) else None
        try:
            contained = (
                isinstance(path, str)
                and os.path.isabs(path)
                and os.path.commonpath((resolved_root, os.path.realpath(path)))
                == resolved_root
            )
        except ValueError:
            contained = False
        if not contained:
            raise RuntimeError(
                f"Syncthing folder path is outside the configured storage root: {path}"
            )


def build_syncthing_policy_config(
    current: dict[str, Any],
    share_root: str,
) -> dict[str, Any]:
    """Apply service policy without replacing GUI-managed devices or folders."""

    desired = copy.deepcopy(current)
    _validate_folder_paths(desired, share_root)
    defaults = _mapping(desired.get("defaults"), "defaults")
    default_folder = _mapping(defaults.get("folder"), "folder defaults")
    default_folder["path"] = share_root
    default_folder["versioning"] = _staggered_versioning(
        default_folder.get("versioning")
    )
    defaults["folder"] = default_folder
    desired["defaults"] = defaults

    gui = _mapping(desired.get("gui"), "GUI settings")
    if not isinstance(gui.get("user"), str) or not gui["user"]:
        raise RuntimeError("Syncthing GUI administrator was not configured")
    if not isinstance(gui.get("password"), str) or not gui["password"]:
        raise RuntimeError("Syncthing GUI password was not configured")
    gui.update(
        {
            "enabled": True,
            "address": SYNCTHING_GUI_ADDRESS,
            "useTLS": False,
            "debugging": False,
            "insecureAdminAccess": False,
            "insecureSkipHostcheck": False,
            "insecureAllowFrameLoading": False,
        }
    )
    desired["gui"] = gui

    options = _mapping(desired.get("options"), "options")
    options.update(
        {
            "defaultFolderPath": share_root,
            "startBrowser": False,
            "globalAnnounceEnabled": True,
            "localAnnounceEnabled": True,
            "relaysEnabled": True,
            "natEnabled": False,
            "urAccepted": -1,
            "crashReportingEnabled": False,
        }
    )
    desired["options"] = options
    return desired


def _put_config(desired: dict[str, Any]) -> None:
    gui = desired.get("gui")
    api_key = gui.get("apiKey") if isinstance(gui, dict) else None
    if not isinstance(api_key, str) or not api_key:
        raise RuntimeError("Syncthing configuration does not contain an API key")
    request = Request(
        SYNCTHING_API_URL,
        data=json.dumps(desired, separators=(",", ":")).encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    try:
        with open_loopback(request, timeout=20) as response:
            if response.status not in {200, 204}:
                raise RuntimeError(
                    f"Syncthing rejected its managed config (HTTP {response.status})"
                )
    except HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"Syncthing rejected its managed config (HTTP {exc.code}){suffix}"
        ) from exc
    except URLError as exc:
        raise RuntimeError("Could not reach the loopback Syncthing API") from exc


def _configure_syncthing_https(config: SetupConfig) -> str | None:
    if os.geteuid() != 0 or not os.path.isfile(
        "/opt/basaltwater/common/service_tools/basaltwater_web.py"
    ):
        return None
    from common.godot_web_steps import configure_internal_web_host, identities_for_config

    configure_internal_web_host(
        identities_for_config(config.host, config.system_hostname),
        [config.username],
        config.effective_access_sources(),
        configure_static_site=True,
        install_utility=True,
    )
    result = run(
        "SUDO_USER=" + shlex.quote(config.username)
        + " /usr/local/bin/basaltwater-web forward add syncthing"
        + " --listen auto --to 127.0.0.1:8384 --profile syncthing --wait 30 --json",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "HTTPS forward failed").strip()
        raise RuntimeError(f"Could not configure HTTPS Syncthing endpoint: {detail}")
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError
        url = payload.get("url")
        listen = payload.get("listen")
    except (TypeError, ValueError):
        url, listen = None, None
    if (
        not isinstance(url, str)
        or not url.startswith("https://")
        or not isinstance(listen, int)
        or not 1024 <= listen <= 65535
    ):
        raise RuntimeError("HTTPS gateway returned an invalid Syncthing endpoint")
    return url


def _remove_syncthing_https(config: SetupConfig) -> None:
    if not os.path.isfile("/usr/local/bin/basaltwater-web"):
        return
    result = run(
        "SUDO_USER=" + shlex.quote(config.username)
        + " /usr/local/bin/basaltwater-web forward remove syncthing --json",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (
            result.stderr or result.stdout or "HTTPS forward removal failed"
        ).strip()
        if "HTTPS forward does not exist: syncthing" not in detail:
            raise RuntimeError(
                f"Could not remove HTTPS Syncthing endpoint: {detail}"
            )


def _prepare_share_root(config: SetupConfig, group_name: str) -> None:
    share_root = _share_root(config)
    if os.path.lexists(share_root) and (
        os.path.islink(share_root)
        or not os.path.isdir(share_root)
    ):
        raise RuntimeError(f"Refusing unsafe Syncthing share root: {share_root}")
    if os.path.realpath(share_root) != share_root:
        raise RuntimeError(
            f"Refusing Syncthing share root with symbolic-link traversal: {share_root}"
        )
    assert_declared_storage_mount(config, share_root)
    run(
        [
            "install",
            "-d",
            "-m",
            "0770",
            "-o",
            config.username,
            "-g",
            group_name,
            share_root,
        ]
    )


def _preflight_existing_folders(username: str, share_root: str) -> None:
    """Validate a running endpoint before changing its writable root."""

    active = run(
        ["systemctl", "is-active", "--quiet", f"{SYNCTHING_SERVICE_NAME}.service"],
        check=False,
    )
    if active.returncode != 0:
        return
    _wait_for_api(username)
    _validate_folder_paths(_load_current_config(username), share_root)


def _validate_state_paths() -> None:
    """Refuse redirected persistent state before privileged ownership changes."""
    for path in (SYNCTHING_HOME, SYNCTHING_SERVICE_FILE):
        validate_filesystem_path(path)
        if os.path.realpath(path) != path:
            raise RuntimeError(f"Refusing symlinked Syncthing state or service path: {path}")
    if os.path.lexists(SYNCTHING_HOME) and not os.path.isdir(SYNCTHING_HOME):
        raise RuntimeError(f"Syncthing state must be a directory: {SYNCTHING_HOME}")
    if os.path.lexists(SYNCTHING_SERVICE_FILE) and not os.path.isfile(SYNCTHING_SERVICE_FILE):
        raise RuntimeError(f"Syncthing service must be a regular file: {SYNCTHING_SERVICE_FILE}")
    # Syncthing reads these when generating/updating an existing identity.
    for name in ("config.xml", "cert.pem", "key.pem"):
        path = os.path.join(SYNCTHING_HOME, name)
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path) or os.stat(path).st_nlink != 1):
            raise RuntimeError(f"Refusing unsafe Syncthing identity file: {path}")


def _stop_before_configuration() -> None:
    """Permit a missing first-install unit, but never configure a live daemon."""
    unit = f"{SYNCTHING_SERVICE_NAME}.service"
    stopped = run(["systemctl", "stop", unit], check=False)
    state = run(["systemctl", "is-active", unit], check=False, capture_output=True)
    if state.returncode == 0 or str(state.stdout or "").strip() not in {"inactive", "failed", "unknown"}:
        raise RuntimeError("Managed Syncthing service did not stop; configuration was not changed")
    if stopped.returncode != 0:
        loaded = run(["systemctl", "show", unit, "--property=LoadState", "--value"], check=False, capture_output=True)
        if loaded.returncode != 0 or str(loaded.stdout or "").strip() != "not-found":
            raise RuntimeError("Could not stop managed Syncthing service")


def _prepare_state_home(username: str, uid: int, gid: int) -> None:
    """Restore account access after staging locks down the shared state parent."""
    parent = os.path.dirname(SYNCTHING_HOME)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if not is_package_installed("acl"):
        run(["apt-get", "-o", "DPkg::Lock::Timeout=60", "install", "-y", "-qq", "acl"])
    # Keep the shared directory private: this account can traverse, not list
    # or write it. Reapply after every runtime staging chmod (which masks ACLs).
    run(["setfacl", "-m", f"u:{uid}:--x", "--", parent])
    os.makedirs(SYNCTHING_HOME, mode=0o700, exist_ok=True)
    os.chmod(SYNCTHING_HOME, 0o700)
    os.chown(SYNCTHING_HOME, uid, gid)
    for permission in ("-x", "-w"):
        run(["runuser", "-u", username, "--", "/usr/bin/test", permission, SYNCTHING_HOME])


def setup_syncthing(config: SetupConfig, **_kwargs: Any) -> None:
    """Install and reconcile one unprivileged, relay-capable Syncthing endpoint."""
    if not config.enable_syncthing and not config.disable_syncthing:
        return
    if not can_manage_system_services(config.machine_type):
        print("  ✓ Skipping Syncthing (persistent services unavailable)")
        return
    if is_dry_run():
        print("  [DRY-RUN] Skipping Syncthing service configuration")
        return
    if config.disable_syncthing:
        _remove_syncthing_https(config)
        unit_exists = os.path.lexists(SYNCTHING_SERVICE_FILE)
        if unit_exists and (
            os.path.islink(SYNCTHING_SERVICE_FILE)
            or not os.path.isfile(SYNCTHING_SERVICE_FILE)
        ):
            raise RuntimeError(
                f"Refusing to remove unsafe Syncthing unit path: "
                f"{SYNCTHING_SERVICE_FILE}"
            )
        run(
            ["systemctl", "disable", "--now", f"{SYNCTHING_SERVICE_NAME}.service"],
            check=False,
        )
        active = run(
            ["systemctl", "is-active", "--quiet", f"{SYNCTHING_SERVICE_NAME}.service"],
            check=False,
        )
        if active.returncode == 0:
            raise RuntimeError("Managed Syncthing service did not stop")
        if unit_exists:
            os.unlink(SYNCTHING_SERVICE_FILE)
        run(["systemctl", "daemon-reload"])
        print("  ✓ Managed Syncthing service and HTTPS endpoint removed")
        print(f"  ℹ Preserved Syncthing identity and database in {SYNCTHING_HOME}")
        retained_root = config.syncthing_root or DEFAULT_SYNCTHING_ROOT
        print(f"  ℹ Preserved synchronized files in {retained_root}")
        return

    admin_username = config.syncthing_admin
    if not admin_username:
        raise RuntimeError("Syncthing administrator username was not configured")
    admin_password = get_runtime_credential(config, admin_username)
    if admin_password is None:
        raise RuntimeError(
            f"Syncthing administrator credential is missing: {admin_username}"
        )
    if "\n" in admin_password or "\r" in admin_password:
        raise RuntimeError("Syncthing administrator password cannot contain line breaks")

    _validate_state_paths()
    if not is_package_installed("syncthing"):
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"
        run(
            [
                "apt-get",
                "-o",
                "DPkg::Lock::Timeout=60",
                "install",
                "-y",
                "-qq",
                "syncthing",
            ]
        )
        if not is_package_installed("syncthing"):
            raise RuntimeError("Syncthing package installation did not verify")

    share_root = _share_root(config)
    uid, gid, group_name = _account(config.username)
    _prepare_state_home(config.username, uid, gid)
    _preflight_existing_folders(config.username, share_root)
    _prepare_share_root(config, group_name)

    _stop_before_configuration()
    _run_as_user(
        config.username,
        [
            "generate",
            f"--home={SYNCTHING_HOME}",
            "--skip-port-probing",
            "--no-default-folder",
            f"--gui-user={admin_username}",
            "--gui-password=-",
        ],
        input_data=admin_password + "\n",
    )

    write_text_atomic(
        SYNCTHING_SERVICE_FILE,
        _render_service(config, group_name),
        mode=0o644,
    )
    run(["systemd-analyze", "verify", SYNCTHING_SERVICE_FILE])
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", f"{SYNCTHING_SERVICE_NAME}.service"])
    try:
        _wait_for_api(config.username)
        device_id_result = _run_as_user(
            config.username,
            ["serve", "--device-id", f"--home={SYNCTHING_HOME}"],
            capture_output=True,
        )
        local_device_id = str(device_id_result.stdout or "").strip()
        if not _DEVICE_ID_PATTERN.fullmatch(local_device_id):
            raise RuntimeError("Syncthing returned an invalid local device ID")
        desired = build_syncthing_policy_config(
            _load_current_config(config.username), share_root
        )
        _put_config(desired)
        run(["systemctl", "restart", f"{SYNCTHING_SERVICE_NAME}.service"])
        _wait_for_api(config.username)
        active = run(
            ["systemctl", "is-active", f"{SYNCTHING_SERVICE_NAME}.service"],
            capture_output=True,
            check=False,
        )
        if active.returncode != 0:
            raise RuntimeError("Managed Syncthing service is not active")
    except Exception:
        run(
            [
                "systemctl",
                "disable",
                "--now",
                f"{SYNCTHING_SERVICE_NAME}.service",
            ],
            check=False,
        )
        raise

    https_url = _configure_syncthing_https(config)
    print(f"  ✓ Syncthing device ID: {local_device_id}")
    if https_url:
        print(f"  ✓ Syncthing HTTPS admin: {https_url}")
    print(f"  ✓ Syncthing GUI folders are confined to {share_root}")
    print("  ℹ Device and folder membership is managed in the Syncthing web GUI")
    print("  ℹ No router mapping was added; relay fallback remains enabled")
