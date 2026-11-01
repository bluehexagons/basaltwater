#!/usr/bin/env python3
"""Draft managed build enrollments from pinned HTTPS manifests; enroll nothing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any

# Support direct invocation from any working directory without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.build_host_steps import parse_build_host_enrollment
from common.build_target_steps import parse_build_target_enrollment
from lib.build_enrollment import DEFAULT_POLICY_LIMITS, generate_enrollments
from lib.build_selection import _public_companion
from lib.build_setup import _private_directory
from lib.build_setup_sources import prepare_setup_sources
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path
from lib.workspace import get_workspace_dir


BUILDER_FILENAME = "builder-enrollment.json"
TARGET_FILENAME = "target-enrollment.json"


def write_drafts(output_dir: str, builder: dict[str, Any], target: dict[str, Any]) -> None:
    """Exclusively create both validated drafts under an owned mode-0700 directory."""
    builder = parse_build_host_enrollment(builder)
    target = parse_build_target_enrollment(target)
    documents = [(BUILDER_FILENAME, builder), (TARGET_FILENAME, target)]
    payloads = [(name, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode())
                for name, document in documents]
    validate_filesystem_path(output_dir)
    with directory_fd(os.path.abspath(output_dir), create=True, mode=0o700) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Output directory must be caller-owned mode 0700")
        created: list[tuple[str, int]] = []
        try:
            # Reserve both names before writing either; an existing name is never replaced.
            for name, _ in payloads:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                created.append((name, descriptor))
                os.fchmod(descriptor, 0o600)
            for (_, descriptor), (_, payload) in zip(created, payloads):
                with os.fdopen(os.dup(descriptor), "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(parent)
        except BaseException:
            for name, _ in created:
                os.unlink(name, dir_fd=parent)
            raise
        finally:
            for _, descriptor in created:
                os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Fetch bounded HTTPS inputs and write drafts for operator review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", nargs=2, action="append", required=True,
                        metavar=("DOMAIN[/PATH]", "HTTPS_URL"))
    parser.add_argument("--builder-key", required=True, help="ssh-ed25519 PUBLIC companion file")
    parser.add_argument("--target-key", required=True, help="ssh-ed25519 PUBLIC companion file")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--principal", default="deploy")
    for name, default in DEFAULT_POLICY_LIMITS.items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=default)
    for role, default_bytes in (("builder", 64 * 1024**3), ("target", 8 * 1024**3)):
        parser.add_argument(f"--{role}-max-bytes", type=int, default=default_bytes)
        parser.add_argument(f"--{role}-max-records", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        for path in (args.builder_key, args.target_key, args.output_dir):
            validate_filesystem_path(path)
        builder_key = _public_companion(os.path.abspath(os.path.expanduser(args.builder_key)))
        target_key = _public_companion(os.path.abspath(os.path.expanduser(args.target_key)))
        workspace = get_workspace_dir()
        _private_directory(workspace)
        with tempfile.TemporaryDirectory(prefix="basaltwater-policy-drafts-", dir=workspace) as root:
            os.chmod(root, 0o700)
            prepared = prepare_setup_sources(args.deploy, root)
            builder, target = generate_enrollments(
                prepared, builder_key=builder_key, target_key=target_key, principal=args.principal,
                limits={name: getattr(args, name) for name in DEFAULT_POLICY_LIMITS},
                builder_max_bytes=args.builder_max_bytes, target_max_bytes=args.target_max_bytes,
                builder_max_records=args.builder_max_records, target_max_records=args.target_max_records,
            )
        output = os.path.abspath(os.path.expanduser(args.output_dir))
        write_drafts(output, builder, target)
    except (OSError, ValueError, PermissionError, TimeoutError) as exc:
        parser.exit(1, f"Draft preparation failed: {exc}\n")
    print(f"Drafts written to {output}; operator review required before separate enrollment.")
    for slug, grant in target["policy"]["applications"].items():
        tools = builder["policy"]["principals"][args.principal]["projects"][slug]["runtimes"]
        print(f"  {slug}: {grant['deployment_root']}; runtimes={','.join(tools) or 'none'}")
        for name, route in grant["components"].items():
            print(f"    {name}: {route['type']} {route['domain']}{route['path']}")
    print("Review keys, complete routes/runtime grants, roots and ceilings in both JSON files.")
    print("Retained reservations consume finite capacity; no automatic cleanup is performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
