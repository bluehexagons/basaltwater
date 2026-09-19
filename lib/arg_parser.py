#!/usr/bin/env python3

from __future__ import annotations

import argparse


from lib.config import (
    AGENT_TOOLS,
    BROWSER_AUTOMATION_PROVIDERS,
    WEB_PANEL_DEFAULT_PORT_SENTINEL,
    DEVICE_PAIRING_PROVIDERS,
    EDITORS,
    GIT_ACCESS_POLICIES,
    GODOT_BUNDLES,
    MACHINE_TYPES,
    WEB_INTERFACES,
)
from lib.notifications import NOTIFICATION_LEVELS


class CommaSeparatedChoicesAction(argparse.Action):
    """Append one or more comma-separated values while preserving order."""

    def __init__(self, option_strings, dest, allowed_values=(), **kwargs):
        self.allowed_values = tuple(allowed_values)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string: str | None = None,
    ) -> None:
        selected = list(getattr(namespace, self.dest, None) or [])
        for raw_value in str(values).split(","):
            value = raw_value.strip().lower()
            if not value:
                parser.error(f"{option_string or self.dest} cannot contain an empty value")
            if self.allowed_values and value not in self.allowed_values:
                choices = ", ".join(str(choice) for choice in self.allowed_values)
                parser.error(
                    f"argument {option_string or self.dest}: invalid choice: {value!r} "
                    f"(choose from {choices})"
                )
            if value not in selected:
                selected.append(value)
        setattr(namespace, self.dest, selected)


class RemovedDesktopPolicyAction(argparse.Action):
    """Explain the one-time migration instead of silently ignoring old flags."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(
            f"{option_string} was removed: desktops now use one shared session "
            "which persists until logout; remove this option"
        )


class VMDiskSettingAction(argparse.Action):
    """Set a VM-wide disk default or one logical disk override."""

    def __init__(self, option_strings, dest, setting: str, **kwargs):
        if setting not in {"discard", "ssd", "backup"}:
            raise ValueError(f"Unsupported VM disk setting: {setting}")
        self.setting = setting
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | None,
        option_string: str | None = None,
    ) -> None:
        del parser
        enabled = not str(option_string or "").startswith("--no-")
        if values is None:
            setattr(namespace, self.dest, enabled)
            return

        specs = [
            list(spec)
            for spec in getattr(namespace, "vm_disk_settings", None) or []
        ]
        matching = next((spec for spec in specs if spec and spec[0] == values), None)
        if matching is None:
            matching = [values]
            specs.append(matching)
        prefix = f"{self.setting}="
        matching[:] = [
            part
            for part in matching
            if not part.startswith(prefix)
        ]
        matching.append(f"{self.setting}={'on' if enabled else 'off'}")
        setattr(namespace, "vm_disk_settings", specs)


from lib.plugin_registry import get_system_type_names


class _DeploySpecActionBase(argparse.Action):
    """Base class for parsing deployment spec/repository pairs."""
    flag_name = "--deploy"
    set_deploy_latest = False

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | list[str],
        option_string: str | None = None,
    ) -> None:
        raw_values = [values] if isinstance(values, str) else values
        if len(raw_values) % 2 != 0:
            parser.error(f"{self.flag_name} requires DOMAIN_OR_PATH and GIT_URL pairs")

        if self.set_deploy_latest:
            setattr(namespace, "deploy_latest", True)

        deploy_specs = list(getattr(namespace, "deploy_specs", None) or [])
        for index in range(0, len(raw_values), 2):
            deploy_specs.append([raw_values[index], raw_values[index + 1]])
        setattr(namespace, "deploy_specs", deploy_specs)


class DeploySpecAction(_DeploySpecActionBase):
    """Parse deployment specs for --deploy flag."""
    pass


class DeployLatestSpecAction(_DeploySpecActionBase):
    """Parse deployment specs for --deploy-latest flag, also enabling latest-version policy."""
    flag_name = "--deploy-latest"
    set_deploy_latest = True


def add_setup_arguments(
    parser: argparse.ArgumentParser,
    for_remote: bool = False,
    allow_steps: bool = False,
    include_system_type: bool = False,
    include_host: bool = True,
) -> None:
    """Add the shared setup/patch argument surface to an argparse parser."""

    if not for_remote:
        if include_system_type:
            parser.add_argument(
                "system_type",
                choices=get_system_type_names(),
                help="Type of system to set up",
            )
        if include_host:
            parser.add_argument(
                "host",
                help=(
                    "IP address or hostname of the remote host; with --provision-on, "
                    "use IPv4 or IPv4/PREFIX (bare IPv4 defaults to /24)"
                ),
            )
            parser.add_argument("username", nargs="?", default=None,
                               help="Username (defaults to current user)")
        parser.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
        parser.add_argument(
            "--workspace",
            help="Workspace root for saved setups, credentials, known_hosts, and history"
        )
    
    parser.add_argument("-p", "--password", help="User password")
    parser.add_argument(
        "--nopasswd",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Enable or disable unrestricted passwordless sudo for the non-root "
            "VM setup user (capability/compatibility mode; disabled by default)"
        ),
    )
    parser.add_argument(
        "--harden-agent",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Remove privileged supplementary groups and apply the hardened "
            "coding-agent policy; cannot be combined with --nopasswd"
        ),
    )
    parser.add_argument(
        "--harden-user",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Also lock password login, make the home private, remove sensitive "
            "system-data and device groups, disable SSH forwarding/user rc, "
            "and disable user lingering; implies --harden-agent and cannot "
            "be combined with --rdp"
        ),
    )
    parser.add_argument("-t", "--timezone", help="Timezone (defaults to UTC)")
    parser.add_argument(
        "--hostname",
        dest="system_hostname",
        metavar="NAME",
        help="Set the target system hostname",
    )
    parser.add_argument(
        "--mdns",
        dest="enable_mdns",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Advertise the target hostname on the local network as NAME.local "
            "using Avahi/mDNS"
        ),
    )
    parser.add_argument(
        "--ip",
        dest="static_ipv4",
        metavar="ADDRESS/PREFIX",
        help="Configure a static IPv4 address in CIDR notation (for example, 192.168.1.10/24)",
    )
    parser.add_argument(
        "--ipv6",
        dest="static_ipv6",
        metavar="ADDRESS/PREFIX",
        help="Configure a static IPv6 address in CIDR notation",
    )
    parser.add_argument(
        "--gateway",
        dest="network_gateway4",
        metavar="IP",
        help="IPv4 default gateway; requires --ip or a --provision-on target",
    )
    parser.add_argument(
        "--gateway6",
        dest="network_gateway6",
        metavar="IP",
        help="IPv6 default gateway; requires --ipv6",
    )
    parser.add_argument(
        "--dns",
        dest="network_dns",
        action="append",
        metavar="IP",
        help="DNS server address; repeat for multiple IPv4 or IPv6 servers",
    )
    parser.add_argument(
        "--network-interface",
        dest="network_interface",
        metavar="INTERFACE",
        help="Interface to configure (default: interface carrying the default route)",
    )
    parser.add_argument(
        "--activate-network",
        action="store_true",
        help=(
            "Safely activate requested addresses during setup, verify SSH on each "
            "new address, and persist only after verification"
        ),
    )
    parser.add_argument("--machine", dest="machine_type",
                       choices=MACHINE_TYPES,
                       default=None,
                       help="Machine type override. Defaults to auto-detection "
                            "on the target; Proxmox provisioning defaults to a VM.")
    parser.add_argument(
        "--proxmox-balloon-target",
        dest="proxmox_balloon_target",
        type=int,
        default=argparse.SUPPRESS,
        metavar="PERCENT",
        help=(
            "Override the automatic Proxmox node balloon target (1-95; "
            "automatic policy reserves at least 20%% or 2 GiB)"
        ),
    )
    parser.add_argument(
        "--control-plane",
        action="store_true",
        help="Install common administrator and Linux control-plane tools in addition to the selected profile",
    )
    access_source_group = parser.add_mutually_exclusive_group()
    access_source_group.add_argument(
        "--access-source",
        dest="access_sources",
        action="extend",
        nargs="+",
        metavar="IP_OR_CIDR",
        help=(
            "Allow managed inbound services only from one or more IPs/CIDRs; "
            "accepts multiple values and may be repeated"
        ),
    )
    access_source_group.add_argument(
        "--no-access-source",
        dest="clear_access_sources",
        action="store_true",
        help="Clear saved generic access sources",
    )
    parser.add_argument(
        "--lan-access",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Allow managed inbound services from the target-adjacent private "
            "IPv4 /24 or IPv6 ULA /64 (an explicit static prefix is honored)"
        ),
    )

    parser.add_argument(
        "--storage",
        dest="container_storage",
        action="append",
        nargs="+",
        metavar="STORAGE",
        help=(
            "Guest storage: root [POOL] AMOUNT, NAME [POOL] AMOUNT for a VM "
            "data disk, or template [POOL] for LXC; repeat as needed"
            if not for_remote
            else argparse.SUPPRESS
        ),
    )
    parser.add_argument(
        "--storage-mount",
        dest="storage_mounts",
        action="append",
        nargs="+",
        metavar="MOUNT",
        help=(
            "Prepare a named VM data disk at an empty path: NAME PATH "
            "[ext4|xfs] [empty]; /home is supported for new VMs; repeat as needed"
            if not for_remote
            else argparse.SUPPRESS
        ),
    )
    parser.add_argument(
        "--storage-cache",
        dest="storage_caches",
        action="append",
        nargs="+",
        metavar="CACHE",
        help=(
            "Cache a named VM data disk with another named disk: "
            "DATA_NAME CACHE_NAME [writethrough|writeback]; cache disks are "
            "consumed by LVM and are not mounted separately"
            if not for_remote
            else argparse.SUPPRESS
        ),
    )
    parser.add_argument(
        "--swap-mode",
        choices=("auto", "preserve", "none"),
        default=argparse.SUPPRESS,
        help=(
            "Swap ownership policy: auto creates a root swap file only when no "
            "swap exists, preserve leaves areas alone, and none removes only "
            "Basaltwater-managed areas"
        ),
    )
    parser.add_argument(
        "--swap-file",
        dest="swap_files",
        action="append",
        nargs="+",
        metavar="SWAP",
        help="Managed swap file: NAME PATH SIZE [priority=N]; repeat as needed",
    )
    parser.add_argument(
        "--swap-device",
        dest="swap_devices",
        action="append",
        nargs="+",
        metavar="SWAP",
        help=(
            "Managed swap block device: NAME SOURCE [priority=N] "
            "[discard=off|once|pages|both]; SOURCE may name a declared VM disk"
        ),
    )
    parser.add_argument(
        "--swap-zram",
        dest="swap_zram",
        action="append",
        nargs="+",
        metavar="SWAP",
        help="Managed compressed-RAM swap: NAME SIZE [priority=N] [algorithm=TOKEN]",
    )
    parser.add_argument(
        "--swappiness",
        type=int,
        default=argparse.SUPPRESS,
        metavar="N",
        help="Linux vm.swappiness value (0-200)",
    )
    parser.add_argument(
        "--zswap",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="Enable or disable the Linux zswap compressed swap cache",
    )
    parser.add_argument(
        "--zswap-max-pool-percent",
        type=int,
        default=argparse.SUPPRESS,
        metavar="N",
        help="Maximum percentage of RAM used by zswap (1-50)",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--swap-resume",
        default=argparse.SUPPRESS,
        metavar="NAME",
        help="Use one managed swap device as the hibernation resume device",
    )
    resume_group.add_argument(
        "--no-swap-resume",
        dest="swap_resume",
        action="store_const",
        const="",
        default=argparse.SUPPRESS,
        help="Remove the managed hibernation resume-device configuration",
    )
    parser.add_argument(
        "--swap-initialize",
        action="append",
        default=argparse.SUPPRESS,
        metavar="NAME",
        help=(
            "Authorize initialization of a blank direct --swap-device once; "
            "named newly provisioned VM disks do not require this flag"
        ),
    )

    if not for_remote:
        parser.add_argument("--name", dest="friendly_name", help="Friendly name for this configuration")
        parser.add_argument("--tags", dest="tags", help="Comma-separated list of tags for this configuration")

        # Proxmox guest provisioning as part of the regular setup flow.
        parser.add_argument(
            "--provision-on",
            dest="hosted_node",
            metavar="HOST",
            help="Create the setup target on this Proxmox node or registered host",
        )
        parser.add_argument(
            "--provision-user",
            dest="hosted_user",
            default=argparse.SUPPRESS,
            metavar="USER",
            help="SSH user for Proxmox node (default: root)",
        )
        parser.add_argument(
            "--provision-key",
            dest="hosted_key",
            default=argparse.SUPPRESS,
            metavar="PATH",
            help="SSH key for Proxmox node (default: saved host key, --key, or SSH config)",
        )
        parser.add_argument(
            "--bridge",
            dest="hosted_bridge",
            metavar="NAME",
            help="Proxmox bridge for the new guest (default: node default-route bridge)",
        )
        parser.add_argument(
            "--memory",
            dest="container_memory",
            metavar="SIZE",
            help="Provisioned guest memory (e.g. 2G, 1.5G, 512M)",
        )
        parser.add_argument(
            "--balloon-min",
            dest="vm_balloon_min",
            metavar="SIZE",
            help=(
                "Provisioned VM balloon minimum (decimals allowed); defaults "
                "to --memory for fixed allocation"
            ),
        )
        parser.add_argument(
            "--balloon-shares",
            dest="vm_balloon_shares",
            type=int,
            default=argparse.SUPPRESS,
            metavar="N",
            help="Relative memory priority for a ballooned VM (1-50000; default: 1000)",
        )
        parser.add_argument(
            "--allow-memory-overcommit",
            action="store_true",
            default=argparse.SUPPRESS,
            help=(
                "Allow running VM memory floors to exceed the Proxmox node "
                "balloon target"
            ),
        )
        parser.add_argument(
            "--cores",
            dest="container_cores",
            type=int,
            default=argparse.SUPPRESS,
            metavar="N",
            help="Provisioned guest CPU cores (default: 1)",
        )
        parser.add_argument(
            "--cpu-type",
            dest="vm_cpu_type",
            default=argparse.SUPPRESS,
            metavar="MODEL",
            help=(
                "Proxmox VM CPU model (default: host; use a common x86-64-* "
                "model when migration compatibility is more important)"
            ),
        )
        parser.add_argument(
            "--disk-discard",
            "--no-disk-discard",
            dest="vm_disk_discard",
            action=VMDiskSettingAction,
            setting="discard",
            nargs="?",
            default=argparse.SUPPRESS,
            metavar="NAME",
            help=(
                "Expose discard/TRIM on provisioned VM disks; optionally name "
                "root or a declared data disk to override only that device "
                "(default: enabled)"
            ),
        )
        parser.add_argument(
            "--disk-ssd",
            "--no-disk-ssd",
            dest="vm_disk_ssd",
            action=VMDiskSettingAction,
            setting="ssd",
            nargs="?",
            default=argparse.SUPPRESS,
            metavar="NAME",
            help=(
                "Advertise provisioned VM disks as SSDs; optionally name root "
                "or a declared data disk to override only that device (default: "
                "disabled; enable only for SSD-like backing storage)"
            ),
        )
        parser.add_argument(
            "--disk-backup",
            "--no-disk-backup",
            dest="vm_disk_backup",
            action=VMDiskSettingAction,
            setting="backup",
            nargs="?",
            default=argparse.SUPPRESS,
            metavar="NAME",
            help=(
                "Include provisioned VM disks in Proxmox backups; optionally "
                "name root or a declared data disk (default: enabled for "
                "non-swap disks and disabled for swap disks)"
            ),
        )
        parser.add_argument(
            "--base",
            dest="container_base",
            default=argparse.SUPPRESS,
            metavar="NAME",
            help="Base OS family for the LXC template or VM image catalog (default: debian)",
        )
        parser.add_argument(
            "--image",
            dest="vm_image",
            default=None,
            metavar="SOURCE",
            help=(
                "VM cloud image override: HTTPS URL to a qcow2, or a Proxmox "
                "storage reference like 'local:import/foo.qcow2'. Used by VM "
                "provisioning. Defaults to the curated Debian catalog "
                "(lib/cloud_images.py)."
            ),
        )
        parser.add_argument(
            "--image-sha512",
            dest="vm_image_sha512",
            metavar="HEX",
            help=(
                "Expected SHA-512 for a custom HTTPS VM image (128 hexadecimal "
                "characters); required with --image SOURCE URL"
            ),
        )
        parser.add_argument(
            "--image-storage",
            dest="vm_image_storage",
            default=None,
            metavar="STORAGE",
            help=(
                "Storage for downloaded VM images; prefers the 'import' content "
                "type and falls back to 'iso' (default: auto-detect)"
            ),
        )
    else:
        parser.add_argument("--name", dest="friendly_name", default=None,
                           help="Friendly name for this configuration")
    
    if for_remote:
        parser.add_argument("--system-type", dest="system_type", 
                           choices=get_system_type_names(),
                           help="System type to setup")
        parser.add_argument("--username", default=None,
                           help="Username (defaults to current user, not used for server_proxmox)")
    
    if allow_steps or for_remote:
        parser.add_argument("--steps", dest="custom_steps", 
                           help="Space-separated list of steps to run (e.g., 'install_node install_python')")
    parser.add_argument("--rdp", dest="enable_rdp", 
                       action=argparse.BooleanOptionalAction, 
                       default=None if not for_remote else False,
                       help="Enable RDP/XRDP setup" + ("" if for_remote else " (default: disabled)"))
    parser.add_argument(
        "--rdp-existing-password",
        action="store_true",
        help="For a local setup, use the existing password of the existing desktop account without exposing it on the command line",
    )
    parser.add_argument(
        "--rdp-bind-address",
        default="0.0.0.0",
        metavar="IP",
        help="IP address XRDP listens on (default: 0.0.0.0)",
    )
    rdp_source_group = parser.add_mutually_exclusive_group()
    rdp_source_group.add_argument(
        "--rdp-source",
        dest="rdp_allowed_sources",
        action="append",
        metavar="IP_OR_CIDR",
        help="Allow RDP only from this IP or CIDR; repeat as needed. "
             "Without this flag, RDP remains globally reachable through UFW.",
    )
    rdp_source_group.add_argument(
        "--no-rdp-source",
        dest="clear_rdp_sources",
        action="store_true",
        help="Clear RDP source defaults and allow the normal unrestricted policy",
    )
    parser.add_argument(
        "--rdp-clipboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow RDP clipboard redirection (default: enabled)",
    )
    parser.add_argument(
        "--rdp-drive-redirection",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow RDP drive, printer, and device redirection (default: disabled)",
    )
    parser.add_argument(
        "--rdp-audio",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow RDP audio redirection (default: disabled)",
    )
    for flag in ("--rdp-max-sessions", "--rdp-disconnected-timeout"):
        parser.add_argument(flag, action=RemovedDesktopPolicyAction, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    for flag in ("--rdp-kill-disconnected", "--no-rdp-kill-disconnected"):
        parser.add_argument(flag, action=RemovedDesktopPolicyAction, nargs=0, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument(
        "--rdp-idle-timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Seconds before disconnecting an idle session; 0 disables (default: 0)",
    )
    parser.add_argument("--desktop", choices=["xfce", "i3", "cinnamon", "lxqt"], 
                       default=None,
                       help="Environment for the one shared desktop session (default: xfce)")
    parser.add_argument("--browser", dest="browsers", 
                       action="append",
                       choices=["brave", "firefox", "browsh", "helium", "lynx", "librewolf"], 
                       help="Web browser to install (can be used multiple times; profile defaults vary)")
    parser.add_argument(
        "--editor",
        choices=EDITORS,
        metavar="EDITOR",
        help="Install an explicit graphical editor (geany or vscode; desktop setups only)",
    )
    parser.add_argument("--flatpak", dest="use_flatpak", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install desktop apps via Flatpak when available (non-containerized environments)")
    parser.add_argument("--office", dest="install_office", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install LibreOffice (desktop only)")
    
    # Package installation
    parser.add_argument("--apt-install", dest="apt_packages",
                       action="append",
                       metavar="PACKAGE",
                       help="Install package via apt (can be used multiple times)")
    parser.add_argument("--flatpak-install", dest="flatpak_packages",
                       action="append",
                       metavar="PACKAGE",
                       help="Install package via flatpak (can be used multiple times)")
    
    # Theme customization
    parser.add_argument("--dark", dest="dark_theme",
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true",
                       default=None if not for_remote else False,
                       help="Configure desktop to use dark theme")
    parser.add_argument(
        "--refresh-packages",
        action="store_true",
        help=(
            "Refresh APT and explicitly update versioned runtimes; normally "
            "unchanged setup work is reused on reruns"
        ),
    )
    
    # Development tools
    parser.add_argument("--go", dest="install_go", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install latest Go version")
    parser.add_argument("--node", dest="install_node", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install nvm + latest Node.JS + PNPM + update NPM")
    parser.add_argument("--python", dest="install_python",
                        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
                        default=None if not for_remote else False,
                        help="Install Python tooling (python aliases and uv). For shell autocompletion, use the local completions installer script.")
    parser.add_argument(
        "--data-analysis",
        dest="install_data_analysis_tools",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Install the larger Python data-analysis bundle (NumPy, pandas, "
            "SciPy, Matplotlib, JupyterLab, and csvkit); also enables --python"
        ),
    )
    parser.add_argument(
        "--av-tools",
        dest="install_av_tools",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Install ImageMagick, FFmpeg (including ffprobe), and ExifTool for "
            "image, audio, video, and metadata processing"
        ),
    )
    parser.add_argument(
        "--gl-tools",
        dest="install_gl_tools",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help="Install Mesa OpenGL diagnostics and API tracing tools",
    )
    parser.add_argument(
        "--godot",
        dest="install_godot",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help="Install the latest stable Godot Engine for graphical and headless use",
    )
    parser.add_argument(
        "--sunshine",
        dest="install_sunshine",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help="Install the native CachyOS Sunshine game-stream host package",
    )
    parser.add_argument(
        "--moonlight",
        dest="install_moonlight",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help="Install the native CachyOS Moonlight Qt game-stream client package",
    )
    parser.add_argument(
        "--gaming",
        dest="install_gaming",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help="Install CachyOS's native gaming libraries, launchers, and tools",
    )
    for option, destination, description in (
        ("--obs", "install_obs", "Install the native CachyOS OBS Studio package"),
        ("--blender", "install_blender", "Install the native CachyOS Blender package"),
        ("--kdenlive", "install_kdenlive", "Install the native CachyOS Kdenlive package"),
        ("--krita", "install_krita", "Install the native CachyOS Krita package"),
        ("--inkscape", "install_inkscape", "Install the native CachyOS Inkscape package"),
        ("--scribus", "install_scribus", "Install the native CachyOS Scribus package"),
        ("--audacity", "install_audacity", "Install the native CachyOS Audacity package"),
        ("--ardour", "install_ardour", "Install the native CachyOS Ardour package"),
        ("--lmms", "install_lmms", "Install the native CachyOS LMMS package"),
        ("--freecad", "install_freecad", "Install the native CachyOS FreeCAD package"),
        ("--kicad", "install_kicad", "Install the native CachyOS KiCad package"),
        ("--shotcut", "install_shotcut", "Install the native CachyOS Shotcut package"),
        ("--gimp", "install_gimp", "Install the native CachyOS GIMP package"),
        ("--remmina", "install_remmina", "Install native CachyOS Remmina and common desktop plugins"),
        ("--sysadmin-tools", "install_sysadmin_tools", "Install native CachyOS sysadmin desktop and network tools"),
    ):
        parser.add_argument(
            option,
            dest=destination,
            action=argparse.BooleanOptionalAction if not for_remote else "store_true",
            default=None if not for_remote else False,
            help=description,
        )
    parser.add_argument(
        "--godot-bundle",
        dest="godot_bundles",
        action="append",
        choices=GODOT_BUNDLES,
        metavar="BUNDLE",
        help=(
            "Install a Godot workflow bundle; repeat as needed "
            "(currently: web, publishing; also enables --godot)"
        ),
    )

    # Agent VM tooling. Values add to narrow profile defaults; --no-agent-tool
    # provides an explicit opt-out for an individual default.
    parser.add_argument(
        "--agent-tool",
        dest="agent_tools",
        action=CommaSeparatedChoicesAction,
        allowed_values=AGENT_TOOLS,
        metavar="TOOL",
        help=(
            "Install one or more comma-separated agent tools; values add to "
            "agent-profile defaults"
        ),
    )
    parser.add_argument(
        "--no-agent-tool",
        dest="no_agent_tools",
        action=CommaSeparatedChoicesAction,
        allowed_values=AGENT_TOOLS,
        metavar="TOOL",
        help="Disable one or more default agent tools",
    )
    parser.add_argument(
        "--t3code-ready",
        dest="t3code_ready",
        action="store_true",
        default=False,
        help=(
            "Enable a headless T3 Code-ready profile with GitHub CLI, Codex, "
            "read-write Git, and device pairing"
        ),
    )
    web_interface_group = parser.add_mutually_exclusive_group()
    web_interface_group.add_argument(
        "--web-interface",
        dest="web_interfaces",
        action="append",
        choices=WEB_INTERFACES,
        metavar="INTERFACE",
        help=(
            "Install an explicit headless web interface; repeat as needed "
            "(currently: t3code)"
        ),
    )
    web_interface_group.add_argument(
        "--no-web-interface",
        dest="disable_web_interface",
        action="store_true",
        help="Disable profile-provided web interfaces",
    )
    parser.add_argument(
        "--web-interface-host",
        dest="web_interface_host",
        metavar="IP",
        help=(
            "Bind address for web interfaces (default: loopback; a non-loopback "
            "address requires --access-source or --web-interface-source)"
        ),
    )
    parser.add_argument(
        "--web-interface-port",
        dest="web_interface_port",
        type=int,
        default=3773,
        metavar="PORT",
        help="TCP port for web interfaces (default: 3773)",
    )
    web_source_group = parser.add_mutually_exclusive_group()
    web_source_group.add_argument(
        "--web-interface-source",
        dest="web_interface_sources",
        action="append",
        metavar="IP_OR_CIDR",
        help=(
            "Allow direct web-interface access only from this private/non-global source; repeat as "
            "needed. Required when binding outside loopback."
        ),
    )
    web_source_group.add_argument(
        "--no-web-interface-source",
        dest="clear_web_interface_sources",
        action="store_true",
        help="Clear web-interface source defaults",
    )
    parser.add_argument(
        "--web-port",
        dest="web_ports",
        action="append",
        type=int,
        metavar="PORT",
        help=(
            "Allow an additional global TCP web port through UFW; repeat as "
            "needed"
        ),
    )
    parser.add_argument(
        "--no-default-web-ports",
        dest="default_web_ports",
        action="store_false",
        default=True if for_remote else None,
        help=(
            "Do not automatically allow TCP ports 80, 443, 8080, and 8081 on "
            "agent VMs"
        ),
    )
    device_pairing_group = parser.add_mutually_exclusive_group()
    device_pairing_group.add_argument(
        "--device-pairing",
        dest="device_pairing_providers",
        action="append",
        choices=DEVICE_PAIRING_PROVIDERS,
        metavar="PROVIDER",
        help=(
            "Install a Basic-Auth-protected browser enrollment portal; repeat "
            "for future providers (currently: t3code)"
        ),
    )
    parser.add_argument(
        "--device-pairing-port",
        type=int,
        default=3774 if for_remote else None,
        metavar="PORT",
        help="TCP port for the protected device-pairing portal (default: 3774)",
    )
    parser.add_argument(
        "--device-pairing-payload",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    if not for_remote:
        device_pairing_group.add_argument(
            "--no-device-pairing",
            dest="disable_device_pairing",
            action="store_true",
            help=(
                "Remove the managed device-pairing portal and its Basic Auth "
                "credentials from a saved host"
            ),
        )
        parser.add_argument(
            "--device-pairing-auth-file",
            metavar="PATH",
            help=(
                "Controller-local Nginx htpasswd file used to protect the "
                "device-pairing portal"
            ),
        )
        parser.add_argument(
            "--device-pairing-password",
            dest="device_pairing_auth_password",
            metavar="PASSWORD",
            help=(
                "Controller-local portal password; transient and hashed before "
                "staging (the portal username defaults to the setup username)"
            ),
        )
    web_panel_group = parser.add_mutually_exclusive_group()
    broker_group = parser.add_mutually_exclusive_group()
    from lib.privilege_setup import DEFAULT_PRIVILEGE_BROKER_PORT
    broker_group.add_argument("--privilege-broker", nargs="?", const=DEFAULT_PRIVILEGE_BROKER_PORT,
                              type=int, metavar="PORT",
                              help="Enable browser-approved privileged operations; port defaults to 9444")
    broker_group.add_argument("--no-privilege-broker", dest="disable_privilege_broker", action="store_true",
                              help="Disable privileged requests and remove the approval listener; retain audit history")
    if for_remote:
        parser.add_argument("--privilege-broker-auth", help=argparse.SUPPRESS)
        parser.add_argument("--privilege-broker-host", help=argparse.SUPPRESS)
    else:
        parser.add_argument("--privilege-broker-password", nargs="?", const="", metavar="PASSWORD",
                            help="Set a separate approval password; omit the value to prompt securely")
    web_panel_group.add_argument(
        "--web-panel",
        dest="web_panel_port",
        nargs="?",
        const=WEB_PANEL_DEFAULT_PORT_SENTINEL,
        type=int,
        metavar="PORT",
        help=(
            "Install the authenticated Basaltwater web panel; the optional "
            "port defaults to 80, or 443 with --ssl"
        ),
    )
    web_panel_group.add_argument(
        "--no-web-panel",
        dest="disable_web_panel",
        action="store_true",
        help="Remove the managed web panel and its Basic Auth credentials",
    )
    parser.add_argument(
        "--web-panel-payload",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--web-panel-notification-ingest",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Enable the HTTPS bearer-token notification ingest endpoint at "
            "/api/v1/notifications"
        ),
    )
    if not for_remote:
        parser.add_argument(
            "--web-panel-password",
            dest="web_panel_auth_password",
            metavar="PASSWORD",
            help=(
                "Controller-local web panel password; transient and hashed "
                "before staging (the username is the setup username)"
            ),
        )
    browser_automation_group = parser.add_mutually_exclusive_group()
    browser_automation_group.add_argument(
        "--browser-automation",
        choices=BROWSER_AUTOMATION_PROVIDERS,
        metavar="PROVIDER",
        help="Install and register browser automation for selected compatible agents",
    )
    browser_automation_group.add_argument(
        "--no-browser-automation",
        dest="disable_browser_automation",
        action="store_true",
        help="Disable profile-provided browser automation",
    )
    parser.add_argument(
        "--git-access",
        choices=GIT_ACCESS_POLICIES,
        default=None if not for_remote else "none",
        help="Target VM Git policy (none, read, or read-write)",
    )
    parser.add_argument(
        "--git-host",
        default="github.com",
        metavar="HOST",
        help="GitHub CLI credential host (currently github.com only)",
    )
    parser.add_argument(
        "--git-credential",
        dest="git_credentials",
        action="append",
        nargs=2,
        metavar=("HTTPS_ORIGIN", "USERNAME"),
        help=(
            "Configure one origin-scoped HTTPS Git/LFS credential using the "
            "matching workspace password; repeat for additional origins"
        ),
    )
    if not for_remote:
        parser.add_argument(
            "--git-ca-certificate",
            dest="git_ca_certificates",
            action="append",
            nargs=2,
            metavar=("HTTPS_ORIGIN", "SOURCE"),
            help=(
                "Trust a local PATH or authenticated ssh://USERNAME@HOST/PATH "
                "PEM certificate bundle only for one Git HTTPS origin; repeatable"
            ),
        )
    parser.add_argument(
        "--git-ca-pem",
        dest="git_ca_pems",
        action="append",
        nargs=2,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-git-credentials",
        dest="clear_git_credentials",
        action="store_true",
        help=(
            "Remove all Basaltwater-managed HTTPS Git credentials and private "
            "CA settings from the target user"
        ),
    )
    parser.add_argument(
        "--agent-payload",
        dest="agent_payload",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--github-auth-payload",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--git-identity-payload",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    if not for_remote:
        git_auth_group = parser.add_mutually_exclusive_group()
        git_auth_group.add_argument(
            "--git-auth",
            dest="git_auth_source",
            choices=("active", "none"),
            help=(
                "Seed missing active GitHub CLI credentials, or none to "
                "disable profile defaults"
            ),
        )
        git_auth_group.add_argument(
            "--git-auth-file",
            dest="git_auth_file",
            metavar="PATH",
            help="Seed missing GitHub CLI credentials from a controller-local file",
        )
        agent_auth_group = parser.add_mutually_exclusive_group()
        agent_auth_group.add_argument(
            "--agent-auth",
            dest="agent_auth_source",
            choices=("login", "active", "none"),
            help=(
                "Authorize Codex on the target with a ChatGPT device code (login), "
                "import active controller credentials (active), or disable profile "
                "defaults (none); agent_code_vm defaults to login"
            ),
        )
        agent_auth_group.add_argument(
            "--agent-auth-file",
            dest="agent_auth_files",
            action="append",
            nargs=2,
            metavar=("TOOL", "PATH"),
            help=(
                "Stage one selected agent credential file, preserving existing "
                "auth except for safe stale-Codex refresh; repeat for different "
                "tools"
            ),
        )
        parser.add_argument(
            "--agent-config",
            dest="agent_config_source",
            choices=("active",),
            help="Copy optional non-secret agent config from the active controller user",
        )
        parser.add_argument(
            "--interactive",
            action="store_true",
            help="Choose tools, repositories, access, and credential sources interactively",
        )
    if for_remote:
        parser.add_argument("--agent-auth-mode", dest="agent_auth_source",
                            choices=("login", "check", "none"), default="none",
                            help=argparse.SUPPRESS)
    parser.add_argument("--repo", dest="agent_repos",
                       action="append",
                       metavar="GIT_URL",
                       help="Clone an HTTPS repository on the target VM; repeat as needed")
    parser.add_argument(
        "--git-lfs",
        dest="install_git_lfs",
        action="store_true",
        help="Install Git LFS and initialize it for the target user before repository clones",
    )
    parser.add_argument(
        "--agent-workspace",
        metavar="PATH",
        help=(
            "Directory in which agent repositories are cloned; defaults to "
            "the target user's ~/repos"
        ),
    )
    
    # Deployment options
    parser.add_argument("--deploy", dest="deploy_specs",
                       action=DeploySpecAction, nargs="+", metavar="DEPLOY",
                       help="Deploy git repositories as DOMAIN_OR_PATH GIT_URL pairs (can be used multiple times)")
    
    deployment_mode_group = parser.add_mutually_exclusive_group()
    deployment_mode_group.add_argument("--deployment-lite", dest="deployment_mode", action="store_const", const="lite",
                                       help="Use uploaded repository files only (skip if not available)")
    deployment_mode_group.add_argument("--deployment-full", dest="deployment_mode", action="store_const", const="full",
                                       help="Upload a fresh repository copy and rebuild everything (force redeploy)")
    
    parser.add_argument("--full-deploy", dest="full_deploy", action="store_true",
                       help="Always rebuild deployments even if they haven't changed (default: skip unchanged deployments)")
    parser.add_argument("--deploy-latest", action=DeployLatestSpecAction, nargs=2, 
                        metavar=("DOMAIN_OR_PATH", "GIT_URL"),
                        help="Deploy latest packages and releases (bypassing age policy); requires DOMAIN_OR_PATH and GIT_URL")
    parser.add_argument("--ssl", dest="enable_ssl", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Enable SSL/TLS: Let's Encrypt for deployed domains and a self-signed certificate for hostless Gogs")
    parser.add_argument("--ssl-email", dest="ssl_email",
                       help="Email address for Let's Encrypt registration (optional)")
    parser.add_argument("--cloudflare", dest="enable_cloudflare", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Preconfigure Cloudflare Tunnel; close public HTTP/HTTPS after activation")
    parser.add_argument("--cicd", dest="enable_cicd", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install webhook-based CI/CD system for GitHub Actions")
    
    parser.add_argument("--build-server", dest="is_build_server",
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true",
                       default=None if not for_remote else False,
                       help="Configure as a build server that deploys to app servers")
    
    parser.add_argument("--app-server", dest="is_app_server",
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true",
                       default=None if not for_remote else False,
                       help="Configure as a lightweight app server to receive deployments from build server")
    
    parser.add_argument("--deploy-target", dest="deploy_targets",
                       action="append",
                       metavar="HOST",
                       help="Target app server for deployments (can be used multiple times). Subsequent --deploy flags deploy to the last specified target")
    
    parser.add_argument("--samba", dest="enable_samba", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install and configure Samba for SMB file sharing")
    samba_source_group = parser.add_mutually_exclusive_group()
    samba_source_group.add_argument(
        "--samba-source",
        dest="samba_sources",
        action="append",
        metavar="IP_OR_CIDR",
        help=(
            "Allow Samba only from this IP or CIDR in addition to generic "
            "access sources; repeat as needed"
        ),
    )
    samba_source_group.add_argument(
        "--no-samba-source",
        dest="clear_samba_sources",
        action="store_true",
        help="Clear saved Samba-specific access sources",
    )
    samba_cache_group = parser.add_mutually_exclusive_group()
    samba_cache_group.add_argument(
        "--samba-metadata-cache",
        dest="samba_metadata_cache",
        metavar="PATH",
        help=(
            "Store Samba's disposable TDB metadata cache in this absolute "
            "directory (for example, on SSD storage)"
        ),
    )
    samba_cache_group.add_argument(
        "--no-samba-metadata-cache",
        dest="clear_samba_metadata_cache",
        action="store_true",
        help="Clear a saved custom Samba metadata-cache directory",
    )
    parser.add_argument("--share", dest="samba_shares", 
                       action="append", nargs=4, metavar=("ACCESS_TYPE", "SHARE_NAME", "PATH", "USERS"),
                       help="Configure one Samba directory share: access_type (read|write), share_name, absolute path, comma-separated username:password pairs or usernames that resolve via --credential (can be used multiple times)")
    parser.add_argument("--credential", dest="share_credentials",
                        action="append", nargs=2, metavar=("USERNAME", "PASSWORD"),
                        help="Save a workspace credential for a matching Gogs admin, Git HTTPS login, Samba share, or SMB mount username (can be used multiple times)")
    
    parser.add_argument("--smbclient", dest="enable_smbclient", 
                       action=argparse.BooleanOptionalAction if not for_remote else "store_true", 
                       default=None if not for_remote else False,
                       help="Install SMB/CIFS client packages for connecting to network shares (default: enabled for pc_dev)")
    
    parser.add_argument("--mount-smb", dest="smb_mounts",
                        action="append", nargs=5, metavar=("MOUNTPOINT", "IP", "CREDENTIALS", "SHARE", "SUBDIR"),
                        help="Mount SMB share: /mnt/path, ip_address, username or username:password, share_name, /share/subdirectory (can be used multiple times). Auto-enables --smbclient")

    syncthing_group = parser.add_mutually_exclusive_group()
    syncthing_group.add_argument(
        "--syncthing",
        dest="enable_syncthing",
        action="store_true",
        default=None if not for_remote else False,
        help=(
            "Install a managed Syncthing endpoint with an authenticated HTTPS "
            "admin UI"
        ),
    )
    syncthing_group.add_argument(
        "--no-syncthing",
        dest="disable_syncthing",
        action="store_true",
        help=(
            "Stop and remove the managed Syncthing service while preserving "
            "its identity, database, and synchronized files"
        ),
    )
    parser.add_argument(
        "--syncthing-admin",
        dest="syncthing_admin",
        default=None,
        metavar="USERNAME",
        help=(
            "Workspace credential username for the Syncthing web administrator "
            "(default: syncthing-admin)"
        ),
    )
    parser.add_argument(
        "--syncthing-root",
        dest="syncthing_root",
        default=None,
        metavar="PATH",
        help=(
            "Confine Syncthing folders to this storage root "
            "(default: /srv/syncthing)"
        ),
    )
    
    parser.add_argument("--sync", dest="sync_specs", 
                       action="append", nargs=3, metavar=("SOURCE", "DESTINATION", "INTERVAL"),
                       help="Configure directory synchronization: source_path, destination_path, interval (hourly|daily|weekly|biweekly|monthly|bimonthly). Uses rsync with systemd timer (can be used multiple times)")

    parser.add_argument(
        "--backup",
        dest="backup_specs",
        action="append",
        nargs=3,
        metavar=("SOURCE", "DESTINATION", "INTERVAL"),
        help=(
            "Mirror a standard path to a backup destination with the existing "
            "rsync storage service; repeat as needed"
        ),
    )
    
    parser.add_argument("--scrub", dest="scrub_specs",
                       action="append", nargs=4, metavar=("DIRECTORY", "DATABASE_PATH", "REDUNDANCY", "FREQUENCY"),
                       help="Configure data integrity checking: /path/to/directory, relative/or/absolute/path/to/.pardatabase, redundancy%%, frequency (hourly|daily|weekly|biweekly|monthly|bimonthly). Uses par2 with systemd timer (can be used multiple times)")
    
    parser.add_argument("--notify", dest="notify_specs",
                       action="append", nargs=2, metavar=("TYPE", "TARGET"),
                       help="Configure notification target: TYPE (webhook|mailbox), TARGET (URL for webhook or email for mailbox). Sends alerts for important events (errors, warnings, successes). Can be used multiple times for multiple targets.")
    parser.add_argument(
        "--notification-level",
        choices=NOTIFICATION_LEVELS,
        help=(
            "Set outbound notification volume: verbose sends every produced event; "
            "normal keeps each job's default; warning or error applies a severity "
            "threshold while retaining recovery events; off disables delivery"
        ),
    )
    parser.add_argument(
        "--notification-strict-https",
        action=argparse.BooleanOptionalAction if not for_remote else "store_true",
        default=None if not for_remote else False,
        help=(
            "Require certificate and hostname verification for HTTPS notification "
            "webhooks (default accepts self-signed certificates)"
        ),
    )
    
    parser.add_argument("--antistatic-server", dest="antistatic_server",
                        metavar="[DOMAIN][:PORT]",
                        help="Deploy the antistatic lobby server behind nginx. "
                            "DOMAIN is the optional public hostname; PORT is the internal listen port "
                            f"(default: 8080). Hostless specs like :8080 listen directly without nginx. "
                             "The built-in STUN responder uses direct public UDP 3478 even when Cloudflare tunnel "
                             "support is enabled.")

    antistatic_admin_group = parser.add_mutually_exclusive_group()
    antistatic_admin_group.add_argument(
        "--antistatic-admin",
        dest="antistatic_admin",
        metavar="USERNAME",
        help="Enable the HTTPS-only antistatic-server admin interface. Resolve the password "
             "from --credential USERNAME PASSWORD or the workspace credential store.",
    )
    antistatic_admin_group.add_argument(
        "--no-antistatic-admin",
        dest="antistatic_admin",
        action="store_const",
        const="",
        help="Disable the antistatic-server admin interface and remove its remote credentials.",
    )

    parser.add_argument("--antistatic-db", dest="antistatic_db",
                       metavar="[DOMAIN][:PORT]",
                       help="Deploy the antistatic-db service behind nginx. "
                            "DOMAIN is the optional public hostname; PORT is the internal listen port "
                            "(default: 8081). Hostless specs like :8081 listen directly without nginx.")

    parser.add_argument(
        "--gogs",
        dest="gogs",
        nargs="+",
        metavar="GOGS",
        help="Deploy Gogs as a minimal self-hosted Git service. "
             "Usage: --gogs DOMAIN[:PORT] [DATA_PATH]. DOMAIN is the optional public "
             "hostname, PORT is the direct public HTTP/HTTPS port and defaults to "
             "3000 (with --cloudflare it remains the private backend port), and "
             "DATA_PATH defaults to /var/lib/gogs. "
             "Hostless specs bind to loopback unless --gogs-source is repeated.",
    )
    parser.add_argument(
        "--gogs-source",
        dest="gogs_sources",
        action="append",
        metavar="IP_OR_CIDR",
        help=(
            "Allow a trusted private IPv4 source to reach a hostless Gogs port; "
            "repeat as needed"
        ),
    )
    
    homebox_group = parser.add_mutually_exclusive_group()
    homebox_group.add_argument("--homebox", nargs="+", metavar="HOMEBOX",
                              help="Install native HomeBox: DOMAIN[:PORT] [DATA_PATH]; hostname requires --ssl, :PORT is loopback-only")
    homebox_group.add_argument("--no-homebox", dest="homebox", action="store_const", const=[],
                              help="Remove the managed HomeBox unit and proxy, preserving inventory, secrets, and releases")
    parser.add_argument("--homebox-version", metavar="TAG", help="Explicit HomeBox version (new installs: v0.26.2; reruns retain installed version)")
    parser.add_argument("--homebox-admin", metavar="EMAIL", help="Initial HomeBox account email (default: admin@homebox.local); password generated on target")
    parser.add_argument("--homebox-port", type=int, metavar="PORT", help="Loopback backend port for hostname-based HomeBox (default: 7745)")

    restart_group = parser.add_mutually_exclusive_group()
    restart_group.add_argument("--auto-restart", dest="auto_restart",
                               action="store_true",
                               default=None if not for_remote else False,
                               help="Allow automatic restarts after updates when no users are active.")
    restart_group.add_argument("--no-auto-restart", "--no-restart", dest="auto_restart",
                               action="store_false",
                               help="Disable normal automatic restarts after updates. --no-restart is deprecated.")
    parser.add_argument("--auto-restart-force-days", dest="auto_restart_force_days",
                        type=int,
                        default=None,
                        help="Force restart after this many days of deferral. Use 0 to never force.")
    parser.add_argument("--auto-restart-grace", dest="auto_restart_grace",
                        type=int,
                        default=None,
                        help="Minutes of warning before an automatic restart starts.")
    
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be done without executing commands")


def create_setup_argument_parser(
    description: str,
    for_remote: bool = False,
    allow_steps: bool = False
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    add_setup_arguments(parser, for_remote=for_remote, allow_steps=allow_steps)
    return parser
