"""Saved client discovery and explicit observation-guarded reconciliation."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from lib.headscale_registry import HeadscaleServer, resolve_server
from lib.headscale_config import validate_headscale_url

if TYPE_CHECKING:
    from lib.config import SetupConfig
    from lib.headscale_transport import SSHBinding


def resolve_cli_selection(args: argparse.Namespace, cached: SetupConfig | None = None) -> None:
    """Resolve only an explicit named input before constructing a patch.

    Omission and opt-out must reach the cache merge intact. Direct URLs also
    reach that merge intact so its migration guard can inspect the real input.
    """
    reference = getattr(args, "tailscale_server", None)
    if reference is None:
        return
    selected = resolve_client_server(
        reference,
        server_id=cached.tailscale_server_id if cached else None,
        endpoint=cached.tailscale_login_server if cached else None,
        reference_is_explicit=True,
        workspace=getattr(args, "workspace", None),
    )
    args.tailscale_server = selected.name
    args.tailscale_server_id = selected.id
    args.tailscale_login_server = selected.url


def validate_final_selection(config: SetupConfig, *, workspace: str | None = None) -> None:
    """Validate complete merged settings without locks, state or native calls."""
    from lib.headscale_config import validate_headscale_settings
    from common.headscale_preflight import validate_ingress

    if config.tailscale is not False and (config.tailscale_server_id or config.tailscale_server):
        selected = resolve_client_server(
            config.tailscale_server, server_id=config.tailscale_server_id,
            endpoint=config.tailscale_login_server, workspace=workspace,
        )
        config.tailscale_server = selected.name
        config.tailscale_server_id = selected.id
        config.tailscale_login_server = selected.url
    validate_headscale_settings(config, complete=True)
    validate_ingress(config)


def resolve_client_server(
    reference: str | None,
    *,
    server_id: str | None = None,
    endpoint: str | None = None,
    reference_is_explicit: bool = False,
    workspace: str | None = None,
) -> HeadscaleServer:
    """Resolve discovery without changing an existing client's network identity.

    Saved selections follow their stable record ID after a rename. A newly
    supplied name is checked against that binding, and every saved endpoint is
    compared before returning configuration for remote transport. Neither a
    changed record nor a different selection authorizes client migration.
    """
    if server_id is not None and endpoint is None:
        raise ValueError("Saved Headscale client selection is missing its endpoint")
    if endpoint is not None:
        endpoint = validate_headscale_url(endpoint)
    if server_id is not None:
        selected = resolve_server(server_id, workspace)
        if reference_is_explicit:
            if reference is None or resolve_server(reference, workspace).id != selected.id:
                raise ValueError("Client network change requires explicit leave or migration")
    elif reference is not None:
        selected = resolve_server(reference, workspace)
    else:
        raise ValueError("A named Headscale server reference is required")
    if endpoint is not None and endpoint != selected.url:
        raise ValueError("Headscale endpoint changed; reconcile the client before migration")
    return selected


def reconcile_saved_selection(
    host: str, *, expected_server_id: str, expected_url: str, server: str,
    new_url: str, node_id: str, tags: list[str], target: SSHBinding,
    workspace: str | None = None, dry_run: bool = False,
) -> None:
    """CAS a saved binding after read-only observation of an owned native client.

    Expected old values are operator inputs, not resolved through the potentially
    edited registry. No native enrollment or authority is inferred from a record.
    """
    from copy import deepcopy
    import os
    import re
    import stat
    import uuid

    from lib.atomic_io import write_json_atomic
    from lib.cache import _cache_path_for_host, _cache_record, _load_cache_file
    from lib.concurrency import resource_lock
    from lib.headscale_admin import approved_tags
    from lib.headscale_client import _identity
    from lib.headscale_registry import _check_private_dir
    from lib.headscale_transport import SSHBinding, exchange
    from lib.setup_directories import check_directory_path
    from lib.validators import validate_host
    from lib.workspace import normalize_workspace_dir

    if not isinstance(host, str) or not validate_host(host) or not isinstance(target, SSHBinding):
        raise ValueError("Invalid selected setup host or explicit SSH binding")
    normalized = host.lower().rstrip(".")
    if target.host.lower().rstrip(".") != normalized:
        raise ValueError("SSH host differs from selected cached host")
    if str(uuid.UUID(expected_server_id)) != expected_server_id:
        raise ValueError("Expected canonical old stable ID")
    if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", node_id):
        raise ValueError("Invalid expected native node ID")
    old_url = validate_headscale_url(expected_url)
    endpoint = validate_headscale_url(new_url)
    if old_url != expected_url or endpoint != new_url:
        raise ValueError("Supply exact canonical endpoints")
    if type(tags) is not list:
        raise ValueError("Expected explicit tag list")
    checked_tags = approved_tags(tags) if tags else []
    root = normalize_workspace_dir(workspace)
    path = _cache_path_for_host(host, root)

    def read() -> tuple[dict, HeadscaleServer, dict]:
        check_directory_path(os.path.dirname(path))
        _check_private_dir(root)
        _check_private_dir(os.path.dirname(path))
        info = os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError("Unsafe saved setup cache")
        record = _cache_record(path)
        config = _load_cache_file(path, host)
        if record is None or config is None or record["host"].lower().rstrip(".") != normalized:
            raise ValueError("Saved setup host differs")
        args = record["args"]
        saved_host = args.get("host", record["host"])
        if (not isinstance(saved_host, str) or saved_host.lower().rstrip(".") != normalized
                or args.get("tailscale_server_id") != expected_server_id
                or args.get("tailscale_login_server") != old_url):
            raise ValueError("Saved binding differs from exact expected old binding")
        selected = resolve_server(server, root)
        if selected.url != endpoint:
            raise ValueError("New registry endpoint differs")
        candidate = deepcopy(record)
        candidate["args"].update(tailscale_server_id=selected.id, tailscale_login_server=endpoint)
        # Preserve omission and opt-out exactly; disabled selections have no name.
        candidate["args"]["tailscale_server"] = None if args.get("tailscale") is False else selected.name
        from lib.config import SetupConfig
        validated = SetupConfig.from_dict(host, record["system_type"], candidate["args"])
        validate_final_selection(validated, workspace=root)
        return record, selected, candidate

    original, selected, candidate = read()
    if dry_run:
        return
    lock_target = "local" if host in {"localhost", "127.0.0.1", "::1"} else normalized
    with resource_lock("setup-target", lock_target):
        with resource_lock("setup-cache", path, wait=True):
            if read() != (original, selected, candidate):
                raise ValueError("Saved selection changed before observation")
        observed = exchange(target, "client", {"action": "observe", "url": endpoint, "tags": checked_tags})
        if set(observed) != {"phase", "identity"} or observed["phase"] != "enrolled":
            raise ValueError("Owned enrolled native observation required")
        value = observed["identity"]
        if (type(value) is not dict
                or set(value) != {"node_id", "addresses", "login_server", "tags", "online", "hostname"}
                or value["login_server"] != endpoint or value["tags"] is None):
            raise ValueError("Invalid native identity observation")
        identity = _identity({"BackendState": "Running", "Self": {
            "ID": value["node_id"], "TailscaleIPs": value["addresses"], "Tags": value["tags"],
            "Online": value["online"], "HostName": value["hostname"],
        }}, {"ControlURL": endpoint}, endpoint, True)
        if identity.node_id != node_id or sorted(identity.tags) != checked_tags:
            raise ValueError("Observed node differs from exact expected new identity")
        with resource_lock("setup-cache", path, wait=True):
            # Registry mutations use their own lock; hold it through publication.
            with resource_lock("headscale-registry", root):
                if read() != (original, selected, candidate):
                    raise ValueError("Saved selection changed during observation")
                write_json_atomic(path, candidate, mode=0o600)
