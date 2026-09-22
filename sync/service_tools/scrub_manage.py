#!/usr/bin/env python3
"""Target-side inspection and recovery under the storage operations lock."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from lib.machine_state import load_setup_config
from lib.runtime_config import RuntimeConfig
from lib.scrub_cli import ACTIONS, MUTATIONS, add_target_arguments
from lib.validation import validate_filesystem_path, validate_scrub_specs
from sync.service_tools.scrub_findings import Findings, examine, file_identity, remediate
from sync.service_tools.scrub_par2 import _confined_path
from sync.service_tools.parity_sets import locate
from sync.service_tools.storage_ops import LOCK_FILE, OperationLock, resolve_scrub_database_path, validate_mounts_for_operation


def selected_jobs(config: RuntimeConfig, args: argparse.Namespace) -> list[tuple[str, str, int]]:
    validate_scrub_specs(config.scrub_specs)
    for field in ("file", "directory", "database", "backup"):
        value = getattr(args, field, None)
        if value:
            validate_filesystem_path(value)
            if not os.path.isabs(value) or ".." in Path(value).parts:
                raise ValueError(f"{field} must be an absolute path without traversal")
    jobs = []
    for directory, database, redundancy, _interval in config.scrub_specs:
        directory = os.path.normpath(directory)
        database = resolve_scrub_database_path(directory, database)
        if args.directory and os.path.normpath(args.directory) != directory:
            continue
        if args.database and os.path.normpath(args.database) != database:
            continue
        path = getattr(args, "file", None)
        if path and (not Path(path).is_relative_to(directory) or Path(path).is_relative_to(database) or path == directory):
            continue
        valid, error = validate_mounts_for_operation([directory, database], config, "scrub management")
        if not valid:
            raise ValueError(error)
        _confined_path(directory, directory)
        _confined_path(database, database)
        if path:
            _confined_path(path, directory)
        jobs.append((directory, database, int(redundancy.rstrip("%"))))
    if not jobs:
        raise ValueError("No configured scrub job matches; rerun setup with --scrub first")
    if args.action != "status" and len(jobs) != 1:
        raise ValueError("File matches multiple scrub jobs; select --directory and --database")
    return jobs


def execute(args: argparse.Namespace, config: RuntimeConfig) -> tuple[dict, int]:
    jobs = selected_jobs(config, args)
    if args.action == "status":
        reports = []
        for directory, database, _ in jobs:
            report = Findings(directory, database)
            reports.append({"directory": directory, "database": database,
                            "recorded": os.path.exists(report.path),
                            "last_full_scan": report.data.get("last_full_scan"),
                            "last_parity_update": report.data.get("last_parity_update"),
                            "files": {path: entry for path, entry in report.data["files"].items()
                                      if args.all or entry["state"] == "open"}})
        return {"jobs": reports}, 0
    directory, database, redundancy = jobs[0]
    path = os.path.normpath(args.file)
    relative = os.path.relpath(path, directory)
    report = Findings(directory, database)
    if args.action == "inspect":
        return {"file": path, "directory": directory, "database": database,
                "file_identity": file_identity(path),
                "parity": locate(path, directory, database)[1],
                "finding": report.data["files"].get(relative)}, 0
    if args.action in MUTATIONS:
        if not args.yes:
            raise ValueError("Single-file remediation requires explicit --yes confirmation")
        backup = getattr(args, "backup", None)
        if backup:
            valid, error = validate_mounts_for_operation([backup], config, "scrub restore")
            if not valid:
                raise ValueError(error)
        result = remediate(path, directory, database, redundancy, args.action, backup=backup)
    else:
        result = examine(path, directory, database)
        report.record(relative, result)
    return {"file": path, **result}, 0 if result["category"] == "healthy" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ACTIONS:
        add_target_arguments(actions.add_parser(action), action)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError("Scrub management requires root on the target host")
        config = RuntimeConfig.from_dict(load_setup_config() or {})
        with OperationLock(LOCK_FILE) as lock:
            if not lock.acquire():
                raise ValueError("Storage operations are active; retry after the current job finishes")
            with redirect_stdout(sys.stderr):
                result, code = execute(args, config)
        if args.json:
            print(json.dumps(result, indent=2))
        elif args.action == "status":
            for job in result["jobs"]:
                print(f"{job['directory']} (parity: {job['database']})")
                if job["last_full_scan"]:
                    print(f"  Last full scan: {job['last_full_scan']['finished_at']}; completed: {job['last_full_scan']['completed']}")
                if not job["recorded"]:
                    print("  No findings report yet; run a scheduled scrub or targeted verify.")
                elif not job["files"]:
                    print("  No unresolved findings recorded.")
                for path, finding in job["files"].items():
                    print(f"  {finding['state']}: {finding['category']}: {path}")
                    print(f"    First: {finding.get('first_seen', '?')}; last checked: {finding.get('last_checked', '?')}")
                    print(f"    {finding.get('advice', '')}")
        else:
            print(json.dumps(result, indent=2))
        return code
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
