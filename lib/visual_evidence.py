"""Validate project runtime observations and build receipts before visual review."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from lib.atomic_io import read_json_file
from lib.validation import validate_filesystem_path


def verify_receipt(path: str, repository: str, revision: str, settings: dict,
                   required_kinds: list[str]) -> dict:
    """Check a trusted harness's claims against requested values and current bytes.

    Build origin and which resources the renderer loaded remain project claims;
    a hash cannot reconstruct compilation history or observe process memory.
    """
    receipt = read_json_file(path, max_bytes=1024 * 1024)
    if not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != 1:
        raise ValueError("Visual receipt requires version: 1")
    json.dumps(receipt, allow_nan=False)
    if not revision or receipt.get("revision") != revision:
        raise ValueError("Visual receipt revision differs from the intended revision")
    effective = receipt.get("effective_settings")
    if not isinstance(effective, dict):
        raise ValueError("Visual receipt requires effective_settings from the renderer")
    for key, value in settings.items():
        if key not in effective or json.dumps(effective[key], sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ValueError(f"Requested setting did not take effect: {key}")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 4096:
        raise ValueError("Visual receipt requires 1–4096 build artifacts")
    root, seen, kinds, total = Path(repository).resolve(), set(), set(), 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("revision") != revision:
            raise ValueError("Artifact build revision differs from the intended revision")
        name, kind, digest = artifact.get("path"), artifact.get("kind"), artifact.get("sha256")
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", kind):
            raise ValueError("Artifact kind must be a short lowercase identifier")
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Artifact path must be repository-relative without parent traversal")
        validate_filesystem_path(name)
        source = root / name
        resolved = source.resolve()
        if not resolved.is_relative_to(root) or resolved in seen:
            raise ValueError("Artifact paths must be unique and remain below the repository")
        seen.add(resolved)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Artifact requires a lowercase SHA-256 build digest")
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            total += info.st_size
            if not stat.S_ISREG(info.st_mode) or info.st_size > 512 * 1024 * 1024 or total > 2 * 1024**3:
                raise ValueError("Artifact must be regular, at most 512 MiB; total at most 2 GiB")
            hasher, count = hashlib.sha256(), 0
            while chunk := file.read(1024 * 1024):
                count += len(chunk)
                if count > info.st_size:
                    raise ValueError("Artifact grew during verification")
                hasher.update(chunk)
            if count != info.st_size or hasher.hexdigest() != digest:
                raise ValueError(f"Artifact differs from its build receipt: {name}")
        kinds.add(kind)
    if missing := set(required_kinds) - kinds:
        raise ValueError("Missing required artifact kinds: " + ", ".join(sorted(missing)))
    return {"status": "verified", "receipt": path, "revision": revision,
            "effective_settings": effective, "artifacts": artifacts,
            "scope": "project-attested build origin and runtime settings; artifact bytes checked"}
