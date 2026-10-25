"""Optional local-console browser capability."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(name="kiosk", module=__name__, plugin_kind="capability", dependencies=("core",))


def extend_kiosk_steps(config: SetupConfig, steps: list[tuple[str, StepFunc]]) -> None:
    """Preflight before profile changes, then configure after host web services."""
    if config.kiosk_mode is None:
        return
    from desktop.kiosk_steps import configure_kiosk, preflight_kiosk

    steps.insert(0, ("Checking local kiosk console and ownership", preflight_kiosk))
    steps.append(("Configuring local browser kiosk", configure_kiosk))
