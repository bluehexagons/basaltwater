"""Local, bounded operator commands for the managed console kiosk."""

from __future__ import annotations

import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import tomllib

from desktop import kiosk_runtime as runtime
from desktop import kiosk_steps as steps
from lib.machine_state import can_manage_system_services, resolve_machine_type
from lib.remote_utils import read_os_release
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


PROPERTIES = ("LoadState", "ActiveState", "SubState", "UnitFileState", "Result",
              "FragmentPath", "DropInPaths", "NeedDaemonReload")
EXECUTABLES = ("/usr/sbin/greetd", "/usr/bin/cage", "/usr/bin/firefox-esr",
               "/usr/bin/dbus-run-session", "/usr/bin/systemd-cat", "/usr/bin/python3",
               "/usr/bin/systemctl", "/usr/bin/loginctl")
ERRORS = (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError)


def add_kiosk_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("kiosk", help="Inspect or control this host's console kiosk")
    commands = parser.add_subparsers(dest="kiosk_command", required=True)
    for name in ("status", "doctor", "start", "stop", "restart"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="Print structured local observations")


def _command(argv: list[str], *, timeout: int = 10) -> str:
    # Never expose command output on failure: diagnostics can include config values.
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{Path(argv[0]).name} timed out; inspect local service state before retrying") from exc
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} {argv[1]} failed (exit {result.returncode}); "
                           "check permissions and local service logs")
    return result.stdout


def _fields(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _service() -> dict[str, str]:
    fields = _fields(_command(["/usr/bin/systemctl", "show", steps.SERVICE,
                              "--property=" + ",".join(PROPERTIES), "--no-pager"]))
    if any(name not in fields for name in PROPERTIES[:5]):
        raise RuntimeError("Incomplete systemd kiosk state; inspect systemctl show locally")
    return {name: fields.get(name, "") for name in PROPERTIES}


def _state(service: dict[str, str]) -> str:
    if service["ActiveState"] == "failed" or service["Result"] not in {"", "success"}:
        return "failed"
    if service["ActiveState"] == "active":
        return "running"
    if service["ActiveState"] not in {"", "inactive"}:
        return service["ActiveState"]
    if service["LoadState"] == "not-found":
        return "absent"
    if service["ActiveState"] == "inactive":
        return "disabled" if service["UnitFileState"] in {"disabled", "masked", "masked-runtime"} else "stopped"
    return service["ActiveState"] or "unknown"


def _account() -> pwd.struct_passwd:
    try:
        account = pwd.getpwnam(runtime.USER)
    except KeyError as exc:
        raise RuntimeError("Kiosk account is absent; rerun kiosk setup") from exc
    if (account.pw_uid == 0 or account.pw_gecos != runtime.ACCOUNT_COMMENT
            or account.pw_dir != str(runtime.HOME) or account.pw_shell != "/usr/sbin/nologin"
            or grp.getgrgid(account.pw_gid).gr_name != runtime.USER):
        raise RuntimeError("Kiosk account identity changed; administrator migration required")
    if _command(["/usr/bin/id", "-G", runtime.USER]).split() != [str(account.pw_gid)]:
        raise RuntimeError("Kiosk account must have only its dedicated primary group")
    return account


def _sessions() -> list[dict[str, str]]:
    rows = _command(["/usr/bin/loginctl", "list-sessions", "--no-legend", "--no-pager"]).splitlines()
    if len(rows) > 64:
        raise RuntimeError("Too many logind sessions to inspect safely (limit 64)")
    sessions = []
    for row in rows:
        if not row.strip():
            continue
        identity = row.split()[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
            raise RuntimeError("Unrecognized logind session identity")
        fields = _fields(_command(["/usr/bin/loginctl", "show-session", identity,
                                  "-p", "Name", "-p", "TTY", "-p", "Type", "-p", "State", "-p", "Seat"]))
        if any(name not in fields for name in ("Name", "TTY", "Type", "State", "Seat")):
            raise RuntimeError("Incomplete logind session information; check permissions")
        if fields["Name"] == runtime.USER or fields["TTY"] == "tty7" or fields["Type"] in {"wayland", "x11"}:
            sessions.append({"id": identity, **{name: fields[name] for name in ("Name", "TTY", "Type", "State", "Seat")}})
    return sessions


def _status() -> dict:
    service = _service()
    result = {"state": _state(service), "service": service, "browser_ready": None,
              "unit_missing": service["LoadState"] == "not-found",
              "readiness": "Service and logind state do not establish browser/page readiness"}
    try:
        account = _account()
        result["account"] = {"name": runtime.USER, "uid": account.pw_uid, "managed": True}
    except ERRORS as exc:
        result["account"] = {"name": runtime.USER, "error": str(exc)}
    try:
        result["sessions"] = _sessions()
    except ERRORS as exc:
        result["sessions_error"] = str(exc)
    return result


def _platform() -> str:
    release = read_os_release()
    machine = resolve_machine_type("auto")
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        raise RuntimeError("Kiosk requires Debian 13 / Proxmox VE 9")
    if machine not in {"hardware", "vm"} or not can_manage_system_services(machine):
        raise RuntimeError("Kiosk requires hardware or VM console with persistent systemd services")
    return f"Debian 13 / Proxmox-compatible {machine}"


def _executables() -> str:
    missing = [path for path in EXECUTABLES if not os.access(path, os.X_OK)]
    if missing:
        raise RuntimeError("Missing executable(s); rerun kiosk package setup: " + ", ".join(missing))
    return "Required greetd, Cage, Firefox ESR and session executables available"


def _devices() -> str:
    for path, pattern, label in ((steps.VT.parent, steps.VT.name, "tty7"),
                                  (steps.DRM_DIR, "card[0-9]*", "DRM card"),
                                  (steps.INPUT_DIR, "event*", "input event device")):
        # iterdir raises for denied directories; Path.glob can silently hide that.
        paths = [child for child in path.iterdir() if child.match(pattern)]
        if not any(stat.S_ISCHR(child.stat().st_mode) for child in paths):
            raise RuntimeError(f"Missing {label}; attach console hardware and check device permissions")
    return "tty7, DRM graphics and input device nodes present"


def _managed(service: dict[str, str]) -> dict:
    owned = steps._owned_service()
    if owned is None:
        raise RuntimeError("Managed kiosk unit is absent; run kiosk setup")
    if (service["LoadState"] != "loaded" or service["FragmentPath"] != str(steps.UNIT_DIR / steps.SERVICE)
            or service["DropInPaths"] or service["NeedDaemonReload"] != "no"):
        raise RuntimeError("Kiosk unit is overridden, stale or not loaded; inspect unit and daemon-reload explicitly")
    return owned


def _configuration(owned: dict) -> str:
    """Validate the selected generation, delegating session data to its owner."""
    try:
        for directory in (steps.UNIT_DIR, steps.CONFIG_DIR, steps.CONFIG_DIR.parent):
            with directory_fd(str(directory)) as descriptor:
                info = os.fstat(descriptor)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise RuntimeError("Unsafe kiosk configuration directory")
        steps._configuration_files()
        starts = [line.removeprefix("ExecStart=") for line in owned["content"].splitlines()
                  if line.startswith("ExecStart=")]
        if len(starts) != 1:
            raise ValueError("Ambiguous greetd command")
        argv = shlex.split(starts[0])
        if len(argv) != 3 or argv[:2] != ["/usr/sbin/greetd", "--config"]:
            raise ValueError("Unexpected greetd command")
        path = Path(argv[2])
        validate_filesystem_path(str(path), must_exist=True)
        if path.parent != steps.CONFIG_DIR or path.suffix != ".toml" or not steps.GENERATED_NAME.fullmatch(path.name):
            raise ValueError("Unmanaged greetd configuration path")
        snapshot = steps._owned(path)
        if snapshot is None:
            raise ValueError("Missing greetd configuration")
        config = tomllib.loads(snapshot["content"])
        if config["terminal"]["vt"] != 7 or config["general"]["source_profile"] is not False:
            raise ValueError("Unexpected console configuration")
        for name in ("initial_session", "default_session"):
            if config[name]["user"] != runtime.USER:
                raise ValueError("Unexpected session account")
        session = shlex.split(config["initial_session"]["command"])
        if (len(session) != 10 or session[:6] != ["/usr/bin/dbus-run-session", "--", "/usr/bin/systemd-cat",
                                                "--identifier=basaltwater-kiosk", "/usr/bin/python3", "-I"]
                or session[-3:] != ["session", "--config", str(path.with_suffix(".json"))]):
            raise ValueError("Unexpected kiosk session command")
        script = Path(session[6])
        validate_filesystem_path(str(script), must_exist=True)
        script_info = steps.snapshot_unit_file(str(script))
        if script_info is None or script_info["uid"] != 0 or script_info["mode"] & 0o022:
            raise ValueError("Unsafe kiosk runtime script")
        if shlex.split(config["default_session"]["command"]) != ["/usr/bin/python3", "-I", str(script), "idle"]:
            raise ValueError("Unexpected kiosk idle command")
        runtime.configuration(path.with_suffix(".json"))
    except PermissionError as exc:
        raise PermissionError("Kiosk configuration inaccessible; rerun doctor as root to verify") from exc
    except (*ERRORS, TypeError, IndexError) as exc:
        # TOML/JSON parsers and path validators can echo sensitive config text.
        raise RuntimeError("Managed kiosk configuration is invalid; inspect and rerun kiosk setup locally") from exc
    return "Selected managed greetd and session configuration valid (destination redacted)"


def _home() -> str:
    account = _account()
    with directory_fd(str(runtime.HOME)) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != account.pw_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError("Kiosk home must be owned by its account with mode 0700")
    return "Private kiosk home ownership valid"


def _password() -> str:
    if os.geteuid() != 0:
        raise PermissionError("Password lock inaccessible; rerun doctor as root to verify")
    _account()
    if _command(["/usr/bin/passwd", "--status", runtime.USER]).split()[:2] != [runtime.USER, "L"]:
        raise RuntimeError("Kiosk password must remain locked; administrator migration required")
    return "Dedicated account password locked"


def _conflicts() -> str:
    for path in (steps.DISPLAY_MANAGER, steps.UNIT_DIR / "greetd.service", steps.VENDOR_MASK):
        if os.path.lexists(path) and not (path.is_symlink() and path.resolve() == Path("/dev/null")):
            raise RuntimeError("Existing console display manager/override requires administrator migration")
    fields = _fields(_command(["/usr/bin/systemctl", "show", "greetd.service",
                              "--property=LoadState,ActiveState,UnitFileState", "--no-pager"]))
    if (fields.get("LoadState") not in {"loaded", "not-found", "masked"}
            or fields.get("ActiveState") not in {"inactive", "failed"}
            or fields.get("UnitFileState") not in {"", "disabled", "not-found", "masked", "masked-runtime"}):
        raise RuntimeError("Disable/stop the vendor greetd service before starting the managed kiosk")
    return "No competing console display manager"


def _session_check() -> str:
    for session in _sessions():
        if session["Name"] != runtime.USER:
            raise RuntimeError("Log out other graphical/tty7 sessions before starting kiosk")
    return "No competing graphical or tty7 logind sessions"


def _doctor(*, launching: bool = False) -> dict:
    checks = []

    def check(name, operation):
        try:
            detail = operation()
        except ERRORS as exc:
            checks.append({"name": name, "ok": False, "inaccessible": isinstance(exc, PermissionError), "detail": str(exc)})
            return None
        checks.append({"name": name, "ok": True, "detail": detail if isinstance(detail, str) else "Verified"})
        return detail

    for name, operation in (("platform", _platform), ("executables", _executables), ("devices", _devices),
                            ("account", _account), ("password_lock", _password), ("home", _home),
                            ("display_manager", _conflicts), ("logind_sessions", _session_check)):
        check(name, operation)
    service = check("systemd", _service)
    if service is not None:
        owned = check("managed_unit", lambda: _managed(service))
        if owned is not None:
            check("configuration", lambda: _configuration(owned))
        if not launching:
            def health():
                state = _state(service)
                if state not in {"running", "disabled", "stopped"}:
                    raise RuntimeError(f"Kiosk service is {state}; inspect systemctl status and local journal")
                if state == "stopped" and service["UnitFileState"] != "disabled":
                    raise RuntimeError("Boot-enabled kiosk is stopped; inspect local logs or start it explicitly")
                if state != "running" and any(item["Name"] == runtime.USER for item in _sessions()):
                    raise RuntimeError("Stopped kiosk retains logind sessions; run kiosk stop as root for checked cleanup")
                return "Service running; browser readiness unverified" if state == "running" else "Intentionally stopped; boot CLI mode"
            check("service_state", health)
    return {"healthy": all(item["ok"] for item in checks), "checks": checks,
            "browser_ready": None, "service": service}


def _mutate(command: str) -> dict:
    if os.geteuid() != 0:
        raise PermissionError("Kiosk start/stop/restart requires root")
    if not can_manage_system_services(resolve_machine_type("auto")):
        raise RuntimeError("This machine cannot manage persistent systemd services")
    _managed(_service())
    if command != "stop":
        diagnosis = _doctor(launching=True)
        if not diagnosis["healthy"]:
            return {**diagnosis, "error": "Kiosk preflight failed; resolve doctor checks before starting"}
    if command == "stop":
        failure = None
        try:
            _command(["/usr/bin/systemctl", command, steps.SERVICE], timeout=90)
        except ERRORS as exc:
            failure = exc
        try:
            stopped = _service()
            if stopped["ActiveState"] not in {"inactive", "failed"}:
                raise RuntimeError("Kiosk producer is still active or transitioning; session cleanup was not attempted")
            _account()
            # A stopped/failed greetd cannot recreate the PAM scopes being reaped.
            # systemctl stop can succeed despite failed ExecStopPost teardown.
            runtime.cleanup_sessions()
        except ERRORS as exc:
            if failure is not None:
                raise RuntimeError(f"{failure}; checked cleanup incomplete: {exc}") from failure
            raise
        if failure is not None:
            raise failure
    else:
        _command(["/usr/bin/systemctl", command, steps.SERVICE], timeout=150 if command == "restart" else 90)
    result = _status()
    service = result["service"]
    expected = "inactive" if command == "stop" else "active"
    if service["ActiveState"] != expected or service["Result"] not in {"", "success"}:
        result["error"] = f"Kiosk {command} did not reach a successful {expected} state; inspect local logs"
    result["action"] = command
    return result


def run_kiosk_command(args: argparse.Namespace) -> int:
    try:
        command = args.kiosk_command
        if command == "status":
            result = _status()
        elif command == "doctor":
            result = _doctor()
        elif command in {"start", "stop", "restart"}:
            result = _mutate(command)
        else:
            raise ValueError("Unknown kiosk command")
        print(json.dumps(result, indent=2))
        return int("error" in result or result.get("healthy") is False or result.get("state") == "failed")
    except ERRORS as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
