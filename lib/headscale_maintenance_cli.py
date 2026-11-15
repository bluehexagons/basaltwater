"""Root local recovery adapter; private inputs and metadata-only output."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import sys


@contextmanager
def _private_output():
    """Suppress Python logging and inherited native stdout/stderr alike."""
    with open(os.devnull, "w") as sink:
        saved = []
        try:
            for descriptor in (1, 2):
                saved.append((descriptor, os.dup(descriptor)))
                os.dup2(sink.fileno(), descriptor)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            for descriptor, original in reversed(saved):
                os.dup2(original, descriptor)
                os.close(original)


def add_headscale_maintenance_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("headscale-maintenance", help="Root local backup and recovery")
    commands = parser.add_subparsers(dest="headscale_maintenance_action", required=True)
    for action in ("backup", "validate", "restore", "upgrade", "unbound-backup",
                   "unbound-validate", "unbound-restore", "unbound-recover-fresh",
                   "unbound-records-inspect", "unbound-records-replace",
                   "adopt-inspect", "adopt"):
        command = commands.add_parser(action)
        command.add_argument("--dry-run", action="store_true")
        if action in {"adopt-inspect", "adopt"}:
            command.add_argument("--configuration", required=True, help="Private explicit version-1 hosting settings")
            command.add_argument("--package", required=True, help="Exact pinned official DEB")
            if action == "adopt-inspect":
                command.add_argument("--destination", required=True, help="New private inspection JSON file")
            else:
                command.add_argument("--review", required=True, help="Private exact inspected approval JSON")
                command.add_argument("--backup", required=True, help="New private snapshot directory")
        elif action == "unbound-records-inspect":
            command.add_argument("--binding", required=True, help="Private exact expected DNSBinding JSON")
        elif action == "unbound-records-replace":
            command.add_argument("--review", required=True, help="Private exact binding/digest/replacement JSON")
        elif action == "unbound-recover-fresh":
            command.add_argument("--review", required=True, help="Private exact operation/digest/identity review JSON")
        elif action in {"backup", "unbound-backup"}:
            command.add_argument("--destination", required=True)
        elif action == "upgrade":
            for name in ("target-package", "recovery-package", "backup", "current-release",
                         "target-release", "release-inventory"):
                command.add_argument("--" + name, required=True)
        else:
            command.add_argument("--source", required=True)
            command.add_argument("--binding" if action.startswith("unbound-") else "--manifest", required=True)
        if action == "restore":
            command.add_argument("--activate", action="store_true")
        if action == "unbound-restore":
            command.add_argument("--reviewed-rebind", help="Private unpublished DNSBinding JSON")
    return parser


def _path(value: str) -> Path:
    from lib.validation import validate_filesystem_path

    validate_filesystem_path(value)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Absolute recovery paths required")
    return path


def _write_inspection(path: Path, value: dict) -> None:
    from lib.setup_directories import directory_fd

    with directory_fd(str(path.parent)) as parent:
        info = os.fstat(parent)
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("Inspection destination requires a root-private directory")
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


def run_headscale_maintenance_command(args: argparse.Namespace) -> int:
    """Dry run validates inputs only; never calls native validation or locks."""
    try:
        from lib import headscale_backup as hs
        from lib import headscale_adoption as adoption
        from lib import unbound_backup as ub
        from lib import unbound_records as ur
        from lib.headscale_dns_binding import parse_dns_binding
        from lib.headscale_server import Release, ServerManifest

        action = args.headscale_maintenance_action
        if action not in {"backup", "validate", "restore", "upgrade", "unbound-backup",
                          "unbound-validate", "unbound-restore", "unbound-recover-fresh",
                          "unbound-records-inspect", "unbound-records-replace",
                          "adopt-inspect", "adopt"}:
            raise ValueError("Unknown maintenance action")
        for name in ("destination", "source", "target_package", "recovery_package", "backup", "package"):
            if hasattr(args, name):
                _path(getattr(args, name))
        expected = None
        if action in {"adopt-inspect", "adopt"}:
            adoption_config = adoption.parse_adoption_settings(ub.private_json(_path(args.configuration)))
            if action == "adopt":
                adoption.validate_adoption_review(adoption_config, ub.private_json(_path(args.review)))
        if action == "unbound-records-replace":
            update = ur.parse_records_update(ub.private_json(_path(args.review)))
        if action == "unbound-recover-fresh":
            review = ub.validate_fresh_recovery_review(ub.private_json(_path(args.review)))
        if action in {"validate", "restore"}:
            expected = ServerManifest(**ub.private_json(_path(args.manifest)))
        if action in {"unbound-validate", "unbound-restore", "unbound-records-inspect"}:
            expected = parse_dns_binding(ub.private_json(_path(args.binding)))
        rebind = None
        if getattr(args, "reviewed_rebind", None):
            rebind = parse_dns_binding(ub.private_json(_path(args.reviewed_rebind)))
            if rebind.advertised or rebind.network_url != expected.network_url:
                raise ValueError("Unpublished same-network rebind required")
        if action == "upgrade":
            current = Release(**ub.private_json(_path(args.current_release)))
            target = Release(**ub.private_json(_path(args.target_release)))
            raw = ub.private_json(_path(args.release_inventory))
            if not isinstance(raw, list) or not 1 <= len(raw) <= 512:
                raise ValueError("Bounded reviewed inventory required")
            inventory = [Release(**item) for item in raw]
            hs.validate_upgrade(current, target, reviewed_releases=inventory)
        if args.dry_run:
            print(json.dumps({"action": action, "dry_run": True, "readiness_claim": False}))
            return 0
        if os.geteuid() != 0:
            raise ValueError("Root required")
        # Existing adapters may log commands/paths. Suppress all downstream
        # output, including native exception diagnostics, at this boundary.
        metadata = {}
        with _private_output():
            if action == "adopt-inspect":
                _write_inspection(_path(args.destination), adoption.inspect_adoption(adoption_config, _path(args.package)))
            elif action == "adopt":
                adoption.adopt_headscale(adoption_config, _path(args.package), _path(args.review), _path(args.backup))
            elif action == "backup":
                hs.backup_headscale(_path(args.destination))
            elif action == "validate":
                hs.validate_backup(_path(args.source), expected=expected)
            elif action == "restore":
                hs.restore_headscale(_path(args.source), expected=expected, activate=args.activate)
            elif action == "upgrade":
                hs.upgrade_headscale(_path(args.target_package), _path(args.recovery_package), _path(args.backup),
                                     current=current, target=target, reviewed_releases=inventory)
            elif action == "unbound-backup":
                ub.backup_unbound(_path(args.destination))
            elif action == "unbound-validate":
                ub.validate_unbound_backup(_path(args.source), expected=expected)
            elif action == "unbound-recover-fresh":
                ub.recover_fresh_unbound(reviewed=review)
            elif action == "unbound-records-inspect":
                metadata = ur.inspect_records(expected=expected)
            elif action == "unbound-records-replace":
                ur.replace_records(update)
            else:
                ub.restore_unbound(_path(args.source), expected=expected, reviewed_rebind=rebind)
        print(json.dumps({"action": action, "confirmed": True, **metadata}))
        return 0
    except (Exception, KeyboardInterrupt):
        print("Maintenance unavailable or unconfirmed; preserve private recovery state and reconcile before retry",
              file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_headscale_maintenance_parser(parser.add_subparsers(required=True))
    return run_headscale_maintenance_command(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
