"""Public contracts for reviewing individual storage routines without filesystem IO."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

from lib.samba_accounts import HEX_ID, HEX_REVISION
from lib.validation import validate_filesystem_path, validate_scrub_specs, validate_sync_specs

ROUTINE_HELPER = "/opt/basaltwater/sync/routine_job.py"
ROUTINE_STATE = "/var/lib/basaltwater-privilege-broker/storage-routine.json"
ROUTINE_UNIT = "basaltwater-storage-routine.service"
ROUTINE_TIMEOUT = 6 * 60 * 60
MAX_ROUTINES = 256
SPEC_FIELDS = {"sync": "sync_specs", "backup": "backup_specs", "scrub": "scrub_specs"}


def routine_identity(kind: str, spec: list[str]) -> str:
    """Stable lexical identity; relative scrub databases have one canonical ID."""
    if kind not in SPEC_FIELDS or len(spec) < 2 or any(not isinstance(path, str) for path in spec[:2]):
        raise ValueError("Invalid routine identity")
    first, second = spec[:2]
    if kind == "scrub" and not os.path.isabs(second):
        second = os.path.join(first, second)
    identity = [kind, os.path.normpath(first), os.path.normpath(second)]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def validate_routine(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"kind", "spec", "paused"}:
        raise ValueError("Invalid storage routine fields")
    kind, spec = value["kind"], value["spec"]
    if (not isinstance(kind, str) or kind not in SPEC_FIELDS or type(value["paused"]) is not bool
            or not isinstance(spec, list) or len(spec) != (4 if kind == "scrub" else 3)
            or any(not isinstance(field, str) for field in spec)):
        raise ValueError("Invalid storage routine declaration")
    for path in spec[:2]:
        if (not path or len(path) > 512 or not os.path.isabs(path) or path == "/"
                or path.startswith("//") or os.path.normpath(path) != path
                or any(ord(character) < 32 or ord(character) == 127 for character in path)):
            raise ValueError("Routine paths must be normalized absolute directories, up to 512 characters")
        validate_filesystem_path(path)
    if kind == "scrub":
        validate_scrub_specs([spec])
        if Path(spec[0]).is_relative_to(Path(spec[1])):
            raise ValueError("Parity storage cannot equal or contain its scrub root")
    else:
        validate_sync_specs([spec])
    return {"kind": kind, "spec": list(spec), "paused": value["paused"]}


def configured_routines(saved: dict, paused: set[str]) -> list[dict]:
    """Return a complete supported selection; an omitted row cannot become removal."""
    rows = []
    identities, mirrors, scrub_keys, parity = set(), set(), set(), []
    for kind, key in SPEC_FIELDS.items():
        declarations = saved.get(key)
        if declarations is None:
            continue
        if not isinstance(declarations, list) or len(rows) + len(declarations) > MAX_ROUTINES:
            raise ValueError("Routine management needs a complete inventory of up to 256 routines")
        for raw in declarations:
            if not isinstance(raw, list) or any(not isinstance(part, str) for part in raw):
                raise ValueError("Invalid saved storage routine")
            spec = list(raw)
            if kind == "scrub" and len(spec) == 4 and not os.path.isabs(spec[1]):
                spec[1] = os.path.normpath(os.path.join(spec[0], spec[1]))
            identifier = routine_identity(kind, spec)
            row = validate_routine({"kind": kind, "spec": spec, "paused": identifier in paused})
            if identifier in identities:
                raise ValueError("Duplicate storage routine identity")
            identities.add(identifier)
            if kind == "scrub":
                keys = {":".join(raw[:2]), ":".join(spec[:2])}
                if keys & scrub_keys:
                    raise ValueError("Duplicate or ambiguous scrub endpoints")
                scrub_keys.update(keys)
                database = Path(spec[1])
                if any(database.is_relative_to(previous) or previous.is_relative_to(database) for previous in parity):
                    raise ValueError("Scrub routines need separate parity directories")
                parity.append(database)
            else:
                # The existing runtime cadence key is shared by sync and backup.
                key = ":".join(spec[:2])
                if key in mirrors:
                    raise ValueError("Duplicate or ambiguous mirror endpoints")
                mirrors.add(key)
            rows.append(row)
    for row in rows:
        if row["kind"] != "scrub":
            destination = Path(row["spec"][1])
            if any(destination.is_relative_to(database) or database.is_relative_to(destination) for database in parity):
                raise ValueError("Mirror destinations cannot overlap parity storage")
    return rows


def validate_routine_action(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"action", "before", "after", "revision", "job_id"}:
        raise ValueError("Invalid storage routine review")
    if value["action"] not in ("change", "run"):
        raise ValueError("Choose a routine change or run once")
    result = dict(value)
    for key in ("before", "after"):
        if value[key] is not None:
            result[key] = validate_routine(value[key])
    before, after = result["before"], result["after"]
    if value["action"] == "run":
        if before is None or after is not None:
            raise ValueError("Run once requires one existing reviewed routine")
    elif (before == after or before is not None and after is not None and before["kind"] != after["kind"]):
        raise ValueError("Choose a routine change; changing kind requires a separate replacement")
    for key, pattern in (("revision", HEX_REVISION), ("job_id", HEX_ID)):
        if not isinstance(value[key], str) or not pattern.fullmatch(value[key]):
            raise ValueError("Invalid storage routine review identity")
    return result


def routine_label(value: dict) -> str:
    if value["action"] == "run":
        return "Run routine once"
    if value["after"] is None:
        return "Remove routine"
    if value["before"] is None:
        return "Create routine"
    if all(value["before"][key] == value["after"][key] for key in ("kind", "spec")):
        return "Pause routine" if value["after"]["paused"] else "Resume routine"
    return "Update routine"


def validate_routine_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    if (set(value) not in ({"available", "revision", "routines"}, {"available", "revision", "routines", "history"}) or not isinstance(value["revision"], str)
            or not HEX_REVISION.fullmatch(value["revision"]) or not isinstance(value["routines"], list)
            or len(value["routines"]) > MAX_ROUTINES):
        raise ValueError("Invalid routine management snapshot")
    saved, paused = {}, set()
    for row in value["routines"]:
        row = validate_routine(row)
        saved.setdefault(SPEC_FIELDS[row["kind"]], []).append(row["spec"])
        if row["paused"]:
            paused.add(routine_identity(row["kind"], row["spec"]))
    configured_routines(saved, paused)
    if "history" in value:
        value = dict(value)
        try:
            value["history"] = validate_routine_history(value["history"], {
                routine_identity(row["kind"], row["spec"]) for row in value["routines"]})
        except (ValueError, TypeError, OverflowError):
            value["history"] = {"available": False}
    return value


def validate_routine_history(value: object, identities: set[str]) -> dict:
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    if (set(value) != {"available", "routines"} or not isinstance(value["routines"], dict)
            or set(value["routines"]) != identities):
        raise ValueError("Invalid routine history selection")
    def timestamp(number):
        return type(number) in {int, float} and math.isfinite(number) and number >= 0
    for entry in value["routines"].values():
        if not isinstance(entry, dict) or set(entry) != {"cadence_at", "retry", "parity_at", "parity_retry"}:
            raise ValueError("Invalid routine history")
        for key in ("cadence_at", "parity_at"):
            if entry[key] is not None and not timestamp(entry[key]):
                raise ValueError("Invalid routine history timestamp")
        for key in ("retry", "parity_retry"):
            retry = entry[key]
            if retry is not None and (not isinstance(retry, dict) or set(retry) != {"failed_at", "retry_after", "failures"}
                    or type(retry["failures"]) is not int or not 1 <= retry["failures"] <= 6
                    or not timestamp(retry["failed_at"]) or not timestamp(retry["retry_after"])
                    or not retry["failed_at"] <= retry["retry_after"] <= retry["failed_at"] + 86400):
                raise ValueError("Invalid routine retry history")
    return value


def routine_effect(value: dict) -> str:
    row = value["after"] or value["before"]
    if value["action"] == "run":
        effect = ("Run one full verification and staged repair, retain originals, update parity and record findings. "
                  if row["kind"] == "scrub" else
                  "Mirror the source to the destination, including replacement and deletion of destination files; this is not a versioned backup. ")
        return effect + "Ignore this routine's cadence and retry delay for this run only; keep its paused state. Preserve all mount and integrity guards. Record completion/retry history."
    return ("Save only the reviewed storage declaration and paused state; do not copy files or run PAR2 now. "
            "Active mirrors can replace and delete destination content on a later scheduled run. "
            "Active scrubs perform scheduled verification/repair and daily parity maintenance. "
            "Pause skips both scrub verification and parity maintenance; it retains integrity protections for mirrors. "
            "Removal retains files, parity, findings, completion history and logs. Removing a scrub also stops mirror safeguards based on its findings; pause keeps those protections. "
            "An interrupted save can require setup recovery.")


def routine_dispatch_argv(value: dict) -> list[str]:
    value = validate_routine_action(value)
    return ["/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--collect", "--unit=" + ROUTINE_UNIT,
            "--property=Type=exec", "--property=Restart=no", f"--property=RuntimeMaxSec={ROUTINE_TIMEOUT}",
            "--property=TimeoutStopSec=30", "--property=KillMode=control-group", "--property=UMask=0077",
            "--property=StandardInput=null", "--property=StandardOutput=null", "--property=StandardError=null",
            "--working-directory=/", "--uid=root", "--", "/usr/bin/python3", "-I", ROUTINE_HELPER, value["job_id"]]
