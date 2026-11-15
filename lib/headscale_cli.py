"""Standalone parser and handler for saved Headscale references."""

from __future__ import annotations

import argparse
import uuid

from lib.headscale_registry import (
    HeadscaleServer,
    _validate_name,
    _validate_url,
    list_servers,
    mutate_servers,
    resolve_server,
)


def add_headscale_parser(subparsers) -> argparse.ArgumentParser:
    """Register the ``headscale`` command and return its parser."""
    parser = subparsers.add_parser("headscale", help="Manage saved Headscale server references")
    commands = parser.add_subparsers(dest="headscale_action", required=True)

    def common(command):
        command.add_argument("--workspace")
        command.add_argument("--dry-run", action="store_true")

    add = commands.add_parser("add", help="Save a server reference")
    common(add)
    add.add_argument("name")
    add.add_argument("--url", required=True)
    add.add_argument("--origin", choices=("managed", "external"), default="external")
    add.add_argument("--onboarding-url")

    listing = commands.add_parser("list", help="List saved references")
    common(listing)

    show = commands.add_parser("show", help="Show one saved reference")
    common(show)
    show.add_argument("reference")

    rename = commands.add_parser("rename", help="Rename a saved reference")
    common(rename)
    rename.add_argument("reference")
    rename.add_argument("name")

    remove = commands.add_parser("remove", help="Remove a local reference")
    common(remove)
    remove.add_argument("reference")

    onboarding = commands.add_parser("onboarding", help="Print a trusted onboarding page URL")
    common(onboarding)
    onboarding.add_argument("reference")
    parser.set_defaults(headscale_handler=run_headscale_command)
    return parser


def run_headscale_command(args: argparse.Namespace) -> int:
    """Handle parsed Headscale reference commands; never contact a server."""
    action = args.headscale_action
    workspace = args.workspace
    dry_run = bool(args.dry_run)
    if action == "list":
        for item in list_servers(workspace):
            print(f"{item.name}\t{item.url}\t{item.origin}")
        return 0
    if action == "show":
        item = resolve_server(args.reference, workspace)
        print(f"id: {item.id}\nname: {item.name}\nurl: {item.url}\norigin: {item.origin}")
        if item.onboarding_url:
            print(f"onboarding_url: {item.onboarding_url}")
        return 0
    if action == "onboarding":
        item = resolve_server(args.reference, workspace)
        if not item.onboarding_url:
            raise ValueError("No onboarding URL is saved for this server")
        print(item.onboarding_url)
        return 0

    if action == "add":
        name = _validate_name(args.name)
        url = _validate_url(args.url, "Server URL")
        onboarding_url = _validate_url(args.onboarding_url, "Onboarding URL") if args.onboarding_url else None
        record = HeadscaleServer(str(uuid.uuid4()), name, url, args.origin, onboarding_url)

        def add_record(records):
            if any(item.name.casefold() == name.casefold() for item in records):
                raise ValueError("A Headscale server with that name already exists")
            return (None if dry_run else records + [record]), record

        if dry_run:
            if any(item.name.casefold() == name.casefold() for item in list_servers(workspace)):
                raise ValueError("A Headscale server with that name already exists")
            result = record
        else:
            result = mutate_servers(workspace, add_record)
        print(f"{'Would add' if dry_run else 'Added'} {result.name} ({result.url})")
        return 0

    reference = args.reference
    selected = resolve_server(reference, workspace)
    if action == "rename":
        name = _validate_name(args.name)

        def rename_record(records):
            if selected not in records:
                raise ValueError("Headscale reference changed; inspect it before renaming")
            if any(item.id != selected.id and item.name.casefold() == name.casefold() for item in records):
                raise ValueError("A Headscale server with that name already exists")
            renamed = HeadscaleServer(selected.id, name, selected.url, selected.origin,
                                      selected.onboarding_url)
            changed = [HeadscaleServer(r.id, name, r.url, r.origin, r.onboarding_url)
                       if r.id == selected.id else r for r in records]
            return (None if dry_run else changed), renamed

        if dry_run:
            if any(item.id != selected.id and item.name.casefold() == name.casefold()
                   for item in list_servers(workspace)):
                raise ValueError("A Headscale server with that name already exists")
            result = HeadscaleServer(selected.id, name, selected.url, selected.origin,
                                     selected.onboarding_url)
        else:
            result = mutate_servers(workspace, rename_record)
        print(f"{'Would rename' if dry_run else 'Renamed'} {selected.name} to {result.name}")
        return 0
    if action == "remove":
        def remove_record(records):
            if selected not in records:
                raise ValueError("Headscale reference changed; inspect it before removal")
            changed = [r for r in records if r.id != selected.id]
            return (None if dry_run else changed), selected

        if not dry_run:
            mutate_servers(workspace, remove_record)
        print(f"{'Would remove' if dry_run else 'Removed'} local reference {selected.name}")
        return 0
    raise ValueError("Unknown Headscale command")
