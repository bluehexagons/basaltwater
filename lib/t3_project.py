"""Check the committed files that T3 Code uses to create a sparse worktree."""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import tempfile

from lib.validation import validate_filesystem_path, validate_no_control_characters


def _read_project_config(contents: bytes) -> object:
    # T3 accepts comments and trailing commas and preserves them inside strings.
    quoted = r'("(?:[^"\\]|\\.)*")'
    uncommented = re.sub(
        quoted + r"|//[^\r\n]*|/\*[\s\S]*?\*/", lambda match: match[1] or " ", contents.decode("utf-8"),
    )
    return json.loads(re.sub(quoted + r"|,(\s*[}\]])", lambda match: match[1] or match[2], uncommented))


def _tree_path(value: str) -> str:
    validate_filesystem_path(value)
    while value.startswith("./"):
        value = value[2:]
    if (
        any(part in ("", ".", "..") for part in value.split("/"))
        or "\\" in value or PureWindowsPath(value).drive
    ):
        raise ValueError(f"Expected a repository-relative file path: {value!r}")
    return value


def check_t3_worktree(
    repository: str, *, ref: str = "HEAD", required_files: list[str],
) -> dict[str, object]:
    validate_filesystem_path(repository, must_exist=True)
    validate_no_control_characters(ref, "Git revision")
    required = list(dict.fromkeys(_tree_path(path) for path in required_files))

    def git(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["git", "--literal-pathspecs", "-C", repository, *args],
                input=input_bytes, capture_output=True, timeout=30,
                env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Git {args[0]} timed out") from exc

    revision = "@{-1}" if ref == "-" else ref
    resolved = git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    if resolved.returncode:
        raise ValueError(f"Cannot resolve Git revision {ref!r} to a commit in {repository!r}")
    commit = resolved.stdout.decode("ascii").strip()

    def file_oid(path: str) -> str | None:
        entry = git("ls-tree", "--full-tree", "-z", commit, "--", path)
        if entry.returncode:
            raise ValueError(f"Git could not list {path!r} at {ref!r}")
        if not entry.stdout:
            return None
        metadata, _, _ = entry.stdout.partition(b"\t")
        mode, _, oid = metadata.split()
        if mode not in (b"100644", b"100755"):
            raise ValueError(f"{path!r} at {ref!r} must be a tracked regular file")
        return oid.decode("ascii")

    def read_blob(oid: str, path: str) -> bytes:
        blob = git("cat-file", "blob", oid)
        if blob.returncode:
            raise ValueError(f"Git could not read {path!r} at {ref!r}")
        return blob.stdout

    patterns_path = None
    patterns = None
    config_oid = file_oid("t3.json")
    if config_oid is not None:
        try:
            config = _read_project_config(read_blob(config_oid, "t3.json"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"t3.json at {ref!r} must be UTF-8 JSON") from exc
        if not isinstance(config, dict):
            raise ValueError(f"t3.json at {ref!r} must be a JSON object")
        if "worktreeLayout" in config:
            raise ValueError(
                f"t3.json at {ref!r} declares worktreeLayout. "
                "Verify the custom layout and its setup inputs in a disposable T3 worktree."
            )
        if "worktreeSparsePatternsPath" in config:
            value = config["worktreeSparsePatternsPath"]
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError("worktreeSparsePatternsPath must be a non-empty path of at most 512 characters")
            patterns_path = _tree_path(value.strip())
            patterns_oid = file_oid(patterns_path)
            if patterns_oid is None:
                raise ValueError(f"Sparse patterns file {patterns_path!r} is missing at {ref!r}")
            patterns = read_blob(patterns_oid, patterns_path)

    missing = [path for path in required if file_oid(path) is None]
    if missing:
        raise ValueError(f"Required files missing at {ref!r}: {', '.join(missing)}")
    if patterns is not None and required:
        with tempfile.TemporaryDirectory(prefix="basaltwater-t3-sparse-") as temporary:
            rules = Path(temporary) / "sparse-checkout"
            rules.write_bytes(patterns)
            included = git(
                "sparse-checkout", "check-rules", "--no-cone", "-z", "--rules-file", str(rules),
                input_bytes=b"".join(os.fsencode(path) + b"\0" for path in required),
            )
        if included.returncode:
            detail = included.stderr.decode(errors="replace").strip()
            raise ValueError(f"Git sparse-checkout check-rules failed (requires Git with check-rules): {detail}")
        kept = set(included.stdout.split(b"\0"))
        excluded = [path for path in required if os.fsencode(path) not in kept]
        if excluded:
            raise ValueError(f"Sparse patterns exclude required files at {ref!r}: {', '.join(excluded)}")
    return {
        "status": "sparse" if patterns_path is not None else "full",
        "commit": commit,
        "patterns_path": patterns_path,
        "required_files": required,
    }
