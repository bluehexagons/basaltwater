#!/usr/bin/env python3
"""Top-level interactive REPL for basaltwater.

Provides a small command loop that wraps the saved-configuration helpers
(``list``/``info``/``cmd``/``deploy``/``rm``), the recall/reconstruct
flows, and a handoff into the existing :class:`lib.proxmox_shell.ProxmoxShell`.

The shell is intentionally driver-agnostic: tests construct a shell with
custom ``input_func``/``output_func`` callables to drive sequences
without a TTY.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

try:
    import readline as _readline
    _READLINE_AVAILABLE = True
except ImportError:
    _readline = None  # type: ignore[assignment]
    _READLINE_AVAILABLE = False

_HISTORY_FILE = Path.home() / ".local" / "share" / "basaltwater" / "shell_history"
_HISTORY_MAX_LINES = 1000
_INIT_FILE = Path.home() / ".basaltwaterrc"

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


HELP_TEXT = """\
Available commands:
  list/ls [pattern] [--json]        List saved configurations (--json for scripting)
  info [pattern] [--compact]        Show saved configuration details (--compact: one line each)
  cmd [pattern]                     Show reconstructed setup commands
  new/setup                         Guided flow to create a new saved setup
  rename <pattern> <new-name>       Rename (re-label) a saved configuration
  clone <pattern> <new-host> [name] Copy a saved configuration to a new host
  tag <pattern> <tag> [tag ...]     Add tags to a saved configuration
  untag <pattern> <tag> [tag ...]   Remove tags from a saved configuration
  deploy <pattern> [--yes] [--deploy-latest]  Redeploy saved configurations
  rm/remove <pattern> [--yes]       Remove saved configurations
  recall <host> [user]              Fetch/reconstruct a remote setup command
  reconstruct [--compact]           Reconstruct local host configuration
  proxmox                           Drop into the proxmox shell
  workspace [path]                  Show or change the active workspace
  help                              Show this help text
  quit/exit                         Leave the shell
"""


@dataclass
class ShellState:
    workspace: Optional[str] = None
    history: list[str] = field(default_factory=list)


class InteractiveShell:
    """Small REPL wrapping the main basaltwater management surface."""

    def __init__(
        self,
        *,
        workspace: Optional[str] = None,
        input_func: InputFn = input,
        output_func: OutputFn = print,
    ) -> None:
        self.state = ShellState(workspace=workspace)
        self._input = input_func
        self._output = output_func
        if workspace is not None:
            from lib.workspace import set_workspace_dir

            set_workspace_dir(workspace)

    # ------------------------------------------------------------------
    # Public entry points

    def run(self) -> int:
        """Drive the REPL until the user quits or input ends."""
        self._load_readline_history()
        self._run_init_file()
        self._output("basaltw shell — type 'help' for commands.")
        try:
            return self._run_loop()
        finally:
            self._save_readline_history()

    def _run_loop(self) -> int:
        while True:
            try:
                raw = self._input(self._make_prompt())
            except (EOFError, KeyboardInterrupt):
                self._output("")
                return 0
            line = raw.strip()
            if not line:
                continue
            self.state.history.append(line)
            if line in {"quit", "exit"}:
                return 0
            try:
                self.dispatch(line)
            except ValueError as exc:
                self._output(f"Error: {exc}")

    def dispatch(self, line: str) -> None:
        """Parse and execute a single shell command line."""
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            raise ValueError(f"Could not parse command: {exc}")
        if not tokens:
            return
        cmd, *args = tokens
        handler = self._handlers().get(cmd)
        if handler is None:
            raise ValueError(
                f"Unknown command '{cmd}'. Type 'help' for the command list."
            )
        handler(args)

    # ------------------------------------------------------------------
    # Internal helpers

    def _make_prompt(self) -> str:
        if self.state.workspace:
            label = Path(self.state.workspace).name or self.state.workspace
            return f"basaltwater[{label}]> "
        return "basaltwater> "

    def _run_init_file(self, init_file: Optional[Path] = None) -> None:
        path = init_file if init_file is not None else _INIT_FILE
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            self._output(f"Warning: could not read {path}: {exc}")
            return
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                self.dispatch(stripped)
            except ValueError as exc:
                self._output(f"Init file error ({stripped!r}): {exc}")

    def _load_readline_history(self) -> None:
        if not _READLINE_AVAILABLE or self._input is not input:
            return
        try:
            _readline.read_history_file(_HISTORY_FILE)
        except (FileNotFoundError, OSError):
            pass
        _readline.set_history_length(_HISTORY_MAX_LINES)

    def _save_readline_history(self) -> None:
        if not _READLINE_AVAILABLE or self._input is not input:
            return
        try:
            _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _readline.write_history_file(_HISTORY_FILE)
        except OSError:
            pass

    def _handlers(self) -> dict[str, Callable[[list[str]], None]]:
        return {
            "help": self._cmd_help,
            "list": self._cmd_list,
            "ls": self._cmd_list,
            "info": self._cmd_info,
            "cmd": self._cmd_command,
            "command": self._cmd_command,
            "new": self._cmd_new,
            "setup": self._cmd_new,
            "rename": self._cmd_rename,
            "clone": self._cmd_clone,
            "tag": self._cmd_tag,
            "untag": self._cmd_untag,
            "deploy": self._cmd_deploy,
            "rm": self._cmd_remove,
            "remove": self._cmd_remove,
            "recall": self._cmd_recall,
            "reconstruct": self._cmd_reconstruct,
            "proxmox": self._cmd_proxmox,
            "workspace": self._cmd_workspace,
        }

    @staticmethod
    def _split_yes_flag(args: list[str]) -> tuple[list[str], bool]:
        yes = any(a in {"-y", "--yes"} for a in args)
        rest = [a for a in args if a not in {"-y", "--yes"}]
        return rest, yes

    def _confirm(self, prompt: str) -> bool:
        try:
            response = self._input(prompt)
        except (EOFError, KeyboardInterrupt):
            self._output("")
            return False
        return response.strip().lower() in {"y", "yes"}

    def _prompt_text(
        self,
        prompt: str,
        *,
        default: Optional[str] = None,
        allow_blank: bool = False,
    ) -> str:
        while True:
            suffix = f" [{default}]" if default else ""
            try:
                response = self._input(f"{prompt}{suffix}: ")
            except (EOFError, KeyboardInterrupt):
                raise ValueError("Prompt cancelled")
            value = response.strip()
            if value:
                return value
            if default is not None:
                return default
            if allow_blank:
                return ""
            self._output("Please enter a value.")

    def _prompt_yes_no(self, prompt: str, *, default: bool = False) -> bool:
        default_text = "Y/n" if default else "y/N"
        while True:
            try:
                response = self._input(f"{prompt} [{default_text}]: ")
            except (EOFError, KeyboardInterrupt):
                raise ValueError("Prompt cancelled")
            value = response.strip().lower()
            if not value:
                return default
            if value in {"y", "yes"}:
                return True
            if value in {"n", "no"}:
                return False
            self._output("Please answer yes or no.")

    def _prompt_choice(
        self,
        prompt: str,
        options: list[tuple[str, str]],
        *,
        default: Optional[str] = None,
        allow_skip: bool = False,
    ) -> Optional[str]:
        option_map = {value: label for value, label in options}
        while True:
            self._output(prompt + ":")
            for index, (value, label) in enumerate(options, start=1):
                marker = " (default)" if default == value else ""
                self._output(f"  {index}. {value} — {label}{marker}")
            if allow_skip:
                self._output("  Enter to skip")
            try:
                response = self._input("> ")
            except (EOFError, KeyboardInterrupt):
                raise ValueError("Prompt cancelled")
            value = response.strip()
            if not value:
                if default is not None:
                    return default
                if allow_skip:
                    return None
            if value.isdigit():
                choice = int(value)
                if 1 <= choice <= len(options):
                    return options[choice - 1][0]
            if value in option_map:
                return value
            self._output("Please choose one of the numbered options.")

    @staticmethod
    def _root_storage_amount(template: Optional["SetupConfig"]) -> Optional[str]:
        if not template or not template.container_storage:
            return None
        for spec in template.container_storage:
            if spec and spec[0] == "root" and len(spec) >= 2:
                return spec[-1]
        return None

    def _choose_template_config(self) -> Optional["SetupConfig"]:
        from lib.cache import load_all_setup_commands

        configs = load_all_setup_commands()
        if not configs:
            return None

        self._output("Optional: choose an existing saved setup as a starting point.")
        for index, config in enumerate(configs, start=1):
            label = config.friendly_name or config.host
            self._output(
                f"  {index}. {label} — {config.system_type} / {config.machine_type}"
            )
        choice = self._prompt_text(
            "Template setup number (blank for none)",
            allow_blank=True,
        )
        if not choice:
            return None
        if not choice.isdigit():
            raise ValueError("Template selection must be a number")
        index = int(choice)
        if index < 1 or index > len(configs):
            raise ValueError(f"Template selection must be between 1 and {len(configs)}")
        return configs[index - 1]

    def _choose_proxmox_host(self) -> Optional["ProxmoxHost"]:
        from lib.proxmox_hosts import load_proxmox_hosts

        hosts = load_proxmox_hosts(self.state.workspace)
        if not hosts:
            return None

        self._output("Optional: select a registered Proxmox host for hosted provisioning.")
        for index, host in enumerate(hosts, start=1):
            self._output(f"  {index}. {host.name} — {host.address}")
        choice = self._prompt_text(
            "Proxmox host number (blank for none)",
            allow_blank=True,
        )
        if not choice:
            return None
        if not choice.isdigit():
            raise ValueError("Proxmox host selection must be a number")
        index = int(choice)
        if index < 1 or index > len(hosts):
            raise ValueError(
                f"Proxmox host selection must be between 1 and {len(hosts)}"
            )
        return hosts[index - 1]

    # ------------------------------------------------------------------
    # Commands

    def _cmd_help(self, args: list[str]) -> None:
        self._output(HELP_TEXT)

    def _cmd_workspace(self, args: list[str]) -> None:
        if not args:
            self._output(
                f"Workspace: {self.state.workspace or '(default)'}"
            )
            return
        if len(args) != 1:
            raise ValueError("Usage: workspace [path]")
        from lib.workspace import set_workspace_dir
        from lib.validation import validate_workspace_dir
        try:
            validate_workspace_dir(args[0])
        except ValueError as exc:
            raise ValueError(str(exc))
        set_workspace_dir(args[0])
        self.state.workspace = args[0]
        self._output(f"Workspace set to {args[0]}")

    def _cmd_list(self, args: list[str]) -> None:
        from basaltwater import list_configurations
        json_output = "--json" in args
        rest = [a for a in args if a != "--json"]
        pattern = rest[0] if rest else None
        list_configurations(pattern, json_output=json_output)

    def _cmd_info(self, args: list[str]) -> None:
        from basaltwater import show_info
        compact = "--compact" in args
        rest = [a for a in args if a != "--compact"]
        pattern = rest[0] if rest else None
        show_info(pattern, compact=compact)

    def _cmd_command(self, args: list[str]) -> None:
        from basaltwater import show_command
        pattern = args[0] if args else None
        show_command(pattern)

    def _cmd_new(self, args: list[str]) -> None:
        if args:
            raise ValueError("Usage: new")

        from lib.cache import save_setup_command
        from lib.config import SetupConfig
        from lib.plugin_registry import get_system_type_definition
        from lib.system_utils import get_current_username
        from lib.validation import validate_memory_string
        from lib.validators import validate_host, validate_username

        template = self._choose_template_config()
        proxmox_host = self._choose_proxmox_host()

        friendly_name = self._prompt_text("Name for this setup")

        if proxmox_host is not None:
            try:
                from lib.proxmox_network import suggest_free_ips
                suggestions = suggest_free_ips(proxmox_host, count=5)
                if suggestions:
                    self._output(
                        "  Suggested free IPs: " + "  ".join(suggestions)
                    )
            except Exception:
                pass  # suggestions are best-effort; never block on failure

        host = self._prompt_text("IP address or hostname")
        if not validate_host(host):
            raise ValueError(f"Invalid IP address or hostname: {host}")

        machine_options = [
            ("vm", "Proxmox or other virtual machine"),
            ("unprivileged", "Unprivileged LXC"),
            ("privileged", "Privileged LXC"),
            ("auto", "Detect the target machine type"),
        ]
        if proxmox_host is None:
            machine_options.insert(0, ("hardware", "Existing physical or already-provisioned host"))
        default_machine = (
            template.machine_type
            if template and any(option[0] == template.machine_type for option in machine_options)
            else ("vm" if proxmox_host is not None else "auto")
        )
        machine_type = self._prompt_choice(
            "Machine type",
            machine_options,
            default=default_machine,
        )
        assert machine_type is not None

        common_system_types = [
            "workstation_dev",
            "workstation_desktop",
            "server_web",
            "server_lite",
            "control_plane",
        ]
        system_options = [
            (
                system_type,
                get_system_type_definition(system_type).description,
            )
            for system_type in common_system_types
        ]
        default_system_type = (
            template.system_type
            if template and template.system_type in common_system_types
            else "server_lite"
        )
        system_type = self._prompt_choice(
            "System type",
            system_options,
            default=default_system_type,
        )
        assert system_type is not None

        username_default = template.username if template else get_current_username()
        username = self._prompt_text("Username", default=username_default)
        if not validate_username(username):
            raise ValueError(f"Invalid username: {username}")

        tags_default = ",".join(template.tags) if template and template.tags else None
        tags_text = self._prompt_text(
            "Tags (comma-separated, optional)",
            default=tags_default,
            allow_blank=True,
        )
        tags = [tag.strip() for tag in tags_text.split(",") if tag.strip()] or None

        config = SetupConfig(
            host=host,
            username=username,
            system_type=system_type,
            machine_type=machine_type,
            friendly_name=friendly_name,
            tags=tags,
        )

        if system_type == "control_plane":
            config.include_cli_tools = True
            config.include_control_plane_tools = True

        if system_type in {"workstation_dev", "workstation_desktop"}:
            desktop_default = template.desktop if template else "xfce"
            desktop = self._prompt_choice(
                "Desktop environment",
                [
                    ("xfce", "XFCE"),
                    ("i3", "i3"),
                    ("cinnamon", "Cinnamon"),
                    ("lxqt", "LXQt"),
                ],
                default=desktop_default,
            )
            assert desktop is not None
            config.desktop = desktop
            config.enable_rdp = self._prompt_yes_no(
                "Enable RDP/XRDP",
                default=template.enable_rdp if template else False,
            )
            if system_type == "workstation_dev":
                install_dev_tools = self._prompt_yes_no(
                    "Install common dev tools (Node, Go, Python)",
                    default=(
                        template.install_node
                        and template.install_go
                        and template.install_python
                    )
                    if template
                    else True,
                )
                if install_dev_tools:
                    config.install_node = True
                    config.install_go = True
                    config.install_python = True
                config.install_data_analysis_tools = self._prompt_yes_no(
                    "Install data analysis tools (Jupyter, NumPy, pandas, SciPy)",
                    default=(
                        bool(template.install_data_analysis_tools)
                        if template
                        else False
                    ),
                )
                if config.install_data_analysis_tools:
                    config.install_python = True

        if system_type == "server_web":
            config.install_node = self._prompt_yes_no(
                "Install Node.js",
                default=template.install_node if template else True,
            )
            config.enable_ssl = self._prompt_yes_no(
                "Enable SSL",
                default=template.enable_ssl if template else False,
            )
            if config.enable_ssl:
                config.ssl_email = self._prompt_text(
                    "SSL email",
                    default=template.ssl_email if template else None,
                )

        if proxmox_host is not None:
            config.hosted_node = proxmox_host.name
            config.hosted_user = proxmox_host.user
            memory_default = (
                template.container_memory
                if template and template.container_memory
                else ("8G" if system_type.startswith("workstation") else "4G")
            )
            config.container_memory = self._prompt_text(
                "Hosted guest memory",
                default=memory_default,
            )
            validate_memory_string(config.container_memory, "--memory")

            cores_default = str(
                template.container_cores
                if template and template.container_cores
                else (4 if system_type.startswith("workstation") else 2)
            )
            cores_text = self._prompt_text("Hosted guest CPU cores", default=cores_default)
            try:
                config.container_cores = int(cores_text)
            except ValueError as exc:
                raise ValueError(f"Hosted guest CPU cores must be an integer, got {cores_text!r}") from exc
            if config.container_cores < 1:
                raise ValueError("Hosted guest CPU cores must be at least 1")

            disk_default = self._root_storage_amount(template) or (
                "40G" if machine_type == "vm" and system_type.startswith("workstation")
                else "20G" if machine_type == "vm"
                else "10G"
            )
            disk_size = self._prompt_text("Hosted root disk size", default=disk_default)
            validate_memory_string(disk_size, "--storage AMOUNT")
            config.container_storage = [["root", disk_size]]
            if machine_type in {"unprivileged", "privileged"}:
                config.container_storage.append(["template"])

            base_default = (
                template.container_base
                if template and template.container_base
                else "debian"
            )
            base = self._prompt_choice(
                "Base OS",
                [("debian", "Debian"), ("ubuntu", "Ubuntu")],
                default=base_default,
            )
            assert base is not None
            config.container_base = base

        save_setup_command(config, operation="setup")
        self._output(
            f"Saved setup for {config.friendly_name or config.host} ({config.host})."
        )
        self._output("Command:")
        self._output("  " + " ".join(config.to_setup_command()))

        if self._prompt_yes_no("Deploy now?", default=False):
            from basaltwater import deploy_configurations
            deploy_configurations(config.host, force=True)

    def _cmd_rename(self, args: list[str]) -> None:
        if len(args) != 2:
            raise ValueError("Usage: rename <pattern> <new-name>")
        pattern, new_name = args[0], args[1].strip()
        if not new_name:
            raise ValueError("New name must not be blank")
        from lib.cache import load_setup_command, save_setup_command
        config = load_setup_command(pattern)
        if config is None:
            raise ValueError(f"No saved setup found matching '{pattern}'")
        old_label = config.friendly_name or config.host
        config.friendly_name = new_name
        save_setup_command(config, operation="setup")
        self._output(f"Renamed '{old_label}' to '{new_name}'.")

    def _cmd_clone(self, args: list[str]) -> None:
        if len(args) < 2 or len(args) > 3:
            raise ValueError("Usage: clone <pattern> <new-host> [new-name]")
        pattern, new_host = args[0], args[1].strip()
        new_name = args[2].strip() if len(args) == 3 else None
        if not new_host:
            raise ValueError("New host must not be blank")
        from lib.cache import load_setup_command, save_setup_command
        from lib.validators import validate_host
        if not validate_host(new_host):
            raise ValueError(f"Invalid IP address or hostname: {new_host}")
        config = load_setup_command(pattern)
        if config is None:
            raise ValueError(f"No saved setup found matching '{pattern}'")
        config.host = new_host
        if new_name:
            config.friendly_name = new_name
        save_setup_command(config, operation="setup")
        label = config.friendly_name or new_host
        self._output(f"Cloned to '{label}' ({new_host}).")

    def _cmd_tag(self, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("Usage: tag <pattern> <tag> [tag ...]")
        pattern, new_tags = args[0], args[1:]
        from lib.cache import load_setup_command, save_setup_command
        config = load_setup_command(pattern)
        if config is None:
            raise ValueError(f"No saved setup found matching '{pattern}'")
        existing = list(config.tags or [])
        added = [t for t in new_tags if t not in existing]
        config.tags = existing + added
        save_setup_command(config, operation="setup")
        label = config.friendly_name or config.host
        self._output(
            f"Tags for '{label}': {', '.join(config.tags)}"
            if config.tags else f"No tags on '{label}'."
        )

    def _cmd_untag(self, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("Usage: untag <pattern> <tag> [tag ...]")
        pattern, remove_tags = args[0], set(args[1:])
        from lib.cache import load_setup_command, save_setup_command
        config = load_setup_command(pattern)
        if config is None:
            raise ValueError(f"No saved setup found matching '{pattern}'")
        config.tags = [t for t in (config.tags or []) if t not in remove_tags]
        save_setup_command(config, operation="setup")
        label = config.friendly_name or config.host
        self._output(
            f"Tags for '{label}': {', '.join(config.tags)}"
            if config.tags else f"No tags remaining on '{label}'."
        )

    def _cmd_deploy(self, args: list[str]) -> None:
        from basaltwater import parse_deploy_command_args, run_deploy_command

        run_deploy_command(parse_deploy_command_args(args))

    def _cmd_remove(self, args: list[str]) -> None:
        rest, yes = self._split_yes_flag(args)
        if len(rest) != 1:
            raise ValueError("Usage: rm <pattern> [--yes]")
        from basaltwater import remove_configurations
        remove_configurations(rest[0], yes)

    def _cmd_recall(self, args: list[str]) -> None:
        if len(args) < 1 or len(args) > 2:
            raise ValueError("Usage: recall <host> [username]")
        from lib.recall import run_recall_command
        from lib.system_utils import get_current_username
        from lib.validators import validate_host, validate_username
        host = args[0]
        username = args[1] if len(args) == 2 else get_current_username()
        if not validate_host(host):
            raise ValueError(f"Invalid IP address or hostname: {host}")
        if not validate_username(username):
            raise ValueError(f"Invalid username: {username}")
        run_recall_command(host, username, None)

    def _cmd_reconstruct(self, args: list[str]) -> None:
        compact = "--compact" in args or "-c" in args
        from lib.reconstruct import run_reconstruct_command
        run_reconstruct_command(compact)

    def _cmd_proxmox(self, args: list[str]) -> None:
        if args:
            raise ValueError("Usage: proxmox (drops into the proxmox shell)")
        from lib.proxmox_shell import run_proxmox_shell
        run_proxmox_shell(self.state.workspace)


def run_interactive_shell(workspace: Optional[str] = None) -> int:
    """Entry point used by the CLI ``shell`` subcommand."""
    shell = InteractiveShell(workspace=workspace)
    return shell.run()


__all__ = ["InteractiveShell", "ShellState", "run_interactive_shell"]
