"""Setup-led native enrollment; discovery alone never grants issuance authority."""

from __future__ import annotations

from collections.abc import Callable
import os
import uuid

from lib.config import SetupConfig
from lib.headscale_config import validate_headscale_settings
from lib.headscale_management import bind_management, enroll_machine, inspect_enrollment, resolve_management
from lib.headscale_registry import HeadscaleServer, list_servers, mutate_servers
from lib.headscale_transport import SSHBinding, _credential
from lib.ssh_enrollment import is_host_key_enrolled
from lib.ssh_utils import strict_ssh_transport
from lib.workspace import get_known_hosts_path


def automated_setup(config: SetupConfig) -> bool:
    return bool((config.headscale and config.headscale_name)
                or (config.tailscale and config.tailscale_tags))


def target_binding(config: SetupConfig, *, workspace: str | None = None) -> SSHBinding:
    if not config.ssh_key:
        raise ValueError("Automatic Headscale setup requires an explicit root SSH identity file")
    if config.host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Automatic Headscale setup requires a pinned remote target")
    return SSHBinding(config.host, os.path.abspath(os.path.expanduser(config.ssh_key)),
                      get_known_hosts_path(workspace))


def _credentials(binding: SSHBinding, *, require_pin: bool) -> None:
    os.close(_credential(binding.identity_file, private=True))
    if require_pin:
        os.close(_credential(binding.known_hosts_file, private=False))
        if not is_host_key_enrolled(binding.host, port=binding.port, known_hosts_path=binding.known_hosts_file):
            raise ValueError("Explicit SSH host pin is missing; enroll trust before setup")


def _hosting_record(config: SetupConfig, records: list[HeadscaleServer]) -> HeadscaleServer | None:
    own = next((r for r in records if r.id == config.headscale_registration_id), None)
    if any(r.id != config.headscale_registration_id and
           (r.name.casefold() == config.headscale_name.casefold()
            or r.url == "https://" + config.headscale_domain) for r in records):
        raise ValueError("Headscale registration collides with unknown ownership")
    if own and (own.origin != "managed" or own.url != "https://" + config.headscale_domain):
        raise ValueError("Managed Headscale endpoint or ownership changed")
    return own


def preflight_setup(config: SetupConfig, *, workspace: str | None = None) -> None:
    """Local admission before provisioning; never observe native state here."""
    validate_headscale_settings(config, complete=True)
    if not automated_setup(config):
        return
    target = (None if config.dry_run and not config.ssh_key
              else target_binding(config, workspace=workspace))
    journal = inspect_enrollment(workspace=workspace)
    if journal and journal["phase"] not in {"complete", "reconciled"}:
        raise ValueError("Reconcile retained enrollment before provisioning or setup")
    if config.headscale and config.headscale_name:
        desired = {"endpoint": "https://" + config.headscale_domain,
                   "ssh": target.to_dict() if target else None}
        retained = config.headscale_registration_binding
        if retained is not None and (retained["endpoint"] != desired["endpoint"]
                                     or (target and retained["ssh"] != desired["ssh"])):
            raise ValueError("Retained hosting endpoint or SSH authority changed")
        own = _hosting_record(config, list_servers(workspace))
        if own:
            from lib.headscale_management import _bindings

            old = _bindings(workspace).get(own.id)
            if old and (old.endpoint != own.url or (target and old.ssh != target)):
                raise ValueError("Managed Headscale SSH authority changed")
        if not config.dry_run and config.headscale_registration_id is None:
            config.headscale_registration_id = str(uuid.uuid4())
        if not config.dry_run:
            config.headscale_registration_binding = desired
    if config.tailscale and config.tailscale_tags:
        if not config.tailscale_server or not config.tailscale_server_id:
            raise ValueError("Automatic enrollment requires a resolved named saved network")
        authority = resolve_management(config.tailscale_server_id, workspace=workspace)
        if authority.endpoint != config.tailscale_login_server:
            raise ValueError("Management authority endpoint differs from selected network")
        if journal and target and journal["target"] == target.to_dict() and (
                journal["server_id"] != config.tailscale_server_id
                or journal["endpoint"] != config.tailscale_login_server
                or journal["tags"] != config.tailscale_tags):
            raise ValueError("Retained target enrollment differs; explicit migration required")
        if not config.dry_run:
            _credentials(authority.ssh, require_pin=True)
    if not config.dry_run:
        _credentials(target, require_pin=not bool(config.hosted_node))


def print_setup_plan(config: SetupConfig) -> int:
    if config.build_on:
        from lib.build_setup import validate_build_setup_selection

        validate_build_setup_selection(config, require_enrollment=False)
    print("[DRY RUN] Validate local Headscale selection and management authority")
    if config.hosted_node:
        print("  Provision or verify the guest once and enroll its SSH host pin")
    if config.tailscale and config.tailscale_tags:
        print("  Prepare native client only; preserve the desired profile")
        print("  Enroll once through existing management authority; verify native association")
    print("  Apply final desired profile" + (" through the selected builder" if config.build_on else ""))
    if config.headscale and config.headscale_name:
        print("  Register managed server and verify explicit pinned root SSH management binding")
    print("  Complete requested power action and record its outcome")
    return 0


def run_setup(config: SetupConfig, *, apply_setup: Callable[..., int],
              apply_profile: Callable[[SetupConfig], int], workspace: str | None = None) -> int:
    """Run under the caller's setup-target lock; never retry uncertain stages."""
    preflight_setup(config, workspace=workspace)
    if config.dry_run:
        return print_setup_plan(config)
    target = target_binding(config, workspace=workspace)
    _credentials(target, require_pin=True)
    with strict_ssh_transport(target.host, identity_file=target.identity_file,
                              known_hosts_path=target.known_hosts_file, port=target.port):
        if config.tailscale and config.tailscale_tags:
            preparation = SetupConfig(
                host=config.host, username=config.username, system_type=config.system_type,
                machine_type=config.machine_type, ssh_key=config.ssh_key,
                tailscale=True, tailscale_login_server=config.tailscale_login_server,
                access_sources=config.access_sources, ssh_access_sources=config.ssh_access_sources,
                lan_access=config.lan_access,
            )
            result = apply_setup(preparation, client_preparation=True)
            if result:
                return result
            enroll_machine(config.tailscale_server_id, target, config.tailscale_tags, workspace=workspace)
        result = apply_profile(config)
        if result:
            return result
    if config.headscale and config.headscale_name:
        # Network activation may have adopted a different, verified SSH host.
        target = target_binding(config, workspace=workspace)
        _credentials(target, require_pin=True)
        config.headscale_registration_binding = {"endpoint": "https://" + config.headscale_domain,
                                                 "ssh": target.to_dict()}
        def register(records):
            own = _hosting_record(config, records)
            candidate = HeadscaleServer(config.headscale_registration_id, config.headscale_name,
                "https://" + config.headscale_domain, "managed",
                "https://" + config.headscale_onboarding_domain if config.headscale_onboarding_domain else None)
            return [r for r in records if own is None or r.id != own.id] + [candidate], candidate

        selected = mutate_servers(workspace, register)
        bind_management(selected.id, target, workspace=workspace)
    return 0
