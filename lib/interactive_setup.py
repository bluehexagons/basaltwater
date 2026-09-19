"""Interactive choices for agent VM setup."""

from __future__ import annotations

import getpass
import sys
from typing import Any

from lib.config import (
    AGENT_TOOLS,
    BROWSER_AUTOMATION_PROVIDERS,
    GIT_ACCESS_POLICIES,
)
from lib.plugin_registry import get_system_type_definition


def _prompt(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _prompt_choice(prompt: str, choices: tuple[str, ...], default: str) -> str:
    while True:
        value = _prompt(f"{prompt} ({'/'.join(choices)})", default).lower()
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}")


def _prompt_repositories() -> list[str]:
    repositories: list[str] = []
    print("Enter HTTPS repository URLs to clone on the VM; leave blank when finished.")
    while True:
        value = input("Repository URL: ").strip()
        if not value:
            return repositories
        repositories.append(value)


def _prompt_device_pairing_credentials(args: Any) -> None:
    """Collect transient Basic Auth credentials for the enrollment portal."""

    args.device_pairing_auth_username = _prompt(
        "Device-pairing portal username", "agent"
    )
    password = getpass.getpass("Device-pairing portal password (hidden): ")
    confirmation = getpass.getpass("Confirm portal password: ")
    if not password or password != confirmation:
        raise ValueError("Device-pairing portal passwords did not match")
    args.device_pairing_auth_password = password


def prompt_for_missing_passwords(args: Any, system_type: str) -> None:
    """Prompt for passwords implied by a setup profile when they are absent.

    Passwords are never persisted in the reconstructed command. An empty
    device-pairing password reuses the target account password when one was
    supplied; for a local setup, an empty RDP password selects the existing
    account password path.
    """

    if getattr(args, "dry_run", False):
        return

    system_defaults = get_system_type_definition(system_type)
    enable_rdp = getattr(args, "enable_rdp", None)
    if enable_rdp is None:
        enable_rdp = system_defaults.default_enable_rdp

    if enable_rdp and not getattr(args, "rdp_existing_password", False):
        password = getattr(args, "password", None)
        if not isinstance(password, str) or not password.strip():
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError(
                    "--rdp requires --password, or run from a terminal to enter it securely"
                )
            password = getpass.getpass(
                f"Password for {getattr(args, 'username', None) or 'target user'} "
                "(hidden; blank reuses an existing local password): "
            )
            if password:
                args.password = password
            else:
                if getattr(args, "hosted_node", None):
                    raise ValueError(
                        "An existing account password can only be reused by a local "
                        "setup; provisioned guests need a new --password"
                    )
                args.rdp_existing_password = True

    pairing_providers = getattr(args, "device_pairing_providers", None)
    if getattr(args, "disable_device_pairing", False):
        pairing_providers = None
    elif not pairing_providers:
        pairing_providers = system_defaults.default_device_pairing_providers
    pairing_auth_supplied = bool(
        getattr(args, "device_pairing_auth_file", None)
        or getattr(args, "device_pairing_auth_username", None)
        or getattr(args, "device_pairing_auth_password", None)
    )
    if pairing_providers and not pairing_auth_supplied:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError(
                "Device pairing requires credentials, or run from a terminal to enter them securely"
            )
        args.device_pairing_auth_username = _prompt(
            "Device-pairing portal username",
            getattr(args, "username", None) or "agent",
        )
        pairing_password = getpass.getpass(
            "Device-pairing portal password (hidden; blank reuses the account password): "
        )
        if not pairing_password:
            pairing_password = getattr(args, "password", None)
        if not pairing_password:
            raise ValueError(
                "Device-pairing password was empty and no target account password is available"
            )
        args.device_pairing_auth_password = pairing_password


def run_interactive_setup(args: Any) -> None:
    """Fill agent setup choices into a parsed setup namespace.

    Credential values are held only in the namespace for the current setup and
    are removed before configuration persistence. Dry-runs deliberately skip
    all credential prompts and secret staging.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("--interactive requires a TTY")

    if not getattr(args, "agent_tools", None):
        raw_tools = _prompt("Agent tools, comma-separated", "").strip()
        selected = [tool.strip() for tool in raw_tools.split(",") if tool.strip()]
        unknown = [tool for tool in selected if tool not in AGENT_TOOLS]
        if unknown:
            raise ValueError(f"Unsupported agent tool: {', '.join(unknown)}")
        args.agent_tools = list(dict.fromkeys(selected)) or None

    if not getattr(args, "agent_repos", None):
        args.agent_repos = _prompt_repositories() or None

    selected_tools = set(args.agent_tools or [])
    compatible_browser_tools = selected_tools.intersection({"codex", "opencode"})
    if compatible_browser_tools and not getattr(args, "browser_automation", None):
        browser_choice = _prompt_choice(
            "Agent browser automation",
            ("none", *BROWSER_AUTOMATION_PROVIDERS),
            "none",
        )
        args.browser_automation = None if browser_choice == "none" else browser_choice

    if args.agent_repos or "gh" in selected_tools:
        args.git_host = _prompt("Git host", getattr(args, "git_host", "github.com"))
        args.git_access = _prompt_choice(
            "VM Git access",
            GIT_ACCESS_POLICIES,
            getattr(args, "git_access", "none"),
        )

    web_interfaces = set(getattr(args, "web_interfaces", None) or [])
    if "t3code" in web_interfaces and not getattr(
        args, "device_pairing_providers", None
    ):
        pairing_choice = _prompt_choice(
            "Allow new browsers to request T3 Code pairing links",
            ("yes", "no"),
            "yes",
        )
        if pairing_choice == "yes":
            args.device_pairing_providers = ["t3code"]

    if getattr(args, "dry_run", False):
        print("Dry-run: skipping credential prompts and secret staging.")
        return

    if (
        "gh" in selected_tools
        and args.git_host == "github.com"
        and args.git_access != "none"
    ):
        auth_choice = _prompt_choice(
            "GitHub credential source",
            ("none", "active", "file", "token"),
            "none",
        )
        if auth_choice == "active":
            args.git_auth_source = "active"
        elif auth_choice == "file":
            args.git_auth_file = _prompt("GitHub hosts.yml or token file")
        elif auth_choice == "token":
            args.git_auth_token = getpass.getpass("GitHub token (hidden): ").strip()
    elif "gh" in selected_tools and args.git_access == "none":
        print("GitHub credentials skipped because VM Git access is none.")

    non_gh_tools = sorted(selected_tools.difference({"gh"}))
    if non_gh_tools:
        agent_auth_choice = _prompt_choice(
            "Agent authentication (login authorizes Codex on the target)",
            ("login", "none", "files") if "codex" in selected_tools else ("none", "files"),
            "login" if "codex" in selected_tools else "none",
        )
        if agent_auth_choice in {"login", "none"}:
            args.agent_auth_source = agent_auth_choice
        elif agent_auth_choice == "files":
            args.agent_auth_source = None
            args.agent_auth_files = []
            for tool in non_gh_tools:
                path = _prompt(f"{tool} credential file (blank to skip)")
                if path:
                    args.agent_auth_files.append([tool, path])
            if not args.agent_auth_files:
                args.agent_auth_files = None

    if selected_tools:
        if (
            _prompt_choice(
                "Copy optional non-secret agent config", ("no", "active"), "no"
            )
            == "active"
        ):
            args.agent_config_source = "active"

    pairing_providers = set(getattr(args, "device_pairing_providers", None) or [])
    pairing_auth_supplied = bool(
        getattr(args, "device_pairing_auth_file", None)
        or getattr(args, "device_pairing_auth_username", None)
        or getattr(args, "device_pairing_auth_password", None)
    )
    if "t3code" in pairing_providers and not pairing_auth_supplied:
        _prompt_device_pairing_credentials(args)

    print(
        "Interactive agent setup choices recorded; credentials will not be saved "
        "in the setup command."
    )
