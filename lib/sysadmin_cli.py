"""CLI subparser registration and dispatch for sysadmin convenience commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys


class SysadminArgumentParser(argparse.ArgumentParser):
    """Separate remote argv before argparse can consume it as host operands."""

    def parse_known_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> tuple[argparse.Namespace, list[str]]:
        cmd = self.get_default("_sysadmin_cmd")
        if cmd not in {"ssh", "fan"} or getattr(self, "_connection_parse", False):
            return super().parse_known_args(args, namespace)
        words = list(sys.argv[1:] if args is None else args)
        explicit = cmd == "fan" and "--" in words
        boundary = words.index("--") if explicit else len(words)
        if cmd == "ssh":
            # The first command word after the host starts the legacy direct
            # command form. Once started, its options belong to that command.
            host_seen = False
            index = 0
            while index < len(words):
                word = words[index]
                if word == "--":
                    explicit = True
                    boundary = index
                    break
                option = word.split("=", 1)[0]
                action = self._option_string_actions.get(option)
                if action is None and self.allow_abbrev and option.startswith("--"):
                    matches = [
                        value for name, value in self._option_string_actions.items()
                        if name.startswith(option)
                    ]
                    if len(matches) == 1:
                        action = matches[0]
                if action is None and word[:2] in {"-u", "-i", "-p"}:
                    action = self._option_string_actions[word[:2]]
                    option = word[:2]
                if action is not None:
                    index += 1
                    if action.nargs != 0 and word == option:
                        index += 1
                elif word.startswith("-"):
                    self.error(f"unrecognized arguments: {word}")
                elif host_seen:
                    boundary = index
                    break
                else:
                    host_seen = True
                    index += 1
        prefix = words[:boundary]
        remote = words[boundary + 1:] if explicit else words[boundary:]
        # Intermixed parsing allows connection options between fan hosts too.
        # Its two internal parse_known_args calls must use ordinary argparse.
        self._connection_parse = True
        try:
            parsed, unknown = self.parse_known_intermixed_args(prefix, namespace)
        finally:
            self._connection_parse = False
        parsed.remote_command = remote
        parsed._remote_delimiter = explicit
        return parsed, unknown


# ---------------------------------------------------------------------------
# mount / umount
# ---------------------------------------------------------------------------

def _add_mount_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "mount",
        help="Mount a remote directory via sshfs",
        description=(
            "Mount a remote directory using sshfs. If the host has a saved "
            "basaltwater config, username/key are inherited automatically."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  basaltw mount myserver:/var/log /mnt/myserver-logs",
    )
    p.add_argument("remote", metavar="host:path", help="Remote host and path (e.g. myhost:/srv/data)")
    p.add_argument("local_path", metavar="local_path", help="Local mountpoint (created if absent)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file (overrides saved config)")
    p.add_argument("--port", "-p", type=int, help="SSH port")
    p.add_argument("--ro", action="store_true", help="Mount read-only")
    p.set_defaults(_sysadmin_cmd="mount")


def _add_umount_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "umount",
        help="Unmount an sshfs mount",
        description="Unmount an sshfs mount by local path or by host name.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  basaltw umount /mnt/myserver-logs\n  basaltw umount myhost",
    )
    p.add_argument(
        "target",
        help="Local mountpoint path or host name to unmount",
    )
    p.set_defaults(_sysadmin_cmd="umount")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def _add_health_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "health",
        help="Show a health summary for a remote host",
        description=(
            "SSH into a host and print uptime, memory, disk usage, failed systemd "
            "units, recent journal errors, pending apt upgrades, and reboot status."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  basaltw health myserver",
    )
    p.add_argument("host", help="Remote host (IP or hostname)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file (overrides saved config)")
    p.set_defaults(_sysadmin_cmd="health")


# ---------------------------------------------------------------------------
# ssh
# ---------------------------------------------------------------------------

def _add_ssh_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "ssh",
        help="Open an SSH session using saved config",
        description=(
            "Open an interactive SSH session (or run a remote command) using the "
            "username, key, and port from the saved basaltwater config for the host."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw ssh myserver\n"
            "  basaltw ssh myserver -- journalctl -f"
        ),
    )
    p.add_argument("host", help="Remote host (IP or hostname)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file (overrides saved config)")
    p.add_argument("--port", "-p", type=int, help="SSH port")
    p.usage = "%(prog)s [options] host [-- command ...]"
    p.set_defaults(_sysadmin_cmd="ssh")


# ---------------------------------------------------------------------------
# push / pull
# ---------------------------------------------------------------------------

def _add_push_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "push",
        help="Push a local path to a remote host via rsync",
        description=(
            "Sync a local file or directory to a remote host using rsync over SSH. "
            "Username and key are inherited from the saved config when available."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw push ./dist myserver:/var/www/app\n"
            "  basaltw push ./data myserver:/backup/data --delete --dry-run"
        ),
    )
    p.add_argument("local_path", help="Local source path")
    p.add_argument("remote", metavar="host:path", help="Remote destination (e.g. myhost:/srv/app)")
    p.add_argument("--username", "-u", help="SSH username")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.add_argument("--port", "-p", type=int, help="SSH port")
    p.add_argument("--delete", action="store_true", help="Delete extraneous files from dest")
    p.add_argument("--dry-run", "-n", action="store_true", help="Show what would be transferred")
    p.set_defaults(_sysadmin_cmd="push")


def _add_pull_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "pull",
        help="Pull a remote path to local via rsync",
        description=(
            "Sync a remote file or directory to a local path using rsync over SSH."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw pull myserver:/var/log ./logs\n"
            "  basaltw pull myserver:/srv/data"
        ),
    )
    p.add_argument("remote", metavar="host:path", help="Remote source (e.g. myhost:/var/log)")
    p.add_argument("local_path", nargs="?", help="Local destination (defaults to ./<basename>)")
    p.add_argument("--username", "-u", help="SSH username")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.add_argument("--port", "-p", type=int, help="SSH port")
    p.add_argument("--dry-run", "-n", action="store_true", help="Show what would be transferred")
    p.set_defaults(_sysadmin_cmd="pull")


# ---------------------------------------------------------------------------
# key
# ---------------------------------------------------------------------------

def _add_key_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "key",
        help="Manage SSH keys on remote hosts",
        description="Install or rotate SSH public keys on remote hosts.",
    )
    key_sub = p.add_subparsers(dest="key_command", help="Key command")

    push_p = key_sub.add_parser(
        "push",
        help="Install local public key on a remote host",
        description=(
            "Append the local public key to ~/.ssh/authorized_keys on the remote "
            "host, creating the file with correct permissions if needed. Idempotent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  basaltw key push myserver",
    )
    push_p.add_argument("host", help="Remote host (IP or hostname)")
    push_p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    push_p.add_argument("--key", "-i", dest="ssh_key", help="SSH key to authenticate with")
    push_p.add_argument(
        "--pubkey",
        default="~/.ssh/id_ed25519.pub",
        help="Public key to install (default: ~/.ssh/id_ed25519.pub)",
    )
    push_p.set_defaults(_sysadmin_cmd="key_push")

    p.set_defaults(_sysadmin_cmd="key")


def _add_ssh_key_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "ssh-key",
        help="Enroll and inspect workspace SSH host keys",
        description=(
            "Enroll a remote host key into the workspace known_hosts file after "
            "displaying its fingerprint for operator verification."
        ),
    )
    key_sub = p.add_subparsers(dest="ssh_key_command", help="SSH host-key command")
    enroll = key_sub.add_parser(
        "enroll",
        help="Enroll a host key after verification",
        epilog="Example:\n  basaltw ssh-key enroll myserver",
    )
    enroll.add_argument("host", help="Remote host (IP or hostname)")
    enroll.add_argument("--port", "-p", type=int, default=22, help="SSH port")
    enroll.add_argument(
        "--yes",
        action="store_true",
        help="Trust the displayed fingerprint without prompting",
    )
    enroll.add_argument(
        "--fingerprint",
        metavar="SHA256:FINGERPRINT",
        help="Enroll without prompting only when this verified fingerprint matches",
    )
    enroll.set_defaults(_sysadmin_cmd="ssh_key_enroll")
    p.set_defaults(_sysadmin_cmd="ssh_key")


# ---------------------------------------------------------------------------
# Public registration
# ---------------------------------------------------------------------------

def _add_df_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "df",
        help="Show disk usage across one or more remote hosts",
        description=(
            "Run df on one or more remote hosts in parallel and print a combined "
            "table sorted by usage, with entries over 85% highlighted."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw df myserver\n"
            "  basaltw df web1 web2 db1 --username admin"
        ),
    )
    p.add_argument("hosts", nargs="+", help="Remote hosts (IP or hostname)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.set_defaults(_sysadmin_cmd="df")


def _add_fan_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "fan",
        help="Run a command on multiple hosts in parallel",
        description=(
            "SSH into multiple hosts concurrently and run a shell command, printing "
            "each host's output with a header and a pass/fail summary at the end."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw fan web1 web2 -- uptime\n"
            "  basaltw fan web1 web2 db1 -- systemctl restart myapp"
        ),
    )
    p.add_argument("hosts", nargs="+", help="Remote hosts (IP or hostname)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.usage = "%(prog)s [options] hosts [hosts ...] -- command ..."
    p.set_defaults(_sysadmin_cmd="fan")


def _add_svc_parser(sub: argparse._SubParsersAction) -> None:
    from lib.sysadmin_svc import VALID_ACTIONS
    p = sub.add_parser(
        "svc",
        help="Manage a systemd service on a remote host",
        description=(
            "Run systemctl actions on a remote service. Non-status actions use sudo "
            "and show a status readout afterward."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw svc myserver nginx\n"
            "  basaltw svc myserver nginx restart\n"
            "  basaltw svc myserver myapp.service stop"
        ),
    )
    p.add_argument("host", help="Remote host (IP or hostname)")
    p.add_argument("unit", help="Systemd unit name (e.g. nginx, myapp.service)")
    p.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=VALID_ACTIONS,
        help="Action to perform (default: status)",
    )
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.set_defaults(_sysadmin_cmd="svc")


def _add_logs_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "logs",
        help="Show or follow journalctl logs for a remote service",
        description="Print recent journal entries for a systemd unit on a remote host.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw logs myserver nginx\n"
            "  basaltw logs myserver myapp -f\n"
            "  basaltw logs myserver nginx -n 100"
        ),
    )
    p.add_argument("host", help="Remote host (IP or hostname)")
    p.add_argument("unit", help="Systemd unit name")
    p.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to show (default: 50)")
    p.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.set_defaults(_sysadmin_cmd="logs")


def _add_upgrade_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "upgrade",
        help="Upgrade basaltwater, or run apt upgrade on remote hosts",
        description=(
            "With no hosts, upgrade the installed basaltwater source on its selected "
            "channel. With one or more hosts, run apt-get update && apt-get upgrade "
            "on each host in parallel, then report which hosts need a reboot."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "  basaltw upgrade\n"
            "\n"
            "Examples:\n"
            "  basaltw upgrade myserver\n"
            "  basaltw upgrade web1 web2 db1\n"
            "  basaltw upgrade web1 --with-new-pkgs --check"
        ),
    )
    p.add_argument("hosts", nargs="*", help="Remote hosts (IP or hostname)")
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.add_argument("--check", action="store_true", help="Simulate using cached APT lists and report pending counts")
    p.add_argument(
        "--with-new-pkgs", action="store_true",
        help="Allow new dependencies, such as kernel packages, without removing installed packages",
    )
    p.set_defaults(_sysadmin_cmd="upgrade")


def _add_reachable_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "reachable",
        help="Check which saved hosts are reachable via SSH",
        description=(
            "Probe saved hosts in parallel and print a reachability table with "
            "latency. With no arguments checks all saved hosts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw reachable\n"
            "  basaltw reachable '*.example.com'\n"
            "  basaltw reachable web1 web2 db1"
        ),
    )
    p.add_argument(
        "hosts",
        nargs="*",
        help="Hosts to probe (default: all saved hosts)",
    )
    p.add_argument(
        "--pattern",
        help="Glob pattern to filter saved hosts (e.g. '*.example.com')",
    )
    p.add_argument("--username", "-u", help="SSH username (overrides saved config)")
    p.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    p.set_defaults(_sysadmin_cmd="reachable")


def _add_rolling_update_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rolling-update",
        help="Apply point updates to Debian and Proxmox targets one at a time",
        description=(
            "Preflight saved Debian and Proxmox targets, then update them in the supplied "
            "order. Reboot when required and verify each target before advancing."
        ),
    )
    p.add_argument("targets", nargs="+", help="Saved setup names or addresses, in update order")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Check readiness and show cached APT updates")
    mode.add_argument("--resume", action="store_true", help="Resume the workspace checkpoint with the same targets")
    p.add_argument("--reboot-timeout", type=int, default=300, metavar="SECONDS",
                   help="Time to wait for a new boot ID per target (default 300)")
    p.set_defaults(_sysadmin_cmd="rolling-update")


def _add_user_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "user",
        help="Manage the configured login account on a remote host",
        description=(
            "Run administrator operations for the target account. Renaming "
            "a user logs out the target account, moves its conventional home, "
            "reconciles basaltwater-managed configuration, and updates the "
            "saved controller configuration after verification."
        ),
    )
    commands = p.add_subparsers(dest="user_command", help="User command")
    rename = commands.add_parser(
        "rename",
        help="Rename the configured target user",
        description=(
            "Rename the saved target login through a detached, root-owned "
            "remote migration job. The target user will be logged out."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  basaltw user rename myserver newadmin --yes\n"
            "  basaltw user rename myserver newadmin --admin-user root\n"
            "  basaltw user rename myserver newadmin --dry-run\n"
            "  basaltw user rename myserver newadmin --resume OPERATION_ID"
        ),
    )
    rename.add_argument("host", help="Remote host with a saved setup")
    rename.add_argument("new_username", help="New login name")
    rename.add_argument(
        "--admin-user",
        help="Administrative SSH username (defaults to the saved target user)",
    )
    rename.add_argument("--key", "-i", dest="ssh_key", help="SSH identity file")
    home = rename.add_mutually_exclusive_group()
    home.add_argument("--new-home", help="Absolute destination home directory")
    home.add_argument(
        "--keep-home",
        action="store_true",
        help="Keep the existing home path instead of moving /home/OLD",
    )
    rename.add_argument("--dry-run", "-n", action="store_true", help="Run preflight only")
    rename.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    rename.add_argument("--resume", metavar="OPERATION_ID", help="Resume a staged operation")
    rename.set_defaults(_sysadmin_cmd="user_rename")
    p.set_defaults(_sysadmin_cmd="user")


def add_sysadmin_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Register all sysadmin convenience subcommands."""
    _add_mount_parser(subparsers)
    _add_umount_parser(subparsers)
    _add_health_parser(subparsers)
    _add_ssh_parser(subparsers)
    _add_push_parser(subparsers)
    _add_pull_parser(subparsers)
    _add_key_parser(subparsers)
    _add_ssh_key_parser(subparsers)
    _add_df_parser(subparsers)
    _add_fan_parser(subparsers)
    _add_svc_parser(subparsers)
    _add_logs_parser(subparsers)
    _add_upgrade_parser(subparsers)
    _add_rolling_update_parser(subparsers)
    _add_reachable_parser(subparsers)
    _add_user_parser(subparsers)


def run_sysadmin_command(args: argparse.Namespace) -> int:
    """Dispatch a sysadmin subcommand."""
    cmd = getattr(args, "_sysadmin_cmd", None)

    if cmd == "mount":
        from lib.sysadmin_mount import run_mount
        return run_mount(
            args.remote,
            args.local_path,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            port=getattr(args, "port", None),
            read_only=args.ro,
        )

    if cmd == "umount":
        from lib.sysadmin_mount import run_umount
        return run_umount(args.target)

    if cmd == "health":
        from lib.sysadmin_health import run_health
        return run_health(
            args.host,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "ssh":
        from lib.sysadmin_ssh import run_ssh
        remote_command = getattr(args, "remote_command", [])
        if getattr(args, "_remote_delimiter", False) and not remote_command:
            print("Error: a remote command is required after --", file=sys.stderr)
            return 1
        return run_ssh(
            args.host,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            port=getattr(args, "port", None),
            remote_command=remote_command or None,
        )

    if cmd == "push":
        from lib.sysadmin_transfer import run_push
        return run_push(
            args.local_path,
            args.remote,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            port=getattr(args, "port", None),
            delete=args.delete,
            dry_run=args.dry_run,
        )

    if cmd == "pull":
        from lib.sysadmin_transfer import run_pull
        return run_pull(
            args.remote,
            getattr(args, "local_path", None),
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            port=getattr(args, "port", None),
            dry_run=args.dry_run,
        )

    if cmd == "key_push":
        from lib.sysadmin_keys import run_key_push
        return run_key_push(
            args.host,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            pubkey_path=args.pubkey,
        )

    if cmd == "ssh_key_enroll":
        from lib.ssh_enrollment import enroll_host_key
        try:
            return enroll_host_key(
                args.host, port=args.port, assume_yes=args.yes,
                expected_fingerprint=args.fingerprint,
            )
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1

    if cmd == "key":
        print("Error: key subcommand required (push)", file=sys.stderr)
        return 1

    if cmd == "df":
        from lib.sysadmin_fan import run_df
        return run_df(
            args.hosts,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "fan":
        from lib.sysadmin_fan import run_fan
        remote_command = getattr(args, "remote_command", [])
        if not remote_command:
            print("Error: a remote command is required after --", file=sys.stderr)
            return 1
        return run_fan(
            args.hosts,
            remote_command,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "svc":
        from lib.sysadmin_svc import run_svc
        return run_svc(
            args.host,
            args.unit,
            action=args.action,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "logs":
        from lib.sysadmin_svc import run_logs
        return run_logs(
            args.host,
            args.unit,
            lines=args.lines,
            follow=args.follow,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "upgrade":
        from lib.sysadmin_upgrade import run_upgrade
        return run_upgrade(
            args.hosts,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
            check_only=args.check,
            with_new_pkgs=getattr(args, "with_new_pkgs", False),
        )

    if cmd == "rolling-update":
        from lib.cluster_update import run_cluster_update
        try:
            return run_cluster_update(
                args.targets, workspace=getattr(args, "workspace", None),
                dry_run=args.dry_run, reboot_timeout=args.reboot_timeout,
                resume=args.resume, include_debian=True,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if cmd == "reachable":
        from lib.sysadmin_reachable import run_reachable
        explicit_hosts = getattr(args, "hosts", []) or None
        return run_reachable(
            pattern=getattr(args, "pattern", None),
            hosts=explicit_hosts,
            username=getattr(args, "username", None),
            ssh_key=getattr(args, "ssh_key", None),
        )

    if cmd == "user_rename":
        from lib.sysadmin_user import run_user_rename
        return run_user_rename(
            args.host,
            args.new_username,
            admin_user=getattr(args, "admin_user", None),
            ssh_key=getattr(args, "ssh_key", None),
            new_home=getattr(args, "new_home", None),
            keep_home=getattr(args, "keep_home", False),
            dry_run=getattr(args, "dry_run", False),
            assume_yes=getattr(args, "yes", False),
            resume=getattr(args, "resume", None),
        )

    print(f"Error: unknown sysadmin command {cmd!r}", file=sys.stderr)
    return 1
