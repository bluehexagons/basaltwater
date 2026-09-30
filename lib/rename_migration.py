"""Retirement notice for the completed infra-tools namespace migration."""

from __future__ import annotations


def migrate(*, system: bool, apply: bool, recovery: bool = False,
            installation: str | None = None) -> int:
    """Reject retired migration requests without reading or changing old state."""
    print(
        "infra-tools migration has been retired. Use the intermediate checkout "
        "97da1799615d5ad7e3d34c7c38a38ae41631e5fd for migration or recovery, "
        "then upgrade to current Basaltwater. See docs/BASALTWATER_MIGRATION.md."
    )
    return 1
