"""Launch supervised tasks from the actual Debian desktop, never guessed GUI state."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct

from desktop import development, session_runtime
from lib.validation import validate_filesystem_path, validate_no_control_characters


def _session_bus_owned(path: Path, uid: int) -> bool:
    """Accept dbus-run-session sockets in sticky /tmp with peer authentication."""
    try:
        info, parent = path.lstat(), path.parent.lstat()
        private = parent.st_uid == uid and not parent.st_mode & 0o022
        sticky = parent.st_uid == 0 and bool(parent.st_mode & stat.S_ISVTX)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid
                or not stat.S_ISDIR(parent.st_mode) or not (private or sticky)):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1)
            connection.connect(str(path))
            _, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            return peer_uid == uid
    except OSError:
        return False


def supervisor_environment(uid: int) -> tuple[dict[str, str], str]:
    """Read only the running supervisor's environment and owned local sockets."""
    runtime = session_runtime.runtime_directory().parent
    if not development._owned_socket(runtime / "bus", uid):
        return {}, "Owned user-systemd bus is unavailable; no task was launched"
    display = os.environ.get("DISPLAY", "")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    match = re.fullmatch(r"unix:path=([^,;%\s]+)(?:,guid=[0-9a-f]{32})?", bus)
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display) or match is None:
        return {}, "Supervisor has no supported local X11 display/session bus"
    socket = Path(match[1])
    validate_filesystem_path(str(socket))
    if not socket.is_absolute() or not _session_bus_owned(socket, uid):
        return {}, "Supervisor session bus is unavailable or not owned by the desktop account"
    authority = os.environ.get("XAUTHORITY") or str(Path(pwd.getpwuid(uid).pw_dir) / ".Xauthority")
    validate_filesystem_path(authority, must_exist=True)
    path = Path(authority)
    info = path.lstat()
    if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
            or info.st_uid != uid or info.st_mode & 0o022):
        return {}, "Supervisor X authority must be an owned regular file without write access for others"
    environment = {"DISPLAY": display, "XAUTHORITY": authority, "DBUS_SESSION_BUS_ADDRESS": bus,
                   "XDG_RUNTIME_DIR": str(runtime), "XDG_SESSION_TYPE": "x11",
                   "NO_AT_BRIDGE": "0", "GTK_MODULES": "atk-bridge", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1"}
    if os.environ.get("XDG_CURRENT_DESKTOP"):
        environment["XDG_CURRENT_DESKTOP"] = os.environ["XDG_CURRENT_DESKTOP"]
    for value in environment.values():
        validate_no_control_characters(value, "Desktop environment")
    return environment, "Existing supervisor X11/session-bus environment; live application readiness is unverified"


OPTIONS = {"engine", "scene", "argv", "dry_run", "headless", "quit_after", "rendering_method",
           "script", "manager", "recipe", "settings", "timeout", "export_check", "review_manifest",
           "verify_evidence", "require_artifact_kind", "playtest", "prepare", "asset_bundle", "before", "after"}


def handle_task(payload: dict, generation: str, *, recorder=None) -> dict:
    if not isinstance(payload.get("project"), str):
        raise ValueError("Development project must be an existing directory path")
    options = payload.get("options", {})
    if not isinstance(options, dict) or set(options) - OPTIONS:
        raise ValueError("Unknown development task options")
    for key, value in options.items():
        if key in ("dry_run", "headless", "export_check", "verify_evidence", "prepare"):
            valid = type(value) is bool
        elif key in ("quit_after", "timeout"):
            valid = type(value) is int or (key == "quit_after" and value is None)
        elif key == "require_artifact_kind":
            valid = isinstance(value, list) and all(isinstance(kind, str) for kind in value)
        elif key == "argv":
            valid = value is None or isinstance(value, list)
            if value:
                development.validate_argv(value)
        else:
            valid = value is None or isinstance(value, str)
        if not valid:
            raise ValueError(f"Invalid development option: {key}")
    action = payload.get("operation")
    if action == "doctor":
        if set(options) - {"engine", "export_check"}:
            raise ValueError("Doctor accepts only engine/export-check options")
        return development.doctor(payload.get("project"), **options, session_generation=generation)
    if action not in development.KINDS or "export_check" in options:
        raise ValueError("Unknown development launch operation")
    if action == "playtest" and not options.get("dry_run"):
        if recorder is None or str(recorder.path) != payload.get("artifact"):
            raise ValueError("Playtest recorder changed; inspect the active recording")
        options = {**options, "attach_playtest": recorder.attach_playtest}
    return development.launch(payload.get("project"), action, **options, session_generation=generation,
                              control_revision=payload.get("control_revision"))


def request_task(args: argparse.Namespace) -> dict:
    current = session_runtime.status()
    if current.get("state") != "running" or "supervised-development" not in current.get("capabilities", []):
        raise RuntimeError("Running desktop lacks supervised-development; refresh at a safe normal logout checkpoint")
    if args.development_command == "playtest" and "recorded-playtest" not in current["capabilities"]:
        raise RuntimeError("Running desktop lacks recorded-playtest; refresh at a safe normal logout checkpoint")
    options = {key: getattr(args, key) for key in OPTIONS if hasattr(args, key)}
    # Older running supervisors can still handle a check without evidence options.
    for key in ("verify_evidence", "require_artifact_kind", "prepare", "asset_bundle"):
        if not options.get(key):
            options.pop(key, None)
    if args.development_command != "doctor":
        options["dry_run"] = options.get("dry_run", False) or development.is_dry_run()
    payload = {"action": "development", "operation": args.development_command,
               "project": str(Path(args.project).expanduser().resolve()), "options": options,
               "generation": current["generation"], "control_revision": current["control_revision"]}
    if args.development_command == "playtest" and not options["dry_run"]:
        if not current.get("evidence_recording") or not current.get("evidence"):
            raise ValueError("Start an evidence recorder for this project before launching a playtest")
        payload["artifact"] = current["evidence"]
    try:
        return session_runtime.request(payload)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Development reply failed: {exc}. Inspect 'desktop develop list --project PATH' "
                           "and task status before retrying; a task may already have started") from exc
