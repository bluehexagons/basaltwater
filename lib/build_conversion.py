"""Private one-time serving snapshot and persistent identity conversion proof."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

from lib.atomic_io import write_json_atomic
from lib.build_protocol import decode_document, encode_document
from lib.build_target_maintenance import read_protected, trusted_path
from lib.build_target_policy import parse_target_policy
from lib.deployment import DeploymentOrchestrator
from lib.validation import validate_filesystem_path


SNAPSHOT = "/var/lib/basaltwater/build-conversion.json"
NGINX = "/etc/nginx"
UNITS = "/etc/systemd/system"
MAX_BYTES = 8 * 1024**2
MAX_FILES = 256


def _load() -> dict | None:
    raw = read_protected(SNAPSHOT, mode=0o600, maximum=MAX_BYTES)
    if raw is None:
        return None
    value = decode_document(raw, maximum=MAX_BYTES)
    if (not isinstance(value, dict) or set(value) != {"schema", "state", "applications", "data", "files"}
            or type(value["schema"]) is not int or value["schema"] != 1 or value["state"] not in {"captured", "completed"}
            or not isinstance(value["applications"], dict) or not isinstance(value["data"], dict)
            or not isinstance(value["files"], list)):
        raise ValueError("Invalid retained conversion snapshot")
    return value


def _identity(path: str) -> list[int] | None:
    """Track the persistent directory itself; database contents may change."""
    validate_filesystem_path(path)
    # All parents of the service-owned data directory must stay root controlled.
    if not trusted_path(os.path.dirname(path), directory=True, missing=True):
        return None
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Conversion data path must be a directory without symlinks")
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid]


def capture_conversion(enrollment: dict) -> None:
    """Capture once before first enrollment; never replace pending evidence.

    Called under target setup ownership. Snapshots are configuration evidence,
    not database backups and never automatic rollback inputs.
    """
    if os.geteuid() != 0:
        raise PermissionError("Conversion evidence requires root")
    previous = _load()
    if previous is not None:
        if previous["state"] != "completed":
            raise RuntimeError("Unfinished conversion snapshot requires operator reconciliation")
        return
    applications = {}
    data = {}
    paths = []
    for application, binding in parse_target_policy(enrollment["policy"])["applications"].items():
        root = binding["deployment_root"]
        validate_filesystem_path(root)
        applications[application] = dict(root=root, routes=[
            dict(name=name, domain=component["domain"], path=component["path"])
            for name, component in binding["components"].items()])
        manager = DeploymentOrchestrator(base_dir=os.path.dirname(root), deploy_user="root", deploy_group="root")
        for name, component in binding["components"].items():
            # The shared root convention is identical for source and artifacts.
            descriptor = SimpleNamespace(name=name)
            shared_data = manager._component_data_dir(root, descriptor)
            identity = _identity(shared_data)
            if identity is not None:
                data[shared_data] = identity
            unit, _user = manager._service_identity(root, descriptor)
            paths.append(os.path.join(UNITS, unit + ".service"))
    for directory in (os.path.join(NGINX, "sites-available"), os.path.join(NGINX, "sites-enabled")):
        if trusted_path(directory, directory=True, missing=True):
            names = os.listdir(directory)
            if len(names) > MAX_FILES:
                raise ValueError("Conversion configuration snapshot exceeds file bound")
            paths.extend(os.path.join(directory, name) for name in names)
    files = []
    total = 0
    for path in sorted(set(paths)):
        validate_filesystem_path(path)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        trusted_path(os.path.dirname(path), directory=True)
        if info.st_uid != 0 or info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode):
            raise ValueError("Conversion serving configuration must be controlled by root")
        if stat.S_ISLNK(info.st_mode):
            files.append(dict(path=path, symlink=os.readlink(path)))
        else:
            mode = stat.S_IMODE(info.st_mode)
            raw = read_protected(path, mode=mode, maximum=1024**2)
            if raw is None:
                raise ValueError("Conversion configuration disappeared")
            total += len(raw)
            if total > 4 * 1024**2:
                raise ValueError("Conversion configuration snapshot exceeds byte bound")
            files.append(dict(path=path, mode=mode, sha256=hashlib.sha256(raw).hexdigest(),
                              content_base64=base64.b64encode(raw).decode("ascii")))
        if len(files) > MAX_FILES:
            raise ValueError("Conversion configuration snapshot exceeds file bound")
    snapshot = dict(schema=1, state="captured", applications=applications, data=data, files=files)
    encode_document(snapshot, maximum=MAX_BYTES)
    trusted_path(str(Path(SNAPSHOT).parent), directory=True)
    write_json_atomic(SNAPSHOT, snapshot, mode=0o600)
    print("  Saved private serving configuration and persistent-directory identities for conversion")


def finish_conversion(activator, expected: list[dict]) -> None:
    """Verify the entire captured inventory under final target maintenance."""
    snapshot = _load()
    if snapshot is None or snapshot["state"] == "completed":
        return
    installed = activator._load(initialize=False)["applications"]
    expected_apps = {item["application"] for item in expected}
    for application, previous in snapshot["applications"].items():
        if application not in expected_apps:
            continue
        current = installed.get(application)
        if current is None or current["root"] != previous["root"]:
            raise ValueError("Converted release root differs from captured inventory")
        routes = {(item["name"], item["domain"], item["path"]) for item in previous["routes"]}
        observed = {(item["name"], item["domain"], item["path"]) for item in current["bindings"]["components"]}
        if routes != observed:
            raise ValueError("Converted routes differ from captured inventory")
    for path, identity in snapshot["data"].items():
        if _identity(path) != identity:
            raise ValueError("Conversion changed an existing persistent-directory identity")
    snapshot["state"] = "completed"
    write_json_atomic(SNAPSHOT, snapshot, mode=0o600)
