"""Local Debian maintenance and read-only CachyOS qualification diagnostics."""

from __future__ import annotations

import argparse
import getpass
import os

from common.common_steps import install_apt_packages, update_and_upgrade_packages
from common.network_steps import configure_static_network, configure_system_hostname
from desktop.browser_steps import configure_default_browser, install_browser
from plugins.desktop import extend_desktop_steps
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run, set_dry_run
from lib.types import StepFunc
from lib.validation import validate_apt_packages
from lib.validators import validate_username


LOCAL_DESKTOPS = ("xfce", "i3", "cinnamon", "lxqt")
LOCAL_BROWSERS = ("brave", "firefox", "browsh", "helium", "lynx", "librewolf")


def _target_username() -> str:
    """Resolve the non-root user whose desktop files should be maintained."""

    username = os.environ.get("SUDO_USER") or getpass.getuser()
    if not validate_username(username):
        raise ValueError(f"Invalid local maintenance user: {username}")
    return username


def _make_config(*, dry_run: bool = False, **values: object) -> SetupConfig:
    """Build the small config object shared by existing local setup steps."""

    return SetupConfig(
        host="localhost",
        username=_target_username(),
        system_type="workstation_dev",
        machine_type="hardware",
        dry_run=dry_run,
        **values,
    )


def _run_step(
    config: SetupConfig,
    label: str,
    step: StepFunc,
) -> int:
    """Run one shared setup step with local root and dry-run handling."""

    if os.geteuid() != 0:
        print("Error: local maintenance commands require root; rerun with sudo.")
        return 1

    previous_dry_run = is_dry_run()
    set_dry_run(config.dry_run)
    try:
        print(f"Running local maintenance: {label}")
        step(config)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        set_dry_run(previous_dry_run)


def _network_config(args: argparse.Namespace, *, dry_run: bool) -> SetupConfig:
    return _make_config(
        dry_run=dry_run,
        static_ipv4=getattr(args, "ipv4", None),
        static_ipv6=getattr(args, "ipv6", None),
        network_gateway4=getattr(args, "gateway", None),
        network_gateway6=getattr(args, "gateway6", None),
        network_dns=getattr(args, "dns", None),
        network_interface=getattr(args, "interface", None),
    )


def _add_network_arguments(parser: argparse.ArgumentParser, *, include_ipv4: bool) -> None:
    if include_ipv4:
        parser.add_argument(
            "address",
            nargs="?",
            help="Static IPv4 address in CIDR notation; omit to show current addresses",
        )
    else:
        parser.add_argument("--ip", dest="ipv4", help="Static IPv4 address in CIDR notation")
    parser.add_argument("--ipv6", help="Static IPv6 address in CIDR notation")
    parser.add_argument("--gateway", help="IPv4 default gateway")
    parser.add_argument("--gateway6", help="IPv6 default gateway")
    parser.add_argument(
        "--dns",
        action="append",
        help="DNS server address; repeat for multiple servers",
    )
    parser.add_argument(
        "--interface",
        help="Network interface; defaults to the default-route interface",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the action without changing the system",
    )


def add_local_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add focused local maintenance commands."""

    parser = subparsers.add_parser(
        "local",
        help="Maintain local Debian or inspect CachyOS desktop prerequisites",
        description=(
            "Run focused local maintenance operations. Mutating commands require "
            "root; use --dry-run where available to inspect the action first."
        ),
    )
    commands = parser.add_subparsers(dest="local_command", help="Local maintenance commands")

    doctor_parser = commands.add_parser(
        "cachyos-doctor", help="Read-only CachyOS desktop qualification prerequisites",
    )
    doctor_parser.add_argument("--json", action="store_true", help="Emit versioned capability metadata")

    install_parser = commands.add_parser("install", help="Install one or more APT packages")
    install_parser.add_argument("packages", nargs="+", metavar="PACKAGE")
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the packages without installing them",
    )

    update_parser = commands.add_parser("update", help="Refresh APT and upgrade installed packages")
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the update without changing the system",
    )

    desktop_parser = commands.add_parser("desktop", help="Install a desktop environment")
    desktop_parser.add_argument("environment", choices=LOCAL_DESKTOPS)
    desktop_parser.add_argument(
        "--dark",
        action="store_true",
        help="Apply the supported dark-theme configuration",
    )
    desktop_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the action without installing packages",
    )

    browser_parser = commands.add_parser("browser", help="Install a browser for the local desktop user")
    browser_parser.add_argument("browser", choices=LOCAL_BROWSERS)
    browser_parser.add_argument(
        "--flatpak",
        action="store_true",
        help="Prefer the Flatpak build when available",
    )
    browser_parser.add_argument(
        "--no-default",
        action="store_true",
        help="Do not make this the default browser",
    )
    browser_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the action without installing packages",
    )

    hostname_parser = commands.add_parser("hostname", help="Set the persistent local system hostname")
    hostname_parser.add_argument("name")
    hostname_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the action without changing the hostname",
    )

    ip_parser = commands.add_parser(
        "ip",
        help="Show addresses or configure a static IPv4 address",
        description=(
            "Configure a static IPv4 address and optional gateway/DNS settings. "
            "With no address, show the current interface addresses."
        ),
    )
    _add_network_arguments(ip_parser, include_ipv4=True)

    network_parser = commands.add_parser(
        "network",
        help="Configure static IPv4/IPv6 addresses and DNS",
    )
    _add_network_arguments(network_parser, include_ipv4=False)


def _show_local_addresses() -> int:
    result = run("ip -brief address", check=False, capture_output=True)
    if result.returncode != 0:
        print((result.stderr or "Unable to query local addresses").strip())
        return 1
    print((result.stdout or "").rstrip())
    return 0


def _run_install_command(args: argparse.Namespace) -> int:
    try:
        validate_apt_packages(args.packages)
        config = _make_config(dry_run=args.dry_run, apt_packages=list(args.packages))
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    return _run_step(config, "installing APT packages", install_apt_packages)


def _run_update_command(args: argparse.Namespace) -> int:
    config = _make_config(dry_run=args.dry_run)
    return _run_step(config, "updating and upgrading packages", update_and_upgrade_packages)


def _run_desktop_command(args: argparse.Namespace) -> int:
    config = _make_config(
        dry_run=args.dry_run,
        desktop=args.environment,
        dark_theme=args.dark,
        include_desktop=True,
    )
    steps: list[tuple[str, StepFunc]] = []
    extend_desktop_steps(config, steps)
    for label, step in steps:
        if _run_step(config, label, step) != 0:
            return 1
    return 0


def _run_browser_command(args: argparse.Namespace) -> int:
    config = _make_config(
        dry_run=args.dry_run,
        browser=args.browser,
        use_flatpak=args.flatpak,
    )
    if _run_step(config, f"installing {args.browser} browser", install_browser) != 0:
        return 1
    if args.no_default:
        return 0
    return _run_step(config, f"setting {args.browser} as the default browser", configure_default_browser)


def _run_hostname_command(args: argparse.Namespace) -> int:
    config = _make_config(dry_run=args.dry_run, system_hostname=args.name)
    return _run_step(config, f"setting hostname to {args.name}", configure_system_hostname)


def _run_ip_command(args: argparse.Namespace) -> int:
    if not args.address:
        if any(
            (
                getattr(args, "ipv6", None),
                args.gateway,
                args.gateway6,
                args.dns,
                args.interface,
                args.dry_run,
            )
        ):
            print("Error: an IPv4 address is required when configuring network options")
            return 1
        return _show_local_addresses()

    config = _make_config(
        dry_run=args.dry_run,
        static_ipv4=args.address,
        static_ipv6=args.ipv6,
        network_gateway4=args.gateway,
        network_gateway6=args.gateway6,
        network_dns=args.dns,
        network_interface=args.interface,
    )
    return _run_step(config, "staging static network configuration", configure_static_network)


def _run_network_command(args: argparse.Namespace) -> int:
    if not any((args.ipv4, args.ipv6)):
        print("Error: local network requires --ip or --ipv6")
        return 1
    config = _network_config(args, dry_run=args.dry_run)
    return _run_step(config, "staging static network configuration", configure_static_network)


def run_local_command(args: argparse.Namespace) -> int:
    """Dispatch a focused local maintenance command."""

    from lib.cachyos_doctor import run_cachyos_doctor

    handlers = {
        "cachyos-doctor": run_cachyos_doctor,
        "install": _run_install_command,
        "update": _run_update_command,
        "desktop": _run_desktop_command,
        "browser": _run_browser_command,
        "hostname": _run_hostname_command,
        "ip": _run_ip_command,
        "network": _run_network_command,
    }
    handler = handlers.get(getattr(args, "local_command", None))
    if handler is None:
        print(
            "Error: local command required "
            "(install, update, desktop, browser, hostname, ip, network, cachyos-doctor)"
        )
        return 1
    try:
        return handler(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
