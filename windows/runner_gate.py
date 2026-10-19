"""Use the Debian identity policy for a native Windows runner through WSL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.atomic_io import read_json_file
from lib.github_runner_config import MAX_OUTCOMES, validate_policy
from lib.github_runner_gate import evaluate_job
from lib.validation import validate_filesystem_path


def write_outcomes(path: str, outcomes: list) -> None:
    """Atomically replace telemetry on DrvFS, leaving Windows ACLs intact."""
    validate_filesystem_path(path)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(path).parent,
                                         delete=False) as stream:
            temporary = stream.name
            json.dump(outcomes, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--event")
    parser.add_argument("--context")
    parser.add_argument("--outcomes")
    args = parser.parse_args(argv)
    try:
        policy = validate_policy(read_json_file(args.policy, max_bytes=16 * 1024))
        if args.validate:
            return 0
        if policy.get("enabled", True) is not True or policy["registration"] != "registered":
            raise ValueError("Runner is disabled or pending")
        context = read_json_file(args.context, max_bytes=16 * 1024)
        if not isinstance(context, dict) or any(not isinstance(v, str) for v in context.values()):
            raise ValueError("Invalid context")
        event = read_json_file(args.event, max_bytes=2 * 1024 * 1024)
        allowed, reason, identity = evaluate_job(policy, event, context)
        try:
            previous = read_json_file(args.outcomes, max_bytes=64 * 1024)
        except FileNotFoundError:
            previous = []
        if not isinstance(previous, list) or len(previous) > MAX_OUTCOMES:
            raise ValueError("Invalid outcome history")
        from datetime import datetime, timezone

        previous.append(dict(time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             allowed=allowed, reason=reason, identity=identity))
        # DrvFS owns Windows ACLs; chmod/chown are deliberately not applied here.
        # The containing directory is private to the Windows owner.
        write_outcomes(args.outcomes, previous[-MAX_OUTCOMES:])
        print("GitHub runner gate: " + reason)
        return 0 if allowed else 1
    except (OSError, ValueError, TypeError):
        print("GitHub runner gate: configuration, event or telemetry unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
