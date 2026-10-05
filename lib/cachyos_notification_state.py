"""Private, metadata-only receipts for local workstation webhook delivery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from lib.atomic_io import read_json_file, write_json_atomic
from lib.config import SetupConfig
from lib.notifications import normalize_notification_level, parse_notification_args
from lib.remote_utils import is_dry_run
from lib.validation import validate_filesystem_path


def _path() -> Path:
    from lib.cachyos_refresh import _record_path

    path = _record_path().with_name("last-notification.json")
    validate_filesystem_path(str(path))
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 8192):
            raise ValueError("Unsafe notification receipt")
    return path


def _configuration(config: SetupConfig) -> tuple[int, str]:
    targets = parse_notification_args(config.notify_specs, config.notification_level,
                                      config.notification_strict_https)
    # Match future observations to this selection without persisting endpoint
    # paths, query secrets, tokens, or exception output in the receipt.
    canonical = sorted((target.type, target.target, target.level, target.strict_https) for target in targets)
    return len(targets), hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def record_notification_result(config: SetupConfig, *, success: bool, delivered: bool) -> None:
    """Save aggregate delivery evidence without changing successful setup state."""
    if config.dry_run or is_dry_run() or not config.notify_specs:
        return
    from lib.cachyos import validate_cachyos_config

    validate_cachyos_config(config)
    if type(success) is not bool or type(delivered) is not bool:
        raise ValueError("Notification outcomes must be boolean")
    count, digest = _configuration(config)
    level = normalize_notification_level(config.notification_level)
    suppressed = level == "off" or (success and level in {"warning", "error"})
    path = _path()
    # The first failed setup can notify before a successful selection exists.
    # Check newly created ancestors again before writing private metadata.
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _path()
    write_json_atomic(str(path), {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "configuration_digest": digest,
        "target_count": count,
        "setup_succeeded": success,
        "delivery": "suppressed" if suppressed else "delivered" if delivered else "failed",
    }, mode=0o600)


def collect_notification_health(config: SetupConfig) -> tuple[str, str]:
    """Observe the last matching setup attempt; never contact a receiver."""
    level = normalize_notification_level(config.notification_level)
    if level == "off":
        return "deferred", "Webhook delivery disabled by notification level; targets remain saved."
    try:
        path = _path()
        if not path.exists():
            return "deferred", "Webhook targets saved; no delivery receipt recorded yet. Receiver delivery is unverified."
        value = read_json_file(str(path), max_bytes=8192)
        fields = {"schema_version", "recorded_at", "configuration_digest", "target_count", "setup_succeeded", "delivery"}
        if (not isinstance(value, dict) or set(value) != fields
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["setup_succeeded"]) is not bool
                or type(value["target_count"]) is not int or not 1 <= value["target_count"] <= 4096
                or value["delivery"] not in {"delivered", "failed", "suppressed"}
                or not isinstance(value["configuration_digest"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["configuration_digest"])
                or not isinstance(value["recorded_at"], str) or len(value["recorded_at"]) > 40):
            raise ValueError("Invalid notification receipt")
        recorded = datetime.fromisoformat(value["recorded_at"])
        if recorded.tzinfo is None or recorded > datetime.now(timezone.utc):
            raise ValueError("Invalid notification receipt timestamp")
        count, digest = _configuration(config)
        if value["configuration_digest"] != digest or value["target_count"] != count:
            return "deferred", "Last webhook receipt belongs to a different target selection or policy; current delivery is unverified."
        # During a new setup the doctor runs before its notification. A prior
        # result must not qualify the newly completed setup's notification.
        receipt_path = path.with_name("last-report.json")
        if receipt_path.exists() or receipt_path.is_symlink():
            info = receipt_path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 262144):
                raise ValueError("Unsafe setup receipt")
            receipt = read_json_file(str(receipt_path), max_bytes=262144)
            completed = datetime.fromisoformat(receipt["completed_at"])
            if completed.tzinfo is None:
                raise ValueError("Invalid setup receipt timestamp")
            if recorded < completed:
                return "deferred", "Webhook receipt predates the latest completed setup; its notification delivery is unverified."
        if value["delivery"] == "suppressed":
            if not value["setup_succeeded"] or level not in {"warning", "error"}:
                raise ValueError("Inconsistent notification receipt")
            return "deferred", "Latest successful setup notification suppressed by delivery level; no receiver delivery attempted."
        if value["delivery"] == "failed":
            return "failed", "Last setup webhook delivery incomplete; inspect receiver connectivity, target/token, and setup output locally."
        outcome = "successful" if value["setup_succeeded"] else "failed"
        return "available", f"Last {outcome} setup result accepted by all {count} configured webhook targets; downstream processing is not verified."
    except FileNotFoundError:
        return "deferred", "Webhook delivery receipt unavailable; receiver delivery is unverified."
    except OSError:
        return "deferred", "Webhook delivery receipt unreadable; receiver delivery is unverified."
    except (ValueError, KeyError, TypeError, AttributeError):
        return "failed", "Webhook delivery receipt unsafe or invalid; inspect local state before relying on delivery evidence."
