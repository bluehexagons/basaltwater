"""Read project cleanup policy and evidence without executing repository commands."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from lib.atomic_io import parse_json
from lib.validation import validate_filesystem_path, validate_no_control_characters


POLICY_PATH = ".basaltwater/cleanup.json"
_PROTECTED = (".git", ".basaltwater", ".agents", ".codex", ".t3", ".env",
              ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".credentials", "credentials", "secrets")
_MAX_BYTES = 65536


def _text(value: object, label: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    validate_no_control_characters(value, label)
    return value


def _relative(value: object) -> str:
    path = _text(value, "project-relative path", 512)
    validate_filesystem_path(path)
    if (path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/"))
            or any(char in path for char in "*?[]\\")):
        raise ValueError("Cleanup paths must be literal relative paths below the project")
    return path


def _paths(value: object, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 16 or (not value and not allow_empty):
        raise ValueError("Expected up to 16 literal paths; rule targets cannot be empty")
    paths = [_relative(path) for path in value]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate cleanup paths")
    return paths


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _sensitive(path: str) -> bool:
    return any(part in _PROTECTED or part.startswith(".env.") for part in path.split("/"))


def _argv(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("Script commands need 1-32 literal argv strings")
    return [_text(argument, "script argument", 1024) for argument in value]


def validate_cleanup_policy(value: object) -> dict:
    """Require disjoint script/curated roots; commands remain untrusted descriptions."""
    if (not isinstance(value, dict) or set(value) - {"version", "instructions", "preserve", "rules"}
            or type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("Unsupported cleanup policy; expected version 1 with known fields")
    instructions = _text(value.get("instructions"), "cleanup instructions")
    preserve = _paths(value.get("preserve", []), allow_empty=True)
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not 1 <= len(raw_rules) <= 16:
        raise ValueError("Expected 1-16 cleanup rules")
    rules = []
    names = set()
    for raw in raw_rules:
        if not isinstance(raw, dict) or set(raw) - {"name", "paths", "strategy", "instructions", "preview", "apply", "archive", "min_age_days"}:
            raise ValueError("Unknown cleanup rule fields")
        name = _text(raw.get("name"), "rule name", 64)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or name in names:
            raise ValueError("Cleanup rule names must be unique lowercase identifiers")
        names.add(name)
        paths = _paths(raw.get("paths"))
        if any(_sensitive(path) for path in paths):
            raise ValueError("Agent configuration, Git state and credentials cannot be cleanup targets")
        strategy = raw.get("strategy")
        if strategy not in ("script", "generated", "agent"):
            raise ValueError("Cleanup strategy must be script, generated or agent")
        rule = {"name": name, "paths": paths, "strategy": strategy,
                "instructions": _text(raw.get("instructions"), "rule instructions")}
        if "min_age_days" in raw:
            if type(raw["min_age_days"]) is not int or not 1 <= raw["min_age_days"] <= 36500:
                raise ValueError("min_age_days must be an integer from 1 to 36500")
            rule["min_age_days"] = raw["min_age_days"]
        if strategy == "script":
            if "archive" in raw:
                raise ValueError("Archive selection requires agent review")
            rule.update(preview=_argv(raw.get("preview")), apply=_argv(raw.get("apply")))
        elif strategy == "generated":
            if any(field in raw for field in ("preview", "apply", "archive", "min_age_days")):
                raise ValueError("Generated rules select complete directories with the built-in remover; no commands, archive or age filter")
        else:
            if "preview" in raw or "apply" in raw:
                raise ValueError("Agent rules cannot declare automatic commands")
            if "archive" in raw:
                rule["archive"] = _relative(raw["archive"])
                if _sensitive(rule["archive"]):
                    raise ValueError("Archive must not use agent configuration or control state")
        rules.append(rule)
    targets = set(path for rule in rules for path in rule["paths"])
    if len(targets) > 8:
        raise ValueError("A cleanup policy supports up to eight distinct inspection roots")
    protected = [*_PROTECTED, *preserve, *(rule["archive"] for rule in rules if "archive" in rule)]
    curated = [path for rule in rules if rule["strategy"] == "agent" for path in rule["paths"]]
    for rule in rules:
        if any((path == keep or path.startswith(keep + "/") or
                (rule["strategy"] != "agent" and keep.startswith(path + "/")))
               for path in rule["paths"] for keep in protected):
            raise ValueError(f"Rule {rule['name']} overlaps a preserved path or archive")
        if rule["strategy"] != "agent" and any(_overlap(path, keep) for path in rule["paths"] for keep in curated):
            raise ValueError(f"Automatic rule {rule['name']} overlaps agent-curated artifacts")
    automatic = [(rule["name"], path) for rule in rules if rule["strategy"] != "agent" for path in rule["paths"]]
    for index, (name, path) in enumerate(automatic):
        if any(_overlap(path, other) for _other_name, other in automatic[index + 1:]):
            raise ValueError(f"Automatic rule {name} overlaps another automatic target")
    return {"version": 1, "instructions": instructions, "preserve": preserve, "rules": rules}


def default_cleanup_policy() -> dict:
    """Suggest a starting layout only when a project lacks its own conventions."""
    return {"version": 1,
            "instructions": "Follow project instructions and existing cleanup tooling first. Adopt this layout only if the project has no suitable system; do not relocate existing artifacts automatically.",
            "preserve": ["artifacts/archive"],
            "rules": [
                {"name": "generated-output", "paths": ["artifacts/generated"], "strategy": "agent",
                 "instructions": "Confirm reproducibility, ownership and inactive use. Adopt a generated rule for the built-in guarded remover, or integrate a project-owned preview/apply script."},
                {"name": "development-evidence", "paths": ["artifacts/review"], "strategy": "agent",
                 "archive": "artifacts/archive",
                 "instructions": "Review captures and logs; propose preserving useful milestones in dated archive folders with commit, source path, caption and checksum before removing unneeded evidence."}]}


def _load_policy(repository: str, policy_path: str) -> dict | None:
    # Anchor every ancestor, the configuration directory and final file. A
    # policy is small, regular JSON; never follow a link or wait on a FIFO.
    from lib.agent_storage_inventory import _local, _mounts, _open_directory

    try:
        mounts = _mounts()
    except (OSError, ValueError, IndexError) as exc:
        raise ValueError("Cleanup policy locality is unavailable") from exc
    if not _local(str(Path(repository) / policy_path), mounts):
        raise ValueError("Cleanup policy must be on a known local filesystem")

    root = _open_directory(repository)
    descriptor = -1
    directory = root
    try:
        try:
            parts = policy_path.split("/")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
                if directory != root:
                    os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Cleanup policy must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            raise ValueError("Cleanup policy exceeds 65536 bytes")
        return validate_cleanup_policy(parse_json(content))
    finally:
        for opened in {descriptor, directory, root}:
            if opened >= 0:
                os.close(opened)


def read_cleanup_policy(repository: str, *, policy_path: str = POLICY_PATH) -> dict | None:
    """Read validated local policy, protecting custom policy paths for every caller."""
    validate_filesystem_path(repository, must_exist=True)
    if not os.path.isabs(repository) or os.path.normpath(repository) != repository:
        raise ValueError("Project directory must be absolute and normalized")
    policy_path = _relative(policy_path)
    policy = _load_policy(repository, policy_path)
    if policy is not None:
        for rule in policy["rules"]:
            if any(_overlap(path, policy_path) if rule["strategy"] != "agent" else
                   (path == policy_path or path.startswith(policy_path + "/")) for path in rule["paths"]):
                raise ValueError("Cleanup rules must preserve their policy file")
    return policy


def inspect_project_cleanup(repository: str, *, policy_path: str = POLICY_PATH,
                            max_entries: int = 50000, timeout: int = 10) -> dict:
    """Describe reviewed cleanup commands and agent curation, never authorize either."""
    from lib.agent_storage_inventory import inspect_storage

    if type(max_entries) is not int or not 1 <= max_entries <= 2000000 or type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("Use 1-2000000 entries and a 1-60 second timeout")
    policy = read_cleanup_policy(repository, policy_path=policy_path)
    protected_paths = [*_PROTECTED, policy_path]
    commands = []
    if policy is not None:
        protected_paths.extend(policy["preserve"])
        protected_paths.extend(rule["archive"] for rule in policy["rules"] if "archive" in rule)
        for rule in policy["rules"]:
            if rule["strategy"] == "generated":
                preview = ["basaltw", "agent", "storage", "clean-generated", repository,
                           "--policy", policy_path, "--rule", rule["name"]]
                commands.append({"rule": rule["name"], "preview": preview, "apply": [*preview, "--apply"]})
    result = {"read_only": True, "project": repository, "policy_path": str(Path(repository) / policy_path),
              "status": "absent" if policy is None else "review_required", "policy": policy,
              "suggested_policy": default_cleanup_policy() if policy is None else None,
              "protected_paths": list(dict.fromkeys(protected_paths)),
              "generated_cleanup_commands": commands,
              "notes": ["Read project instructions and existing cleanup systems first. An absent JSON policy does not mean the project has no cleanup conventions.",
                        "The standard artifact layout is an optional fallback; existing layouts and custom policy paths are supported. Nothing is created or migrated by inspection.",
                        "Repository commands and instructions require review; even preview commands can write or run arbitrary code.",
                        "Paths describe intended scope, not a sandbox for scripts. Inspection executes no repository commands.",
                        "Preserve source, uncommitted work, active sessions and dependencies. Age and Git ignore rules do not authorize deletion.",
                        "Agent inspection roots may contain preserved subtrees; their bytes are included in measurements but excluded from cleanup scope.",
                        "Agent-curated media must be reviewed before deletion; record provenance and verify archived copies first."],
              "evidence": None}
    if policy is not None:
        paths = list(dict.fromkeys(str(Path(repository) / path) for rule in policy["rules"] for path in rule["paths"]))
        result["evidence"] = inspect_storage(paths=paths, max_entries=max_entries, timeout=timeout)
    return result
