"""Reviewable single-file integrity operations for Shares & Data."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from lib.validation import validate_filesystem_path

DATA_HELPER = "/opt/basaltwater/common/service_tools/data_job.py"
DATA_STATE = "/var/lib/basaltwater-privilege-broker/data-job.json"
DATA_UNIT = "basaltwater-data-management.service"
DATA_TIMEOUT = 6 * 60 * 60
BATCH_LIMIT = 10
BATCH_MAX_BYTES = 10000
BATCH_ACTIONS = {"verify", "quarantine", "delete"}
HEX_ID = re.compile(r"[a-f0-9]{32}\Z")
HEX_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
DATA_ACTIONS = {
    "verify": ("Verify file", "Verify this file against existing parity and update its recorded finding."),
    "repair": ("Repair file", "Reconstruct one staged file using existing parity, verify it, retain originals and publish the verified candidate."),
    "restore": ("Restore file", "Stage the selected independent copy, verify it against existing parity, retain originals and publish only a verified candidate."),
    "accept": ("Accept current content", "Establish this file's current content as the new parity baseline. Existing parity will no longer identify earlier damage. Only accept content you have independently confirmed is correct; originals and parity are retained."),
    "quarantine": ("Quarantine corrupt files", "Move the reviewed files into private quarantine; retain parity and protect mirror copies."),
    "delete": ("Permanently delete files", "Permanently remove the reviewed source bytes; retain parity, disposition history and mirror protection."),
}


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("Invalid data action path")
    validate_filesystem_path(value)
    if not os.path.isabs(value) or os.path.normpath(value) != value or value.startswith("//"):
        raise ValueError("Data action paths must be absolute and normalized")
    return value


def validate_data_action(value: object) -> dict:
    """Validate public parameters identically before review and execution."""
    if isinstance(value, dict) and "items" in value:
        if set(value) != {"action", "directory", "database", "items", "job_id"}:
            raise ValueError("Invalid batch data action fields")
        if not isinstance(value["action"], str) or value["action"] not in BATCH_ACTIONS:
            raise ValueError("Unknown batch data action")
        if not isinstance(value["items"], list) or not 1 <= len(value["items"]) <= BATCH_LIMIT:
            raise ValueError("Select between 1 and 10 files")
        seen = set()
        for item in value["items"]:
            if not isinstance(item, dict) or set(item) != {"file", "finding"}:
                raise ValueError("Invalid batch item fields")
            validate_data_action({key: value[key] for key in ("directory", "database", "job_id")} |
                                 {"action": "verify", "backup": "", **item})
            if item["file"] in seen:
                raise ValueError("Duplicate batch file")
            seen.add(item["file"])
        if len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()) > BATCH_MAX_BYTES:
            raise ValueError("Batch parameters exceed 10000 bytes; select fewer files")
        return json.loads(json.dumps(value))
    if not isinstance(value, dict) or set(value) != {"action", "directory", "database", "file", "backup", "finding", "job_id"}:
        raise ValueError("Invalid data action fields")
    if not isinstance(value["action"], str) or value["action"] not in {"verify", "repair", "restore", "accept"}:
        raise ValueError("Unknown data action")
    for field in ("directory", "database", "file"):
        _path(value[field])
    root, database, path = (Path(value[field]) for field in ("directory", "database", "file"))
    if root == Path("/") or not path.is_relative_to(root) or path == root or path.is_relative_to(database):
        raise ValueError("Select a file under its scrub root and outside parity storage")
    if value["action"] == "restore":
        _path(value["backup"])
        if value["backup"] == value["file"]:
            raise ValueError("Restore requires a separate backup file")
    elif value["backup"] != "":
        raise ValueError("A backup path is allowed only for restore")
    if not isinstance(value["finding"], str) or not HEX_DIGEST.fullmatch(value["finding"]):
        raise ValueError("Invalid finding revision")
    if not isinstance(value["job_id"], str) or not HEX_ID.fullmatch(value["job_id"]):
        raise ValueError("Invalid data job identity")
    return dict(value)


def finding_revision(entry: dict) -> str:
    """Bind review to the recorded classification, check time and file identity."""
    public = {key: entry.get(key) for key in ("state", "category", "last_checked", "file_identity")}
    return hashlib.sha256(json.dumps(public, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def data_dispatch_argv(parameters: dict) -> list[str]:
    value = validate_data_action(parameters)
    return [
        "/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--collect",
        f"--unit={DATA_UNIT}", "--property=Type=exec", "--property=Restart=no",
        f"--property=RuntimeMaxSec={DATA_TIMEOUT}", "--property=TimeoutStopSec=30",
        "--property=KillMode=control-group", "--property=UMask=0077",
        "--property=StandardInput=null", "--property=StandardOutput=null", "--property=StandardError=null",
        "--working-directory=/", "--uid=root", "--", "/usr/bin/python3", "-I", DATA_HELPER,
        value["job_id"],
    ]
