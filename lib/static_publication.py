"""Private static-publication receipts and read-only artifact verification."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


RECEIPT_FILE = ".basaltwater-receipt.json"
RESERVED_FILES = {RECEIPT_FILE, ".basaltwater.json"}
MAX_ARTIFACTS = 10000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def source_state(project: str) -> dict[str, object]:
    """Observe Git metadata without recording file names or patch contents."""
    try:
        head = subprocess.run(
            ["git", "-C", project, "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if head.returncode or not re.fullmatch(r"[0-9a-f]{40,64}", head.stdout.strip()):
            return {"commit": None, "dirty": None}
        status = subprocess.run(
            ["git", "-C", project, "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        return {"commit": head.stdout.strip(), "dirty": bool(status.stdout) if not status.returncode else None}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def _hash_file(path: str) -> dict[str, object]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Publication artifacts must be regular files")
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def artifact_hashes(root: str) -> dict[str, object]:
    """Hash a complete artifact inventory, refusing links and incomplete scans."""
    validate_filesystem_path(root, must_exist=True)
    if os.path.islink(root) or not os.path.isdir(root):
        raise ValueError("Artifact root must be a real directory")
    result: dict[str, object] = {}

    def failed(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(root, onerror=failed, followlinks=False):
        if any(os.path.islink(os.path.join(current, name)) for name in directories):
            raise ValueError("Publication artifacts contain a symlink")
        for name in sorted(files):
            if current == root and name in RESERVED_FILES:
                continue
            relative = os.path.relpath(os.path.join(current, name), root)
            validate_filesystem_path(relative)
            result[relative] = _hash_file(os.path.join(current, name))
            if len(result) > MAX_ARTIFACTS:
                raise ValueError(f"Publication exceeds the {MAX_ARTIFACTS}-artifact receipt limit")
    return dict(sorted(result.items()))


def write_receipt(root: str, project: str, *, output: str, published_at: str, built: bool) -> None:
    """Stage the receipt with its snapshot so both activate or roll back together."""
    receipt = {
        "version": 1, "source": source_state(project), "output": output,
        "published_at": published_at, "build_performed": built,
        "artifacts": artifact_hashes(root),
    }
    if len((json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ValueError("Publication receipt exceeds its size limit")
    write_json_atomic(os.path.join(root, RECEIPT_FILE), receipt, mode=0o600, sort_keys=True)


def read_receipt(root: str) -> dict[str, object]:
    path = os.path.join(root, RECEIPT_FILE)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError("Publication receipt must be a private owner-held regular file")
    value = read_json_file(path, max_bytes=MAX_RECEIPT_BYTES)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported publication receipt")
    artifacts, source = value.get("artifacts"), value.get("source")
    if not isinstance(artifacts, dict) or not 1 <= len(artifacts) <= MAX_ARTIFACTS or "index.html" not in artifacts:
        raise ValueError("Invalid publication artifact inventory")
    if (not isinstance(source, dict) or "commit" not in source or "dirty" not in source
            or source["dirty"] is not None and type(source["dirty"]) is not bool):
        raise ValueError("Invalid publication source state")
    commit = source.get("commit")
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit)):
        raise ValueError("Invalid publication source commit")
    for path, record in artifacts.items():
        validate_filesystem_path(path)
        if os.path.isabs(path) or os.path.normpath(path) != path or path in {".", ".."} or path.startswith("../") or path in RESERVED_FILES:
            raise ValueError("Unsafe publication artifact path")
        if not isinstance(record, dict) or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
            raise ValueError("Invalid publication artifact digest")
        if type(record.get("size")) is not int or not 0 <= record["size"] < 2**63:
            raise ValueError("Invalid publication artifact size")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        raise urllib.error.URLError("Publication verification refuses redirects")


def _served_hash(url: str, size: int, deadline: float) -> dict[str, object]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Publication verification deadline reached")
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"})
    with opener.open(request, timeout=min(15, remaining)) as response:
        if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError("Publication did not return an unencoded HTTP 200 artifact")
        digest, received = hashlib.sha256(), 0
        while chunk := response.read(min(65536, size + 1 - received)):
            received += len(chunk)
            digest.update(chunk)
            if received > size:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Publication verification deadline reached")
    return {"sha256": digest.hexdigest(), "size": received}


def verify_receipt(root: str, *, url: str | None, local_output: str | None = None,
                   project: str | None = None, timeout: int = 60) -> dict[str, object]:
    """Compare stored, local, and served bytes; never rebuild or republish."""
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("Verification timeout must be from 1 through 300 seconds")
    if url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not validate_host(parsed.hostname or "") or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("Verification requires a credential-free HTTPS publication URL")
        _ = parsed.port
    receipt = read_receipt(root)
    expected = receipt["artifacts"]
    issues, warnings = [], []
    for component, output in (("publication", root), ("local_build", local_output)):
        if output is not None and artifact_hashes(output) != expected:
            issues.append({"issue": "artifact_mismatch", "component": component,
                           "remediation": "Rebuild the intended source and publish the final output again."})
    source_matches = None
    if project is not None:
        current = source_state(project)
        if current["commit"] is None or receipt["source"]["commit"] is None:
            warnings.append("source_revision_unavailable")
        elif current["commit"] != receipt["source"]["commit"]:
            source_matches = False
            issues.append({"issue": "source_revision_mismatch", "component": "source",
                           "remediation": "Build and publish the intended source revision."})
        elif current["dirty"] is not False or receipt["source"]["dirty"] is not False:
            warnings.append("dirty_source_provenance_unverified")
        else:
            source_matches = True
    deadline = time.monotonic() + timeout
    served = 0
    if url:
        for path, record in expected.items():
            try:
                actual = _served_hash(url.rstrip("/") + "/" + urllib.parse.quote(path, safe="/"), record["size"], deadline)
                if actual != record:
                    issues.append({"issue": "artifact_mismatch", "component": "served", "path": path,
                                   "remediation": "Check the route and publish the intended snapshot again."})
                served += 1
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
                issues.append({"issue": "served_artifact_unavailable", "component": "served", "path": path,
                               "error": str(exc), "remediation": "Check HTTPS, routing and VM CA trust; preserve TLS verification."})
                break
    return {"ok": not issues, "issue": issues[0]["issue"] if issues else None,
            "remediation": issues[0]["remediation"] if issues else None,
            "issues": issues, "warnings": warnings, "source": receipt["source"],
            "source_matches": source_matches, "build_performed": receipt.get("build_performed"),
            "artifacts": len(expected), "served_artifacts_checked": served,
            "network_origin": "vm" if url else None, "rendering_verified": False}
