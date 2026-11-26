"""Built-in plugin discovery and system type metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import importlib
import pkgutil
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.config import SetupConfig
    from lib.types import StepFunc

    StepBuilder = Callable[["SetupConfig"], list[tuple[str, StepFunc]]]
    CustomStepProvider = Callable[[], Mapping[str, StepFunc]]
    ValidatorProvider = Callable[[], Mapping[str, Callable[..., object]]]


@dataclass(frozen=True)
class SystemTypeDefinition:
    """Plugin-owned metadata for a supported system type."""

    name: str
    description: str
    order: int
    include_desktop: bool = False
    include_cli_tools: bool = False
    include_control_plane_tools: bool = False
    include_desktop_apps: bool = False
    include_workstation_dev_apps: bool = False
    include_pc_dev_apps: bool = False
    include_web_server: bool = False
    include_web_firewall: bool = False
    default_enable_rdp: bool = False
    default_install_office: bool = False
    default_enable_smbclient: bool = False
    default_auto_restart: bool = True
    default_auto_restart_force_days: int = 7
    default_browser: str | None = None
    default_editor: str | None = None
    default_agent_tools: tuple[str, ...] = ()
    default_web_interfaces: tuple[str, ...] = ()
    default_device_pairing_providers: tuple[str, ...] = ()
    default_git_access: str = "none"
    default_git_auth_source: str | None = None
    default_agent_auth_source: str | None = None
    default_browser_automation: str | None = None
    step_builder: str | None = None

    def default_pairing_for(self, web_interfaces: Sequence[str] | None) -> tuple[str, ...]:
        """Select enrollment defaults for the enabled web interfaces."""
        return tuple(
            provider for provider in self.default_device_pairing_providers
            if provider in (web_interfaces or ())
        )


@dataclass(frozen=True)
class PluginDefinition:
    """Declarative plugin contract for built-in plugin discovery."""

    name: str
    module: str
    plugin_kind: str = "base"
    dependencies: tuple[str, ...] = ()
    system_types: tuple[SystemTypeDefinition, ...] = ()
    custom_steps: tuple[str, ...] = ()
    custom_step_provider: str | None = None
    validators: tuple[str, ...] = ()
    validator_provider: str | None = None


@dataclass(frozen=True)
class PluginRegistry:
    """Resolved plugin registry with deterministic plugin and system-type order."""

    plugins: tuple[PluginDefinition, ...]
    system_types: tuple[SystemTypeDefinition, ...]
    system_types_by_name: Mapping[str, SystemTypeDefinition] = field(default_factory=dict)
    custom_step_providers_by_name: Mapping[str, str] = field(default_factory=dict)
    validator_providers_by_name: Mapping[str, str] = field(default_factory=dict)


VALID_PLUGIN_KINDS = ("base", "capability", "composition")


def _discover_plugin_definitions(package_name: str = "plugins") -> list[PluginDefinition]:
    package = importlib.import_module(package_name)
    if not hasattr(package, "__path__"):
        raise ValueError(f"Plugin package {package_name!r} is not importable as a package")

    plugin_definitions: list[PluginDefinition] = []
    for _, module_name, is_package in sorted(pkgutil.iter_modules(package.__path__), key=lambda entry: entry[1]):
        if is_package:
            continue
        module = importlib.import_module(f"{package_name}.{module_name}")
        plugin_definition = getattr(module, "PLUGIN", None)
        if not isinstance(plugin_definition, PluginDefinition):
            raise ValueError(
                f"Plugin module {module.__name__!r} must define PLUGIN as PluginDefinition"
            )
        plugin_definitions.append(plugin_definition)
    return plugin_definitions


def build_plugin_registry(plugin_definitions: Sequence[PluginDefinition]) -> PluginRegistry:
    """Validate and resolve plugin definitions into a registry."""

    plugins_by_name: dict[str, PluginDefinition] = {}
    for plugin_definition in plugin_definitions:
        if plugin_definition.name in plugins_by_name:
            raise ValueError(f"Duplicate plugin name: {plugin_definition.name}")
        plugins_by_name[plugin_definition.name] = plugin_definition

    for plugin_definition in plugin_definitions:
        if plugin_definition.plugin_kind not in VALID_PLUGIN_KINDS:
            raise ValueError(
                f"Plugin {plugin_definition.name!r} has invalid plugin_kind "
                f"{plugin_definition.plugin_kind!r}; expected one of {VALID_PLUGIN_KINDS}"
            )
        if plugin_definition.plugin_kind == "base" and plugin_definition.dependencies:
            raise ValueError(
                f"Base plugin {plugin_definition.name!r} cannot declare dependencies"
            )
        if plugin_definition.plugin_kind == "capability" and plugin_definition.system_types:
            raise ValueError(
                f"Capability plugin {plugin_definition.name!r} cannot register system types"
            )
        if plugin_definition.plugin_kind == "composition":
            if not plugin_definition.dependencies:
                raise ValueError(
                    f"Composition plugin {plugin_definition.name!r} must declare dependencies"
                )
            if not plugin_definition.system_types:
                raise ValueError(
                    f"Composition plugin {plugin_definition.name!r} must register at least one system type"
                )
        if bool(plugin_definition.custom_steps) != bool(plugin_definition.custom_step_provider):
            raise ValueError(
                f"Plugin {plugin_definition.name!r} must declare both custom_steps "
                "and custom_step_provider together"
            )
        if bool(plugin_definition.validators) != bool(plugin_definition.validator_provider):
            raise ValueError(
                f"Plugin {plugin_definition.name!r} must declare both validators "
                "and validator_provider together"
            )

        for dependency in plugin_definition.dependencies:
            if dependency not in plugins_by_name:
                raise ValueError(
                    f"Plugin {plugin_definition.name!r} depends on unknown plugin {dependency!r}"
                )

    resolved_plugins: list[PluginDefinition] = []
    pending_plugins = dict(plugins_by_name)
    resolved_names: set[str] = set()
    while pending_plugins:
        ready_plugins = sorted(
            (
                plugin_definition
                for plugin_definition in pending_plugins.values()
                if all(dependency in resolved_names for dependency in plugin_definition.dependencies)
            ),
            key=lambda plugin_definition: plugin_definition.name,
        )
        if not ready_plugins:
            cycle = ", ".join(sorted(pending_plugins))
            raise ValueError(f"Cyclic plugin dependencies detected: {cycle}")

        for plugin_definition in ready_plugins:
            resolved_plugins.append(plugin_definition)
            resolved_names.add(plugin_definition.name)
            del pending_plugins[plugin_definition.name]

    system_types_by_name: dict[str, SystemTypeDefinition] = {}
    ordered_system_types: list[SystemTypeDefinition] = []
    custom_step_providers_by_name: dict[str, str] = {}
    custom_step_plugins_by_name: dict[str, str] = {}
    validator_providers_by_name: dict[str, str] = {}
    validator_plugins_by_name: dict[str, str] = {}
    for plugin_definition in resolved_plugins:
        for system_type in plugin_definition.system_types:
            if system_type.name in system_types_by_name:
                existing = system_types_by_name[system_type.name]
                raise ValueError(
                    "Duplicate system type "
                    f"{system_type.name!r} from {plugin_definition.name!r}; "
                    f"already registered by {existing.description!r}"
                )
            system_types_by_name[system_type.name] = system_type
            ordered_system_types.append(system_type)
        if plugin_definition.custom_step_provider:
            for custom_step in plugin_definition.custom_steps:
                if custom_step in custom_step_providers_by_name:
                    existing_plugin = custom_step_plugins_by_name[custom_step]
                    raise ValueError(
                        "Duplicate custom step "
                        f"{custom_step!r} from {plugin_definition.name!r}; "
                        f"already registered by {existing_plugin!r}"
                    )
                custom_step_providers_by_name[custom_step] = plugin_definition.custom_step_provider
                custom_step_plugins_by_name[custom_step] = plugin_definition.name
        if plugin_definition.validator_provider:
            for validator_name in plugin_definition.validators:
                if validator_name in validator_providers_by_name:
                    existing_plugin = validator_plugins_by_name[validator_name]
                    raise ValueError(
                        "Duplicate validator "
                        f"{validator_name!r} from {plugin_definition.name!r}; "
                        f"already registered by {existing_plugin!r}"
                    )
                validator_providers_by_name[validator_name] = plugin_definition.validator_provider
                validator_plugins_by_name[validator_name] = plugin_definition.name

    ordered_system_types.sort(key=lambda system_type: (system_type.order, system_type.name))
    return PluginRegistry(
        plugins=tuple(resolved_plugins),
        system_types=tuple(ordered_system_types),
        system_types_by_name=system_types_by_name,
        custom_step_providers_by_name=custom_step_providers_by_name,
        validator_providers_by_name=validator_providers_by_name,
    )


@lru_cache(maxsize=1)
def get_plugin_registry() -> PluginRegistry:
    """Discover and cache the built-in plugin registry."""

    return build_plugin_registry(_discover_plugin_definitions())


def get_system_type_names() -> list[str]:
    """Return all registered system type names in display order."""

    return [system_type.name for system_type in get_plugin_registry().system_types]


def get_system_type_definition(system_type: str) -> SystemTypeDefinition:
    """Return metadata for a registered system type."""

    try:
        return get_plugin_registry().system_types_by_name[system_type]
    except KeyError as exc:
        raise ValueError(f"Unknown system type: {system_type!r}") from exc


def _resolve_reference(reference: str, reference_kind: str) -> Callable[..., object]:
    """Resolve a lazy module:function reference."""

    module_name, separator, function_name = reference.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError(f"Invalid {reference_kind} reference: {reference!r}")

    module = importlib.import_module(module_name)
    resolved = getattr(module, function_name, None)
    if resolved is None or not callable(resolved):
        raise ValueError(f"{reference_kind.capitalize()} {reference!r} is not callable")
    return resolved


def resolve_step_builder(system_type: str) -> "StepBuilder":
    """Resolve a system type's lazy step-builder reference."""

    definition = get_system_type_definition(system_type)
    if not definition.step_builder:
        raise ValueError(f"No step builder registered for system type: {system_type}")

    builder = _resolve_reference(definition.step_builder, "step builder")
    return builder  # type: ignore[return-value]


@lru_cache(maxsize=None)
def _get_custom_step_provider(provider_reference: str) -> Mapping[str, "StepFunc"]:
    """Load and cache a plugin custom-step provider mapping."""

    provider = _resolve_reference(provider_reference, "custom step provider")
    mapping = provider()
    if not isinstance(mapping, Mapping):
        raise ValueError(
            f"Custom step provider {provider_reference!r} must return a mapping of step names"
        )
    return mapping


@lru_cache(maxsize=None)
def _get_validator_provider(provider_reference: str) -> Mapping[str, Callable[..., object]]:
    """Load and cache a plugin validator provider mapping."""

    provider = _resolve_reference(provider_reference, "validator provider")
    mapping = provider()
    if not isinstance(mapping, Mapping):
        raise ValueError(
            f"Validator provider {provider_reference!r} must return a mapping of validator names"
        )
    return mapping


def resolve_custom_step(step_name: str) -> "StepFunc":
    """Resolve a custom step name to its plugin-owned step function."""

    try:
        provider_reference = get_plugin_registry().custom_step_providers_by_name[step_name]
    except KeyError as exc:
        raise ValueError(f"Unknown step: {step_name}") from exc

    mapping = _get_custom_step_provider(provider_reference)
    try:
        step_function = mapping[step_name]
    except KeyError as exc:
        raise ValueError(
            f"Plugin custom step provider {provider_reference!r} did not expose {step_name!r}"
        ) from exc
    return step_function


def resolve_validator(validator_name: str) -> Callable[..., object]:
    """Resolve a validator or parser name to its plugin-owned callable."""

    try:
        provider_reference = get_plugin_registry().validator_providers_by_name[validator_name]
    except KeyError as exc:
        raise ValueError(f"Unknown validator: {validator_name}") from exc

    mapping = _get_validator_provider(provider_reference)
    try:
        validator = mapping[validator_name]
    except KeyError as exc:
        raise ValueError(
            f"Plugin validator provider {provider_reference!r} did not expose {validator_name!r}"
        ) from exc
    return validator


def format_system_type_help(indent: str = "  ") -> str:
    """Return aligned help text for all registered system types."""

    system_types = get_plugin_registry().system_types
    if not system_types:
        return ""

    width = max(len(system_type.name) for system_type in system_types) + 2
    return "\n".join(
        f"{indent}{system_type.name.ljust(width)}{system_type.description}"
        for system_type in system_types
    )
