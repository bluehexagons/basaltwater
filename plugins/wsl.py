"""Small Ubuntu-on-WSL setup composition for build and coding tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.plugin_registry import PluginDefinition, SystemTypeDefinition

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.types import StepFunc


PLUGIN = PluginDefinition(
    name="wsl",
    module=__name__,
    plugin_kind="composition",
    dependencies=("core", "common"),
    system_types=(SystemTypeDefinition(
        name="server_wsl",
        description="Ubuntu WSL build and agent tools",
        order=39,
        default_auto_restart=False,
        default_auto_restart_force_days=0,
        step_builder="plugins.wsl:build_wsl_steps",
    ),),
)


def build_wsl_steps(config: SetupConfig) -> list[tuple[str, StepFunc]]:
    from common.agent_steps import install_claude, install_codex, install_github_cli, install_opencode
    from common.common_steps import install_go, install_node, install_python
    from common.wsl_steps import install_wsl_base, preflight_wsl, report_wsl_readiness

    steps: list[tuple[str, StepFunc]] = [
        ("Checking Ubuntu WSL environment", preflight_wsl),
        ("Installing Ubuntu build prerequisites", install_wsl_base),
    ]
    for selected, title, function in (
        (config.install_go, "Installing Go", install_go),
        (config.install_node, "Installing Node.js", install_node),
        (config.install_python, "Installing Python tooling", install_python),
        (config.install_gh, "Installing GitHub CLI", install_github_cli),
        (config.install_codex, "Installing Codex CLI", install_codex),
        (config.install_claude, "Installing Claude Code", install_claude),
        (config.install_opencode, "Installing OpenCode", install_opencode),
    ):
        if selected:
            steps.append((title, function))
    steps.append(("Checking WSL tool readiness", report_wsl_readiness))
    return steps
