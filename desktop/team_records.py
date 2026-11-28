"""Private desktop team assignments and append-only coordination records."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

from desktop.preflight import instruction_page
from desktop.qa_artifacts import clock, file_digest, new_bundle, snapshot, stamp
from lib.atomic_io import fsync_tree, read_json_file, rename_path_durable, write_json_atomic
from lib.validation import validate_filesystem_path, validate_no_control_characters

BACKENDS = ("cachyos-native", "debian-desktop")
MAX_RECORDS = 2048
MAX_JSON = 256 * 1024


def identifier(value, label="Identifier"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value):
        raise ValueError(f"{label} must be a lowercase identifier of at most 48 characters")
    return value


def message(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2000 or len(value) > 2000:
        raise ValueError(f"{label} must contain 1–2000 characters")
    validate_no_control_characters(value, label)
    if len(json.dumps(value)) > 4096:
        raise ValueError(f"{label} exceeds the encoded record size")
    return value


def object_file(path, *, max_bytes=MAX_JSON):
    value = read_json_file(str(path), max_bytes=max_bytes)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def private_directory(path):
    validate_filesystem_path(str(path), must_exist=True)
    folder = Path(path).absolute()
    info = folder.lstat()
    if (folder != folder.resolve() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("Expected an owned private directory without symlinks")
    return folder


@contextmanager
def locked(folder, name, *, nonblocking=False):
    fd = os.open(folder / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Unsafe team lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError as exc:
            raise TeamBusy("Desktop operation in progress; wait for its release receipt and inspect status") from exc
        yield
    finally:
        os.close(fd)


class TeamBusy(RuntimeError):
    pass


def _selection(value, allowed, label, maximum=16):
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"Invalid {label}")
    return value


def prepare(spec_path, output):
    spec = object_file(spec_path)
    assignment = identifier(spec.get("assignment"), "Assignment")
    objective = message(spec.get("objective"), "Objective")
    resources = spec.get("resources", {})
    if not isinstance(resources, dict) or len(resources) > 8:
        raise ValueError("Select at most eight named desktop resources")
    for name, backend in resources.items():
        identifier(name, "Resource")
        if backend not in BACKENDS:
            raise ValueError("Resource backend must be cachyos-native or debian-desktop")
    scopes = spec.get("scopes", ["shared"])
    if not isinstance(scopes, list) or "shared" not in scopes or len(scopes) > 16:
        raise ValueError("Declare shared and at most fifteen additional observation scopes")
    scopes = [identifier(scope, "Scope") for scope in scopes]
    if len(set(scopes)) != len(scopes):
        raise ValueError("Observation scopes must be unique")
    workers = spec.get("workers")
    if not isinstance(workers, dict) or "coordinator" not in workers or not 1 <= len(workers) <= 16:
        raise ValueError("Declare one to sixteen workers including coordinator")
    briefs = {}
    for name, worker in workers.items():
        identifier(name, "Worker")
        if not isinstance(worker, dict):
            raise ValueError("Expected a worker object")
        resource = worker.get("resource")
        if resource is not None and (name == "coordinator" or not isinstance(resource, str) or resource not in resources):
            raise ValueError("Worker resource must be declared; coordinator can cover any resource")
        watches = _selection(worker.get("watches", ["shared"]), scopes, "worker watches")
        if "shared" not in watches:
            raise ValueError("Workers must watch the shared observation scope")
        instructions = worker.get("instructions", [])
        if (not isinstance(instructions, list) or len(instructions) > 16
                or any(not isinstance(path, str) or not Path(path).is_absolute() for path in instructions)):
            raise ValueError("Select at most sixteen absolute instruction paths per worker")
        workspace = worker.get("workspace")
        if workspace is not None:
            validate_filesystem_path(workspace)
            if not Path(workspace).is_absolute():
                raise ValueError("Workspace must be an absolute path in the worker's environment")
        pages = [instruction_page(path, characters=0) for path in instructions]
        briefs[name] = {
            "role": message(worker.get("role"), "Role"),
            "task": message(worker.get("task"), "Task"),
            "acceptance": message(worker.get("acceptance"), "Acceptance"),
            "resource": resource, "backend": resources.get(resource), "watches": watches,
            "workspace": workspace,
            "instructions": [{key: page[key] for key in ("path", "sha256", "total_characters", "next_argv")}
                             for page in pages],
        }
    root = new_bundle(output)
    for directory in ("workers", "observations", "decisions", "results", "leases"):
        (root / directory).mkdir(mode=0o700)
    data = {"schema_version": 1, "kind": "desktop-team", "assignment": assignment,
            "objective": objective, "scopes": scopes, "resources": resources,
            "workers": {name: {"resource": brief["resource"], "watches": brief["watches"]}
                        for name, brief in briefs.items()}, "created": stamp()}
    for name, brief in briefs.items():
        folder = root / "workers" / name
        folder.mkdir(mode=0o700)
        (folder / "evidence").mkdir(mode=0o700)
        artifact = folder / "brief.json"
        write_json_atomic(str(artifact), {**brief, "schema_version": 1, "kind": "desktop-team-brief",
                          "assignment": assignment, "team": str(root), "worker": name,
                          "objective": objective, "evidence_directory": str(folder / "evidence"),
                          "checkpoint": str(folder / "checkpoint.md"),
                          "result_argv": ["basaltw", "desktop", "team", "result", str(root), name,
                                          "--spec", str(folder / "result-spec.json"), "--json"]})
    artifact = root / "team.json"
    write_json_atomic(str(artifact), data)
    groups = {resource: [name for name, brief in briefs.items() if brief["resource"] == resource]
              for resource in resources}
    return {"artifact": str(artifact), "team": str(root),
            "briefs": {name: str(root / "workers" / name / "brief.json") for name in briefs},
            "desktop_groups": groups,
            "offline_workers": [name for name in briefs if name != "coordinator" and briefs[name]["resource"] is None]}


def load_team(path):
    root = private_directory(path)
    team = object_file(root / "team.json")
    if team.get("schema_version") != 1 or team.get("kind") != "desktop-team":
        raise ValueError("Expected a desktop team directory from team prepare")
    return root, team


def require_worker(team, worker):
    identifier(worker, "Worker")
    if worker not in team["workers"]:
        raise ValueError("Worker is absent from this assignment")
    return team["workers"][worker]


def _ids(folder):
    return sorted(path.name for path in folder.iterdir() if re.fullmatch(r"[0-9]{6}", path.name))


def _record(folder, identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9]{6}", identity):
        raise ValueError("Record ID must have six digits")
    return object_file(folder / identity / "record.json")


def _publish(root, lane, team, payload, attachments=None):
    folder = root / lane
    existing = _ids(folder)
    index = int(existing[-1]) + 1 if existing else 1
    if index > MAX_RECORDS:
        raise ValueError("Team record limit reached; prepare a new assignment")
    identity = f"{index:06d}"
    destination = folder / identity
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=folder) as staging:
        build = Path(staging) / "record"
        build.mkdir(mode=0o700)
        files = {}
        for recipient, sources in (attachments or {}).items():
            owned = build / recipient
            owned.mkdir(mode=0o700)
            files[recipient] = []
            for i, source in enumerate(sources, start=1):
                suffix = Path(source).suffix.lower()
                name = f"{i:02d}{suffix}"
                pin = snapshot(source, owned / name)
                if lane == "observations":
                    _validate_view(owned / name, pin["bytes"])
                files[recipient].append({**pin, "artifact": f"{recipient}/{name}"})
        data = {"schema_version": 1, "kind": f"desktop-team-{lane[:-1]}", "id": identity,
                "assignment": team["assignment"], "created": stamp(), "clock": clock(), **payload,
                "files": files}
        write_json_atomic(str(build / "record.json"), data)
        fsync_tree(str(build))
        rename_path_durable(str(build), str(destination))
    return {"artifact": str(destination / "record.json"), **data}


def _files(sources, *, suffixes, maximum=8):
    if (not isinstance(sources, list) or len(sources) > maximum
            or any(not isinstance(path, str) or not Path(path).is_absolute()
                   or Path(path).suffix.lower() not in suffixes for path in sources)):
        raise ValueError("Invalid attachment paths or file types")
    return sources


def _head(root):
    ids = _ids(root / "observations")
    return _record(root / "observations", ids[-1]) if ids else {"id": "initial", "revisions": {}}


def _validate_view(path, size):
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Team views must be PNG captures or reviewed PNG crops")
    width, height = struct.unpack(">II", header[16:])
    if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > 4 * 1024 * 1024 or size > 16 * 1024 * 1024:
        raise ValueError("Team views may contain up to four megapixels and 16 MiB; use a reviewed crop")


def _capture_source(path):
    receipt = object_file(path, max_bytes=65536)
    fields = ("observation_id", "captured_at", "artifact_sha256", "geometry", "image_geometry", "image_origin")
    if (not isinstance(receipt.get("observation_id"), str)
            or not isinstance(receipt.get("captured_at"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("artifact_sha256", "")))):
        raise ValueError("Expected a retained helper capture receipt")
    return {key: receipt[key] for key in fields if key in receipt}


def observe(path, worker, after, spec):
    root, team = load_team(path)
    require_worker(team, worker)
    updates, views = spec.get("updates"), spec.get("views", {})
    if not isinstance(updates, dict) or not updates or set(updates) - set(team["scopes"]):
        raise ValueError("Supply changes for one or more declared scopes")
    updates = {scope: message(text, "Observation update") for scope, text in updates.items()}
    if not isinstance(views, dict) or set(views) - set(team["workers"]):
        raise ValueError("Views must be assigned to known workers")
    views = {recipient: _files(sources, suffixes=(".png",), maximum=4) for recipient, sources in views.items()}
    source = _capture_source(spec["capture"]) if spec.get("capture") is not None else None
    with locked(root, "records.lock"):
        head = _head(root)
        if head["id"] != after:
            raise ValueError(f"Observation head changed to {head['id']}; reconcile events before publishing")
        version = int(after) + 1 if after != "initial" else 1
        revisions = {**head["revisions"], **dict.fromkeys(updates, version)}
        if "shared" not in revisions:
            raise ValueError("The first observation must initialize shared state")
        return _publish(root, "observations", team, {"worker": worker, "after": after, "updates": updates,
                        "revisions": revisions, "capture": source}, attachments=views)


def view(path, worker, identity=None):
    root, team = load_team(path)
    watches = require_worker(team, worker)["watches"]
    observation = _record(root / "observations", identity) if identity else _head(root)
    identity = observation["id"]
    folder = root / "observations" / identity
    revisions = {scope: version for scope, version in observation["revisions"].items() if scope in watches}
    views = []
    summaries = {}
    for version in dict.fromkeys(revisions.values()):
        origin = f"{version:06d}"
        source = _record(root / "observations", origin)
        summaries.update({scope: text for scope, text in source["updates"].items() if revisions.get(scope) == version})
        for item in source["files"].get(worker, []):
            image = root / "observations" / origin / item["artifact"]
            if file_digest(image) != item["sha256"]:
                raise ValueError("Observation view changed after publication")
            views.append({**item, "artifact": str(image), "observation": origin,
                          "created": source["created"], "capture": source.get("capture")})
    return {"id": identity, "worker": worker, "revisions": revisions, "summaries": summaries, "views": views,
            **({"artifact": str(folder / "record.json"), "created": observation["created"]} if identity != "initial" else {})}


def _stale(decision, head):
    return [scope for scope, version in decision["based_on"].items()
            if head["revisions"].get(scope) != version]


def decide(path, worker, observation, spec):
    root, team = load_team(path)
    watches = require_worker(team, worker)["watches"]
    proposal = message(spec.get("proposal"), "Proposal")
    assumptions = spec.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 8:
        raise ValueError("Supply at most eight assumptions")
    assumptions = [message(item, "Assumption") for item in assumptions]
    source = _record(root / "observations", observation)
    if set(watches) - source["revisions"].keys():
        raise ValueError("Observation does not cover all watched scopes")
    with locked(root, "records.lock"):
        payload = {"worker": worker, "observation": observation, "proposal": proposal,
                   "assumptions": assumptions, "based_on": {scope: source["revisions"][scope] for scope in watches}}
        if stale := _stale(payload, _head(root)):
            raise ValueError(f"Decision needs a fresh observation for {', '.join(stale)}")
        return _publish(root, "decisions", team, payload)


def check(path, decision):
    root, _team = load_team(path)
    item = _record(root / "decisions", decision)
    head = _head(root)
    stale = _stale(item, head)
    return {**item, "head": head["id"], "current": not stale, "stale_scopes": stale,
            **({"error": "Decision needs a new observation for changed scopes", "issue": "stale_decision"} if stale else {})}


def result(path, worker, spec):
    root, team = load_team(path)
    require_worker(team, worker)
    state, delivery = spec.get("state"), spec.get("delivery", "none")
    if state not in ("ready", "finished", "blocked", "interrupted") or delivery not in (
            "none", "not-sent", "completed", "partial", "unknown"):
        raise ValueError("Invalid worker state or action delivery")
    summary = message(spec.get("summary"), "Worker result")
    next_step = message(spec.get("next"), "Next step")
    evidence = _files(spec.get("evidence", []), suffixes=(".png", ".json", ".md", ".txt"))
    observation, decision = spec.get("observation"), spec.get("decision")
    if observation is not None:
        _record(root / "observations", observation)
    if decision is not None and _record(root / "decisions", decision)["worker"] != worker:
        raise ValueError("Decision belongs to another worker")
    with locked(root, "records.lock"):
        return _publish(root, "results", team, {"worker": worker, "state": state, "delivery": delivery,
                        "summary": summary, "next": next_step, "observation": observation, "decision": decision},
                        attachments={"evidence": evidence} if evidence else None)


def results(path, after, worker=None):
    root, team = load_team(path)
    if worker is not None:
        require_worker(team, worker)
    if not re.fullmatch(r"[0-9]{6}", after):
        raise ValueError("Result cursor must have six digits")
    folder = root / "results"
    entries = [identity for identity in _ids(folder) if identity > after]
    rows = []
    for identity in entries[:20]:
        item = _record(folder, identity)
        if worker is None or item["worker"] == worker:
            rows.append({"artifact": str(folder / identity / "record.json"),
                         **{key: item[key] for key in ("id", "worker", "state", "delivery", "created",
                                                     "observation", "decision")}})
    return {"results": rows, "cursor": entries[min(20, len(entries)) - 1] if entries else after,
            "remaining": max(0, len(entries) - 20)}
