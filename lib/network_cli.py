"""CLI support for generic network inventory commands."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from lib.network_firewall import (
    format_proxmox_firewall_plan,
    format_rendered_proxmox_plan,
    plan_proxmox_control_plane_lockdown,
    render_proxmox_firewall_plan,
)
from lib.network_inventory import (
    NetworkHost,
    NetworkProfile,
    NetworkSubnet,
    add_network_host,
    find_network_profile,
    load_network_profiles,
    upsert_network_profile,
)
from lib.network_proxmox import (
    import_proxmox_guest_networks,
    import_registered_proxmox_hosts,
)


def add_network_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the top-level ``network`` command tree."""

    parser = subparsers.add_parser(
        "network",
        help="Manage generic network inventory profiles",
        description=(
            "Manage provider-neutral network inventory profiles used by future "
            "firewall planners and provider adapters."
        ),
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )

    sub = parser.add_subparsers(dest="network_command", help="Network command")

    list_cmd = sub.add_parser("list", aliases=["ls"], help="List network profiles")
    list_cmd.add_argument("--json", action="store_true", help="Output JSON")
    list_cmd.set_defaults(_handler=_cmd_list)

    show = sub.add_parser("show", help="Show one network profile")
    show.add_argument("profile", help="Network profile name")
    show.add_argument("--json", action="store_true", help="Output JSON")
    show.set_defaults(_handler=_cmd_show)

    init = sub.add_parser("init", help="Create a network profile")
    init.add_argument("profile", help="Network profile name")
    init.add_argument(
        "--management",
        action="append",
        default=[],
        help="Management source IP or CIDR; repeatable",
    )
    init.add_argument(
        "--control-plane",
        action="append",
        default=[],
        help="Control-plane IP or CIDR; repeatable",
    )
    init.add_argument(
        "--guest-network",
        action="append",
        default=[],
        help="Guest network IP or CIDR; repeatable",
    )
    init.add_argument(
        "--subnet",
        action="append",
        nargs=3,
        metavar=("NAME", "CIDR", "ZONE"),
        default=[],
        help="Named subnet: NAME CIDR ZONE; repeatable",
    )
    init.add_argument(
        "--vlan",
        action="append",
        nargs=4,
        metavar=("ID", "NAME", "CIDR", "ZONE"),
        default=[],
        help="Named VLAN subnet: ID NAME CIDR ZONE; repeatable",
    )
    init.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing profile with the same name",
    )
    init.set_defaults(_handler=_cmd_init)

    add_host = sub.add_parser("add-host", help="Add a host to a network profile")
    add_host.add_argument("profile", help="Network profile name")
    add_host.add_argument("name", help="Host name")
    add_host.add_argument("address", help="Host IP address")
    add_host.add_argument(
        "--provider",
        default="generic",
        help="Provider tag such as generic, proxmox, linux, router, or switch",
    )
    add_host.add_argument(
        "--role",
        action="append",
        dest="roles",
        default=[],
        help="Host role tag; repeatable",
    )
    add_host.add_argument(
        "--ref",
        dest="profile_ref",
        help="Optional reference to another basaltwater record",
    )
    add_host.add_argument(
        "--replace",
        action="store_true",
        help="Replace a host with the same name or address",
    )
    add_host.set_defaults(_handler=_cmd_add_host)

    import_proxmox = sub.add_parser(
        "import-proxmox",
        help="Import registered Proxmox hosts into a network profile",
    )
    import_proxmox.add_argument("profile", help="Network profile name")
    import_proxmox.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=[],
        help="Registered Proxmox host name or address to import; repeatable",
    )
    import_proxmox.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=[],
        help="Only import Proxmox hosts with this registry tag; repeatable",
    )
    import_proxmox.add_argument(
        "--no-control-plane",
        action="store_true",
        help="Do not add imported node IPs to the profile control-plane set",
    )
    import_proxmox.set_defaults(_handler=_cmd_import_proxmox)

    import_guests = sub.add_parser(
        "import-proxmox-guests",
        help="Import guest subnets from registered Proxmox hosts",
    )
    import_guests.add_argument("profile", help="Network profile name")
    import_guests.add_argument(
        "--host",
        action="append",
        dest="hosts",
        default=[],
        help="Registered Proxmox host name or address to scan; repeatable",
    )
    import_guests.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=[],
        help="Only scan Proxmox hosts with this registry tag; repeatable",
    )
    import_guests.set_defaults(_handler=_cmd_import_proxmox_guests)

    plan_proxmox = sub.add_parser(
        "plan-proxmox",
        help="Plan Proxmox control-plane firewall lockdown without applying it",
    )
    plan_proxmox.add_argument("profile", help="Network profile name")
    plan_proxmox.add_argument("--json", action="store_true", help="Output JSON")
    plan_proxmox.add_argument(
        "--proxmox",
        action="store_true",
        help="Render concrete Proxmox firewall artifacts instead of the abstract plan",
    )
    plan_proxmox.set_defaults(_handler=_cmd_plan_proxmox)

    return parser


def run_network_command(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``network`` subcommand."""

    handler = getattr(args, "_handler", None)
    if handler is None:
        print(
            "Error: network command required "
            "(list, show, init, add-host, import-proxmox, "
            "import-proxmox-guests, plan-proxmox)"
        )
        return 1
    try:
        return handler(args, getattr(args, "workspace", None))
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1


def _cmd_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    profiles = load_network_profiles(workspace)
    if args.json:
        print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
        return 0
    if not profiles:
        print("No network profiles.")
        return 0
    print(f"{'PROFILE':<24} {'MGMT':>5} {'CTRL':>5} {'GUEST':>5} {'SUBNETS':>7} {'HOSTS':>5}")
    print("-" * 60)
    for profile in profiles:
        print(
            f"{profile.name:<24} "
            f"{len(profile.management_sources):>5} "
            f"{len(profile.control_plane):>5} "
            f"{len(profile.guest_networks):>5} "
            f"{len(profile.subnets):>7} "
            f"{len(profile.hosts):>5}"
        )
    return 0


def _cmd_show(args: argparse.Namespace, workspace: Optional[str]) -> int:
    profile = find_network_profile(args.profile, workspace)
    if profile is None:
        raise ValueError(f"No network profile named '{args.profile}'")
    if args.json:
        print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
        return 0
    print(f"Network profile: {profile.name}")
    _print_list("Management", profile.management_sources)
    _print_list("Control plane", profile.control_plane)
    _print_list("Guest networks", profile.guest_networks)
    if profile.subnets:
        print("Subnets:")
        for subnet in profile.subnets:
            vlan = f" vlan={subnet.vlan_id}" if subnet.vlan_id is not None else ""
            gateway = f" gateway={subnet.gateway}" if subnet.gateway else ""
            print(f"  {subnet.name}: {subnet.cidr} zone={subnet.zone or '-'}{vlan}{gateway}")
    if profile.hosts:
        print("Hosts:")
        for host in profile.hosts:
            roles = ",".join(host.roles) if host.roles else "-"
            print(f"  {host.name}: {host.address} provider={host.provider} roles={roles}")
    return 0


def _cmd_init(args: argparse.Namespace, workspace: Optional[str]) -> int:
    subnets = [
        NetworkSubnet(name=name, cidr=cidr, zone=zone)
        for name, cidr, zone in args.subnet
    ]
    for vlan_id_raw, name, cidr, zone in args.vlan:
        try:
            vlan_id = int(vlan_id_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid VLAN ID: {vlan_id_raw}") from exc
        subnets.append(NetworkSubnet(name=name, cidr=cidr, zone=zone, vlan_id=vlan_id))
    profile = NetworkProfile(
        name=args.profile,
        management_sources=list(args.management),
        control_plane=list(args.control_plane),
        guest_networks=list(args.guest_network),
        subnets=subnets,
    )
    upsert_network_profile(profile, workspace, replace=args.replace)
    print(f"Saved network profile '{profile.name}'.")
    return 0


def _cmd_add_host(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = NetworkHost(
        name=args.name,
        address=args.address,
        provider=args.provider,
        roles=list(args.roles),
        profile_ref=args.profile_ref,
    )
    profile = add_network_host(
        args.profile,
        host,
        workspace,
        replace=args.replace,
    )
    print(f"Added host '{host.name}' to network profile '{profile.name}'.")
    return 0


def _cmd_import_proxmox(args: argparse.Namespace, workspace: Optional[str]) -> int:
    result = import_registered_proxmox_hosts(
        args.profile,
        workspace,
        targets=list(args.hosts),
        tags=list(args.tags),
        include_control_plane=not args.no_control_plane,
    )
    print(
        f"Imported {len(result.imported_hosts)} Proxmox host(s) into "
        f"network profile '{result.profile.name}'."
    )
    for host in result.imported_hosts:
        marker = (
            " control-plane"
            if host.address in result.control_plane_added
            else ""
        )
        print(f"  {host.name}: {host.address}{marker}")
    if result.skipped_hosts:
        print("Skipped conflicting non-Proxmox host(s):")
        for host_name in result.skipped_hosts:
            print(f"  {host_name}")
    return 0


def _cmd_import_proxmox_guests(
    args: argparse.Namespace,
    workspace: Optional[str],
) -> int:
    result = import_proxmox_guest_networks(
        args.profile,
        workspace,
        targets=list(args.hosts),
        tags=list(args.tags),
    )
    print(
        f"Scanned {result.scanned_guests} Proxmox guest(s) for "
        f"network profile '{result.profile.name}'."
    )
    if result.imported_networks:
        print("Imported guest networks:")
        for cidr in result.imported_networks:
            print(f"  {cidr}")
    else:
        print("No new guest networks imported.")
    if result.skipped_guests:
        print("Skipped guest scan(s):")
        for skipped in result.skipped_guests:
            print(f"  {skipped}")
    return 0


def _cmd_plan_proxmox(args: argparse.Namespace, workspace: Optional[str]) -> int:
    plan = plan_proxmox_control_plane_lockdown(args.profile, workspace)
    if args.proxmox and args.json:
        rendered = render_proxmox_firewall_plan(plan)
        print(json.dumps(rendered.to_dict(), indent=2, sort_keys=True))
        return 0 if rendered.safe_to_apply else 1
    if args.proxmox:
        rendered = render_proxmox_firewall_plan(plan)
        print(format_rendered_proxmox_plan(rendered))
        return 0 if rendered.safe_to_apply else 1
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_proxmox_firewall_plan(plan))
    return 0 if plan.safe_to_apply else 1


def _print_list(label: str, values: list[str]) -> None:
    print(f"{label}:")
    if not values:
        print("  -")
        return
    for value in values:
        print(f"  {value}")
