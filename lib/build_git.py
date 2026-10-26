"""Restore version metadata inside an already supervised unprivileged build."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

from lib.validation import validate_no_control_characters


_MAX_OUTPUT = 2 * 1024**2


def _git(arguments: list[str], workspace: str, environment: dict[str, str],
         *, input_data: bytes | None = None) -> bytes:
    # The systemd cgroup owns time, memory, descendants and scratch capacity.
    # Spooling keeps ref output out of the helper's unbounded pipe buffers.
    with tempfile.TemporaryFile(dir=environment["TMPDIR"]) as output:
        result = subprocess.run([
            "/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "core.attributesFile=/dev/null", "-c", "init.templateDir=/dev/null", *arguments,
        ], cwd=workspace, env=environment, input=input_data,
            stdin=subprocess.DEVNULL if input_data is None else None,
            stdout=output, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0 or output.tell() > _MAX_OUTPUT:
            raise ValueError("Build history preparation failed")
        output.seek(0)
        return output.read(_MAX_OUTPUT + 1)


def prepare_history(bundle: str, revision: str, workspace: str, home: str) -> None:
    """Unbundle without network fetches, hooks, or checkout of historical blobs."""
    if os.getuid() == 0 or os.getgid() == 0 or os.getgroups():
        raise RuntimeError("Build history must be prepared under the isolated build identity")
    if not isinstance(revision, str) or re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision) is None:
        raise ValueError("Build history revision is invalid")
    if os.path.lexists(os.path.join(workspace, ".git")):
        raise ValueError("Build workspace already contains Git metadata")
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8", "TMPDIR": str(Path(home) / "tmp"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ATTR_NOSYSTEM": "1",
    }
    algorithm = "sha256" if len(revision) == 64 else "sha1"
    _git(["init", "--quiet", "--object-format=" + algorithm], workspace, environment)
    # Fetch applies full-object connectivity checks to a blob-filtered bundle.
    # Unbundle imports only local bytes; reset later populates the index without
    # checking out absent historical blobs or contacting a remote.
    raw = _git(["bundle", "unbundle", bundle], workspace, environment)
    if not raw.endswith(b"\n"):
        raise ValueError("Build history returned incomplete references")
    references: dict[str, str] = {}
    for line in raw[:-1].split(b"\n"):
        try:
            object_id, reference = line.decode("utf-8").split(" ", 1)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Build history returned invalid references") from exc
        if re.fullmatch("[a-f0-9]{" + str(len(revision)) + "}", object_id) is None:
            raise ValueError("Build history object identity is invalid")
        validate_no_control_characters(reference, "Build history reference")
        if (len(reference) > 4096 or any(char.isspace() for char in reference) or reference in references
                or (reference != "HEAD" and not reference.startswith("refs/tags/"))):
            raise ValueError("Build history contains unsupported references")
        references[reference] = object_id
    if references.get("HEAD") != revision:
        raise ValueError("Build history does not match the admitted revision")
    updates = ["create refs/heads/source " + revision]
    updates.extend("create " + name + " " + value for name, value in references.items() if name != "HEAD")
    _git(["update-ref", "--stdin"], workspace, environment,
         input_data=("\n".join(updates) + "\n").encode("utf-8"))
    _git(["symbolic-ref", "HEAD", "refs/heads/source"], workspace, environment)
    _git(["reset", "--mixed", "--quiet", "HEAD"], workspace, environment)
    if _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], workspace, environment):
        raise ValueError("Restored build source does not match the admitted revision")
