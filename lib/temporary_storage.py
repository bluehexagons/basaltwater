"""Retention for temporary artifacts created by Basaltwater operations."""

from __future__ import annotations

import os
import re
import stat
import time

from lib.agent_storage_inventory import _mounts
from lib.cleanup_paths import process_uses_path, remove_checked_path
from lib.maintenance_defaults import BASALTWATER_TMP_PATTERNS, STALE_BASALTWATER_TMP_MAX_AGE_DAYS
from lib.setup_directories import directory_fd
from lib.validation import validate_positive_integer

_ARTIFACT = re.compile(rf"(?:{'|'.join(BASALTWATER_TMP_PATTERNS)})")


def clean_temporary_artifacts(
    directory: str, *, apply: bool, max_age_days: int = STALE_BASALTWATER_TMP_MAX_AGE_DAYS,
) -> dict:
    """Inspect exact, aged artifacts, preserving mounted and in-use paths."""
    age = validate_positive_integer(str(max_age_days), "max age days")
    cutoff = time.time() - age * 86400
    report = {"selected": 0, "deferred": 0, "failures": []}
    try:
        with directory_fd(directory) as fd, os.scandir(fd) as entries:
            for entry in entries:
                if not _ARTIFACT.fullmatch(entry.name):
                    continue
                path = os.path.join(directory, entry.name)
                try:
                    info = entry.stat(follow_symlinks=False)
                    if info.st_mtime > cutoff:
                        continue
                    if not (stat.S_ISDIR(info.st_mode) or
                            stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                        continue

                    def protected():
                        return any(mount == path or mount.startswith(path + "/") for mount in _mounts()) or (
                            process_uses_path(path, uid=info.st_uid, include_open_files=True))

                    if remove_checked_path(path, info, uid=info.st_uid, in_use=protected, dry_run=not apply):
                        report["selected"] += 1
                    else:
                        report["deferred"] += 1
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    recovery = getattr(exc, "recovery_path", None)
                    detail = f"{path}: {exc}"
                    if recovery:
                        detail += f" (retained at {recovery})"
                    report["failures"].append(detail)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        report["failures"].append(f"{directory}: {exc}")
    return report
