"""Host-wide cache maintenance provisioning, independent of agent tooling."""

from __future__ import annotations

from lib.config import SetupConfig
from lib.maintenance_systemd import configure_maintenance_timer


def configure_storage_maintenance(config: SetupConfig) -> None:
    if not configure_maintenance_timer(
        service_name="basaltwater-storage", service_desc="Maintain disposable host caches and completed build payloads",
        timer_desc="Inspect host storage budgets daily", schedule="daily",
        script_path="/opt/basaltwater/common/service_tools/storage_maintenance.py",
        script_args=("--apply", "--account", config.username, "--payloads"),
        isolated_python=True, check_name="Host storage", network_online=False,
        timeout="30min",
    ):
        raise RuntimeError("Storage maintenance timer configuration failed")
