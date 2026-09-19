"""Read-only network firewall planning helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, cast

from lib.network_inventory import NetworkProfile, find_network_profile
from lib.types import JSONDict


PROXMOX_MANAGEMENT_TCP_PORTS = ["22", "8006"]
PROXMOX_CLUSTER_TCP_PORTS = ["5900-5999", "60000-60050"]
PROXMOX_CLUSTER_UDP_PORTS = ["5405-5412"]


@dataclass
class FirewallAddressSet:
    """A named set of addresses or CIDRs used by a firewall plan."""

    name: str
    entries: list[str]

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))


@dataclass
class FirewallRulePlan:
    """Provider-neutral firewall rule intent."""

    action: str
    source: str
    destination: str
    protocol: str = "any"
    ports: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))


@dataclass
class ProxmoxFirewallPlan:
    """Read-only Proxmox firewall plan derived from a network profile."""

    profile: str
    address_sets: list[FirewallAddressSet]
    rules: list[FirewallRulePlan]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def safe_to_apply(self) -> bool:
        return not self.errors

    def to_dict(self) -> JSONDict:
        payload = {
            "profile": self.profile,
            "address_sets": [address_set.to_dict() for address_set in self.address_sets],
            "rules": [rule.to_dict() for rule in self.rules],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "safe_to_apply": self.safe_to_apply,
        }
        return cast(JSONDict, payload)


@dataclass
class ProxmoxRenderedArtifact:
    """A concrete Proxmox firewall file or snippet."""

    path: str
    content: str
    description: str = ""

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))


@dataclass
class ProxmoxRenderedPlan:
    """Concrete Proxmox-native rendering for review."""

    profile: str
    artifacts: list[ProxmoxRenderedArtifact]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def safe_to_apply(self) -> bool:
        return not self.errors

    def to_dict(self) -> JSONDict:
        payload = {
            "profile": self.profile,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "safe_to_apply": self.safe_to_apply,
        }
        return cast(JSONDict, payload)


def plan_proxmox_control_plane_lockdown(
    profile_name: str,
    workspace: Optional[str] = None,
) -> ProxmoxFirewallPlan:
    """Plan Proxmox control-plane lockdown without touching remote hosts."""

    profile = find_network_profile(profile_name, workspace)
    if profile is None:
        raise ValueError(f"No network profile named '{profile_name}'")
    return build_proxmox_control_plane_lockdown_plan(profile)


def build_proxmox_control_plane_lockdown_plan(
    profile: NetworkProfile,
) -> ProxmoxFirewallPlan:
    """Build a read-only Proxmox control-plane lockdown plan."""

    warnings: list[str] = []
    errors: list[str] = []
    if not profile.management_sources:
        errors.append(
            "At least one management source is required before control-plane "
            "lockdown can be applied."
        )
    if not profile.control_plane:
        errors.append("At least one control-plane address is required.")
    if not profile.guest_networks:
        warnings.append(
            "No guest networks are defined; the guest-to-control-plane deny "
            "rule would have no source set."
        )

    address_sets = [
        FirewallAddressSet("basaltwater-management", list(profile.management_sources)),
        FirewallAddressSet("basaltwater-control-plane", list(profile.control_plane)),
        FirewallAddressSet("basaltwater-guests", list(profile.guest_networks)),
    ]
    if errors:
        # Keep rules empty whenever prerequisite safety checks fail so callers
        # cannot accidentally apply a partial lockdown plan.
        rules: list[FirewallRulePlan] = []
    else:
        rules = [
            FirewallRulePlan(
                action="ACCEPT",
                source="+basaltwater-management",
                destination="+basaltwater-control-plane",
                protocol="tcp",
                ports=list(PROXMOX_MANAGEMENT_TCP_PORTS),
                description="Allow management sources to reach SSH and Proxmox API",
            ),
            FirewallRulePlan(
                action="ACCEPT",
                source="+basaltwater-control-plane",
                destination="+basaltwater-control-plane",
                protocol="tcp",
                ports=list(PROXMOX_CLUSTER_TCP_PORTS),
                description="Allow Proxmox console and migration traffic between nodes",
            ),
            FirewallRulePlan(
                action="ACCEPT",
                source="+basaltwater-control-plane",
                destination="+basaltwater-control-plane",
                protocol="udp",
                ports=list(PROXMOX_CLUSTER_UDP_PORTS),
                description="Allow Proxmox cluster messaging between nodes",
            ),
            FirewallRulePlan(
                action="DROP",
                source="+basaltwater-guests",
                destination="+basaltwater-control-plane",
                description="Block guests from reaching Proxmox control-plane addresses",
            ),
        ]
    return ProxmoxFirewallPlan(
        profile=profile.name,
        address_sets=address_sets,
        rules=rules,
        warnings=warnings,
        errors=errors,
    )


def format_proxmox_firewall_plan(plan: ProxmoxFirewallPlan) -> str:
    """Render a Proxmox firewall plan for CLI review."""

    lines = [f"Proxmox firewall plan: {plan.profile}"]
    status = "yes" if plan.safe_to_apply else "no"
    lines.append(f"Safe to apply: {status}")
    if plan.errors:
        lines.append("Errors:")
        lines.extend(f"  {error}" for error in plan.errors)
    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"  {warning}" for warning in plan.warnings)
    lines.append("Address sets:")
    for address_set in plan.address_sets:
        entries = ", ".join(address_set.entries) if address_set.entries else "-"
        lines.append(f"  {address_set.name}: {entries}")
    lines.append("Rules:")
    for rule in plan.rules:
        ports = ",".join(rule.ports) if rule.ports else "any"
        lines.append(
            f"  {rule.action:<6} {rule.protocol:<3} "
            f"{rule.source} -> {rule.destination} ports={ports}"
        )
        if rule.description:
            lines.append(f"    {rule.description}")
    return "\n".join(lines)


def render_proxmox_firewall_plan(plan: ProxmoxFirewallPlan) -> ProxmoxRenderedPlan:
    """Render a read-only plan into Proxmox-native firewall artifacts."""

    if not plan.safe_to_apply:
        return ProxmoxRenderedPlan(
            profile=plan.profile,
            artifacts=[],
            warnings=list(plan.warnings),
            errors=list(plan.errors),
        )

    management_entries = _entries_for(plan, "basaltwater-management")
    control_entries = _entries_for(plan, "basaltwater-control-plane")
    guest_entries = _entries_for(plan, "basaltwater-guests")
    artifacts = [
        ProxmoxRenderedArtifact(
            path="/etc/pve/firewall/cluster.fw",
            description=(
                "Cluster-wide IP sets and security groups."
            ),
            content=_render_cluster_fw(
                management_entries,
                control_entries,
                guest_entries,
            ),
        ),
        ProxmoxRenderedArtifact(
            path="/etc/pve/nodes/<node>/host.fw",
            description=(
                "Host firewall snippet for every Proxmox control-plane node."
            ),
            content=_render_host_fw(),
        ),
        ProxmoxRenderedArtifact(
            path="/etc/pve/firewall/<VMID>.fw",
            description=(
                "Guest firewall snippet to attach to VMs and containers that "
                "should be blocked from the control plane."
            ),
            content=_render_guest_fw(),
        ),
    ]
    return ProxmoxRenderedPlan(
        profile=plan.profile,
        artifacts=artifacts,
        warnings=list(plan.warnings),
        errors=list(plan.errors),
    )


def format_rendered_proxmox_plan(rendered: ProxmoxRenderedPlan) -> str:
    """Render concrete Proxmox artifacts for CLI review."""

    lines = [f"Proxmox rendered plan: {rendered.profile}"]
    status = "yes" if rendered.safe_to_apply else "no"
    lines.append(f"Safe to apply: {status}")
    if rendered.errors:
        lines.append("Errors:")
        lines.extend(f"  {error}" for error in rendered.errors)
    if rendered.warnings:
        lines.append("Warnings:")
        lines.extend(f"  {warning}" for warning in rendered.warnings)
    for artifact in rendered.artifacts:
        lines.append("")
        lines.append(f"# {artifact.path}")
        if artifact.description:
            lines.append(f"# {artifact.description}")
        lines.append(artifact.content.rstrip())
    return "\n".join(lines)


def _entries_for(plan: ProxmoxFirewallPlan, name: str) -> list[str]:
    for address_set in plan.address_sets:
        if address_set.name == name:
            return list(address_set.entries)
    return []


def _render_cluster_fw(
    management_entries: list[str],
    control_entries: list[str],
    guest_entries: list[str],
) -> str:
    lines: list[str] = []
    lines.extend(_render_ipset("basaltwater-management", management_entries))
    lines.append("")
    lines.extend(_render_ipset("basaltwater-control-plane", control_entries))
    lines.append("")
    lines.extend(_render_ipset("basaltwater-guests", guest_entries))
    lines.append("")
    lines.extend(
        [
            "[group basaltwater-cluster-management]",
            "IN SSH(ACCEPT) -source +basaltwater-management",
            "IN ACCEPT -p tcp -dport 8006 -source +basaltwater-management",
            "IN ACCEPT -p tcp -dport 5900:5999 -source +basaltwater-control-plane",
            "IN ACCEPT -p tcp -dport 60000:60050 -source +basaltwater-control-plane",
            "IN ACCEPT -p udp -dport 5405:5412 -source +basaltwater-control-plane",
            "",
            "[group basaltwater-deny-control-plane]",
            "OUT DROP -dest +basaltwater-control-plane",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_ipset(name: str, entries: list[str]) -> list[str]:
    lines = [f"[IPSET {name}]"]
    if not entries:
        lines.append("# empty")
        return lines
    lines.extend(entries)
    return lines


def _render_host_fw() -> str:
    return "\n".join(
        [
            "[OPTIONS]",
            "enable: 1",
            "",
            "[RULES]",
            "GROUP basaltwater-cluster-management",
            "",
        ]
    )


def _render_guest_fw() -> str:
    return "\n".join(
        [
            "[OPTIONS]",
            "enable: 1",
            "",
            "[RULES]",
            "GROUP basaltwater-deny-control-plane",
            "",
            "# Add guest-specific ipfilter-net* IP sets when enabling IP filter.",
            "",
        ]
    )


__all__ = [
    "FirewallAddressSet",
    "FirewallRulePlan",
    "PROXMOX_CLUSTER_TCP_PORTS",
    "PROXMOX_CLUSTER_UDP_PORTS",
    "PROXMOX_MANAGEMENT_TCP_PORTS",
    "ProxmoxFirewallPlan",
    "ProxmoxRenderedArtifact",
    "ProxmoxRenderedPlan",
    "build_proxmox_control_plane_lockdown_plan",
    "format_proxmox_firewall_plan",
    "format_rendered_proxmox_plan",
    "plan_proxmox_control_plane_lockdown",
    "render_proxmox_firewall_plan",
]
