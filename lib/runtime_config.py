"""Runtime configuration for storage operations.

This module provides a lightweight configuration class for runtime
operations (sync/scrub) that only includes fields needed by the
orchestrator and service tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class RuntimeConfig:
    """Lightweight runtime configuration for storage operations.

    This class provides type safety for runtime use (orchestrator, service tools)
    without the overhead of full SetupConfig which includes many setup-time
    options that aren't needed during periodic operations.

    Attributes:
        username: System user for file operations
        friendly_name: Human-readable name for this system (e.g. 'scrapbox')
        sync_specs: List of sync specifications [source, dest, interval]
        scrub_specs: List of scrub specifications [dir, db, redundancy, freq]
        notify_specs: List of notification specifications [type, target]
        notification_level: Outbound notification threshold for this system
        notification_strict_https: Require certificate and hostname verification
            for HTTPS notification webhooks
        smb_mounts: List of SMB mount specifications [mountpoint, ip, creds, share, subdir]
    """
    username: str
    sync_specs: list[list[str]]
    scrub_specs: list[list[str]]
    notify_specs: list[list[str]]
    backup_specs: list[list[str]] = field(default_factory=list)
    friendly_name: Optional[str] = None
    smb_mounts: Optional[list[list[str]]] = None
    notification_level: Optional[str] = None
    notification_strict_https: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeConfig":
        """Create RuntimeConfig from a dictionary (loaded from JSON state).

        Args:
            data: Dictionary containing config fields (typically from setup.json)

        Returns:
            RuntimeConfig instance
        """
        return cls(
            username=data.get("username", "root"),
            sync_specs=data.get("sync_specs") or [],
            backup_specs=data.get("backup_specs") or [],
            scrub_specs=data.get("scrub_specs") or [],
            notify_specs=data.get("notify_specs") or [],
            friendly_name=data.get("friendly_name"),
            smb_mounts=data.get("smb_mounts"),
            notification_level=data.get("notification_level"),
            notification_strict_https=data.get("notification_strict_https") is True,
        )

    @classmethod
    def from_setup_config(cls, config: Any) -> "RuntimeConfig":
        """Create RuntimeConfig from a SetupConfig instance.

        Args:
            config: SetupConfig instance from lib.config

        Returns:
            RuntimeConfig instance
        """
        return cls(
            username=config.username,
            sync_specs=config.sync_specs or [],
            backup_specs=getattr(config, "backup_specs", None) or [],
            scrub_specs=config.scrub_specs or [],
            notify_specs=config.notify_specs or [],
            friendly_name=getattr(config, 'friendly_name', None),
            smb_mounts=config.smb_mounts,
            notification_level=getattr(config, 'notification_level', None),
            notification_strict_https=(
                getattr(config, 'notification_strict_https', None) is True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation
        """
        return {
            "username": self.username,
            "friendly_name": self.friendly_name,
            "sync_specs": self.sync_specs,
            "backup_specs": self.backup_specs or [],
            "scrub_specs": self.scrub_specs,
            "notify_specs": self.notify_specs,
            "smb_mounts": self.smb_mounts,
            "notification_level": self.notification_level,
            "notification_strict_https": self.notification_strict_https,
        }

    def has_storage_ops(self) -> bool:
        """Check if any storage operations are configured.

        Returns:
            True if sync or scrub specs are configured
        """
        return bool(self.sync_specs or self.backup_specs or self.scrub_specs)

    def all_sync_specs(self) -> list[list[str]]:
        """Return ordinary sync and semantic backup jobs for execution."""

        return [*self.sync_specs, *(self.backup_specs or [])]

    def get_all_paths(self) -> list[str]:
        """Get all unique paths from sync and scrub specs.

        Returns:
            Sorted list of unique paths
        """
        # Import here to avoid circular dependency
        from lib.task_utils import get_all_storage_paths
        return get_all_storage_paths(self)
