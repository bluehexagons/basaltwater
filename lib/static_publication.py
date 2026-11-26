"""Private static-publication receipts and read-only artifact verification."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import http.client
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from lib.atomic_io import read_json_file, write_json_atomic
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path, validate_network_ip
from lib.validators import validate_host


RECEIPT_FILE = ".basaltwater-receipt.json"
GODOT_PROFILE_FILE = ".basaltwater-godot"
RESERVED_FILES = {RECEIPT_FILE, ".basaltwater.json", GODOT_PROFILE_FILE}
MAX_ARTIFACTS = 10000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


class ReceiptLimitError(ValueError):
    """The artifact inventory exceeds the supported receipt limits."""


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
                raise ReceiptLimitError(f"Publication exceeds the {MAX_ARTIFACTS}-artifact receipt limit")
    return dict(sorted(result.items()))


def write_receipt(
    root: str, project: str | None, *, output: str | None, published_at: str, built: bool,
    source_before_build: dict[str, object] | None = None,
) -> dict[str, object]:
    """Stage the receipt with its snapshot so both activate or roll back together."""
    source = source_state(project) if project is not None else {"commit": None, "dirty": None}
    if source_before_build is not None:
        if source_before_build["commit"] != source["commit"]:
            source = {"commit": None, "dirty": None}
        elif source_before_build["dirty"] is True:
            source = {**source, "dirty": True}
        elif source_before_build["dirty"] is None and source["dirty"] is False:
            source = {**source, "dirty": None}
    receipt = {
        "version": 1,
        "source": source,
        "output": output,
        "published_at": published_at, "build_performed": built,
        "artifacts": artifact_hashes(root),
    }
    if len((json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ReceiptLimitError("Publication receipt exceeds its size limit")
    write_json_atomic(os.path.join(root, RECEIPT_FILE), receipt, mode=0o600, sort_keys=True)
    return receipt


def read_receipt(root: str) -> dict[str, object]:
    path = os.path.join(root, RECEIPT_FILE)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError("Publication receipt must be a private owner-held regular file")
    value = read_json_file(path, max_bytes=MAX_RECEIPT_BYTES)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported publication receipt")
    output = value.get("output")
    if output is not None:
        if (
            not isinstance(output, str) or os.path.isabs(output)
            or os.path.normpath(output) != output or output == ".." or output.startswith("../")
        ):
            raise ValueError("Unsafe publication output path")
        validate_filesystem_path(output)
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


def _served_hash(url: str, size: int, deadline: float, *, opener: urllib.request.OpenerDirector) -> dict[str, object]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Publication verification deadline reached")
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


def _served_hashes(url: str, artifacts: list[tuple[str, int]], deadline: float) -> Iterator[dict[str, object]]:
    try:
        opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        for path, size in artifacts:
            artifact_url = url.rstrip("/") + "/" + urllib.parse.quote(path, safe="/")
            yield _served_hash(artifact_url, size, deadline, opener=opener)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        yield {"error": str(exc)}


def _verify_served(url: str, expected: dict[str, dict], timeout: int) -> tuple[int, list[dict]]:
    artifacts = list(expected.items())
    served, issues = 0, []
    error = "HTTPS verification worker exited before reporting every artifact"

    def collect(line: str) -> None:
        nonlocal served, error
        actual = json.loads(line)
        if "error" in actual:
            error = actual["error"]
            return
        path, record = artifacts[served]
        if actual != record:
            issues.append({"issue": "artifact_mismatch", "component": "served", "path": path,
                           "remediation": "Check the route and publish the intended snapshot again."})
        served += 1

    # Socket timeouts bound inactivity. The supervisor also bounds DNS, TLS,
    # headers and body reads that can stay active past the verification deadline.
    request = {"url": url, "artifacts": [(path, record["size"]) for path, record in artifacts],
               "deadline": time.monotonic() + timeout}
    source_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    try:
        run_streamed(
            [sys.executable, "-P", "-m", "lib.static_publication"], timeout=timeout,
            input_data=json.dumps(request).encode(), on_output=collect, discard_stderr=True,
            env={**os.environ, "PYTHONPATH": source_root},
        )
    except TimeoutError:
        error = "Publication verification deadline reached"
    except (OSError, ValueError) as exc:
        error = str(exc)
    if served < len(artifacts):
        issues.append({"issue": "served_artifact_unavailable", "component": "served", "path": artifacts[served][0],
                       "error": error, "remediation": "Check HTTPS, routing and VM CA trust; preserve TLS verification."})
    return served, issues


def verify_receipt(root: str, *, url: str | None, local_output: str | None = None,
                   project: str | None = None, timeout: int = 60,
                   generated_gzip: tuple[str, ...] = (),
                   receipt: dict[str, object] | None = None) -> dict[str, object]:
    """Compare stored, local, and served bytes; never rebuild or republish."""
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError("Verification timeout must be from 1 through 300 seconds")
    if url:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https" or not host or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
        ):
            raise ValueError("Verification requires a credential-free HTTPS publication URL")
        if not validate_host(host):
            validate_network_ip(host, "publication host")
        _ = parsed.port
    if receipt is None:
        receipt = read_receipt(root)
    expected = receipt["artifacts"]
    # Compare the project's own files against the published export. Generated
    # gzip siblings remain part of both the on-disk and served verification.
    generated = {
        path for path in generated_gzip
        if path.endswith((".wasm.gz", ".pck.gz")) and path in expected and path[:-3] in expected
    }
    issues, warnings = [], []
    for component, output, ignored in (("publication", root, set()), ("local_build", local_output, generated)):
        if output is None:
            continue
        actual = artifact_hashes(output)
        if {path: record for path, record in actual.items() if path not in ignored} != {
            path: record for path, record in expected.items() if path not in ignored
        }:
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
    served = 0
    # Nginx rejects dot-prefixed path segments. Their hashes still belong to the
    # complete publication and local build inventories above.
    hidden = {path for path in expected if any(part.startswith(".") for part in path.split("/"))}
    if url:
        served, served_issues = _verify_served(url, {path: record for path, record in expected.items() if path not in hidden}, timeout)
        issues.extend(served_issues)
    return {"ok": not issues, "issue": issues[0]["issue"] if issues else None,
            "remediation": issues[0]["remediation"] if issues else None,
            "issues": issues, "warnings": warnings, "source": receipt["source"],
            "source_matches": source_matches, "build_performed": receipt.get("build_performed"),
            "artifacts": len(expected), "hidden_artifacts": len(hidden), "served_artifacts_checked": served,
            "network_origin": "vm" if url else None, "rendering_verified": False}


if __name__ == "__main__":
    for result in _served_hashes(**json.load(sys.stdin)):
        print(json.dumps(result), flush=True)
